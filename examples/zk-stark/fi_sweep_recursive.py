#!/usr/bin/env python3
"""
Fault injection sweep on the recursive Cooley-Tukey NTT.

The attack skips the first odd recursive call reached in fft().  The
fault target is discovered from the FI_MARK trace and then injected by
absolute instruction step, so the fault is attempted once rather than
every time the recursive-call PC is fetched.

Extraction opens queried output pairs separated by n/2.  For a matching
pair (x, y), the candidate input coefficient is (x - y) * 2^-1.
"""

import argparse
import os
import re
import random
import signal
import subprocess

P = (1 << 61) - 1

EXAMPLE_DIR = os.path.dirname(os.path.abspath(__file__))

# PC of the final top-level fft(evals, 2*n, ...) call in main().
FINAL_FFT_CALL_PC = 0x10286

# PC of "jal fft.part.0" for the odd recursive call after FI_MARK.  These PCs
# are only used to discover the first absolute instruction step from --fi-trace.
ODD_RECURSIVE_CALL_PC = 0x110cc

def extract_coefficients(faulted_evals, transcript_size, num_queries):
    ext = len(faulted_evals)
    if ext < 2:
        return []

    nq  = min(num_queries, ext)
    queries = random.sample(range(ext), nq)
    opened = set(queries)
    gap = ext // 2
    if gap <= 0:
        return []

    root = pow(3, (P - 1) // ext, P)
    inv2 = pow(2, P - 2, P)
    extracted = []

    for q in queries:
        mate = q + gap
        if mate >= ext:
            continue
        if mate not in opened:
            continue
        scaled = (faulted_evals[q] + P - faulted_evals[mate]) * inv2 % P
        inv_twiddle = pow(pow(root, q, P), P - 2, P)
        extracted.append(scaled * inv_twiddle % P)
    return extracted

def try_extract(faulted_evals, transcript_size, input_coeffs, num_queries):
    input_coeffs = set(input_coeffs)
    extracted = extract_coefficients(faulted_evals, transcript_size, num_queries)
    return any(coeff in input_coeffs for coeff in extracted)

# Sweep infrastructure

def make(*args):
    return subprocess.run(
        ["make", *args],
        cwd=EXAMPLE_DIR,
        capture_output=True,
        text=True,
    )

def build():
    make("clean").check_returncode()
    make("build", "TEST_SRC=test_recursive.c").check_returncode()

def parse_output(text):
    root        = re.search(r"merkle root: ([0-9a-f]+)", text)
    coeffs_block = re.search(r"coeffs:\n(.*?)(?:\nmerkle root:|\Z)", text, re.DOTALL)
    evals_block = re.search(r"evals:\n(.*?)(?:\n\n|\Z)", text, re.DOTALL)
    coeffs      = re.findall(r"\[\d+\] = (\d+)", coeffs_block.group(1)) if coeffs_block else []
    evals       = re.findall(r"\[\d+\] = (\d+)", evals_block.group(1)) if evals_block else []
    return (
        root.group(1) if root else None,
        [int(v) for v in coeffs],
        [int(v) for v in evals],
    )

def run_clean(transcript_size):
    r = make("run", f"TRANSCRIPT_SIZE={transcript_size}")
    return parse_output(r.stdout + r.stderr)

def discover_first_recursive_call_step(transcript_size):
    final_call_re = re.compile(
        rf"fi: trace => step = \d+, pc =\s+{FINAL_FFT_CALL_PC:x},"
    )
    odd_call_re = re.compile(
        rf"fi: trace => step = (\d+), pc =\s+{ODD_RECURSIVE_CALL_PC:x},"
    )

    p = subprocess.Popen(
        ["make", "run", "FI=--fi-enable --fi-debug --fi-trace",
         f"TRANSCRIPT_SIZE={transcript_size}"],
        cwd=EXAMPLE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    seen_final_fft = False
    odd_calls_seen = 0
    target_odd_call = transcript_size // 2
    try:
        for line in p.stdout:
            if not seen_final_fft:
                seen_final_fft = bool(final_call_re.search(line))
                continue
            call = odd_call_re.search(line)
            if call:
                odd_calls_seen += 1
                if odd_calls_seen == target_odd_call:
                    os.killpg(p.pid, signal.SIGTERM)
                    p.wait()
                    return int(call.group(1))
        p.wait()
    finally:
        if p.poll() is None:
            os.killpg(p.pid, signal.SIGTERM)
            p.wait()

    raise RuntimeError("could not find final FFT recursive-call step in trace output")

def run_fi(prob, transcript_size, fault_step):
    spec = f"{fault_step}:{prob:.1f}:s"
    r    = make("run", f"FI=--fi-enable --fi-debug --fi-spec={spec}",
                f"TRANSCRIPT_SIZE={transcript_size}")
    text = r.stdout + r.stderr
    root, _, evals = parse_output(text)
    crashed  = root is None
    triggers = len(re.findall(r"trigger=1", text))
    return root, evals, triggers, crashed

def parse_args():
    ap = argparse.ArgumentParser(description="Recursive CT loop-skip FI sweep")
    ap.add_argument("-n", "--sizes", type=int, nargs="+",
                    default=[8, 16, 32, 64, 128, 256, 512, 1024])
    ap.add_argument("-p", "--probs", type=float, nargs="+",
                    default=[round(i * 0.1, 1) for i in range(1, 11)])
    ap.add_argument("-q", "--queries", type=int, nargs="+", default=[64])
    ap.add_argument("-r", "--runs", type=int, default=30)
    return ap.parse_args()

def main():
    args = parse_args()

    print("  building ... ", end="", flush=True)
    build()
    print("done", flush=True)

    for transcript_size in args.sizes:
        fault_step = discover_first_recursive_call_step(transcript_size)
        _, input_coeffs, _ = run_clean(transcript_size)

        for num_queries in args.queries:
            print(f"\n{'='*60}", flush=True)
            print(f"TRANSCRIPT_SIZE={transcript_size}  "
                  f"NUM_QUERIES={num_queries}  "
                  f"FAULT_STEP={fault_step}", flush=True)
            print(f"{'='*60}", flush=True)

            print(f"  {'prob':>6}  {'runs':>5}  {'extracted':>10}  "
                  f"{'crashes':>8}  {'avg triggers':>13}", flush=True)
            print(f"  {'-'*50}", flush=True)

            for prob in args.probs:
                extracted      = 0
                crashes        = 0
                total_triggers = 0

                for _ in range(args.runs):
                    root, evals, triggers, crashed = run_fi(prob, transcript_size, fault_step)
                    total_triggers += triggers

                    if crashed:
                        crashes += 1
                    else:
                        if try_extract(evals, transcript_size, input_coeffs, num_queries):
                            extracted += 1

                avg_triggers = total_triggers / args.runs
                print(f"  {prob:>6.1f}  {args.runs:>5}  {extracted:>10}  "
                      f"{crashes:>8}  {avg_triggers:>13.1f}", flush=True)

if __name__ == "__main__":
    main()
