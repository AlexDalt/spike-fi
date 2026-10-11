#!/usr/bin/env python3
"""
Fault injection sweep on the Gentleman-Sande (DIF) NTT.

The attack skips the return edge of the inner j-loop.  The final DIF
evaluation FFT is compiled as a separate function so a PC-based return
edge fault can fire repeatedly there without corrupting the earlier IFFT
coefficient generation.  The final DIF mixing stage is assumed to remain
unskipped.  Public openings are output-bit-reversed, so extraction
correlates opened neighboring values before bit-reversal, then subtracts
one from the other and multiplies by 2^-1 to invert that final butterfly
and recover candidate input coefficients.
"""

import argparse
import os
import re
import random
import subprocess

P = (1 << 61) - 1

EXAMPLE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_HOME = os.path.abspath(os.path.join(EXAMPLE_DIR, "..", ".."))
ASM_PATH = os.path.join(REPO_HOME, "build", "zk-stark", "test.asm")

def bit_reverse_index(i, bits):
    r = 0
    for _ in range(bits):
        r = (r << 1) | (i & 1)
        i >>= 1
    return r

def extract_coefficients(faulted_evals, num_queries):
    ext = len(faulted_evals)
    if ext < 2:
        return []

    nq = min(num_queries, ext)
    queries = random.sample(range(ext), nq)
    opened = set(queries)
    bits = ext.bit_length() - 1
    gap = 1
    inv2 = pow(2, P - 2, P)
    extracted = []

    for pre in range(0, ext - gap, 2):
        a = bit_reverse_index(pre, bits)
        b = bit_reverse_index(pre + gap, bits)
        if a not in opened or b not in opened:
            continue
        extracted.append((faulted_evals[a] + P - faulted_evals[b]) * inv2 % P)
        extracted.append((faulted_evals[b] + P - faulted_evals[a]) * inv2 % P)
    return extracted

def try_extract(faulted_evals, input_coeffs, num_queries):
    input_coeffs = set(input_coeffs)
    extracted = extract_coefficients(faulted_evals, num_queries)
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
    make("build", "TEST_SRC=test_dif.c").check_returncode()

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

def discover_eval_return_edge_pc():
    with open(ASM_PATH, "r", encoding="utf-8") as f:
        asm = f.readlines()

    in_fft_eval = False
    for line in asm:
        if re.match(r"^[0-9a-f]+ <fft_eval>:", line):
            in_fft_eval = True
            continue
        if in_fft_eval and re.match(r"^[0-9a-f]+ <[^>]+>:", line):
            break
        if not in_fft_eval:
            continue

        m = re.match(r"\s*([0-9a-f]+):.*\bbltu\s+a5,s11,", line)
        if m:
            return int(m.group(1), 16)

    raise RuntimeError("could not find fft_eval inner-loop return-edge PC")

def run_fi(prob, transcript_size, return_edge_pc):
    spec = f"pc:{return_edge_pc:#x}:{prob:.1f}:s"
    r = make("run", f"FI=--fi-enable --fi-debug --fi-spec={spec}",
             f"TRANSCRIPT_SIZE={transcript_size}")
    text = r.stdout + r.stderr
    root, _, evals = parse_output(text)
    crashed  = root is None
    triggers = len(re.findall(r"trigger=1", text))
    return root, evals, triggers, crashed

def parse_args():
    ap = argparse.ArgumentParser(description="DIF loop-skip FI sweep")
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
    return_edge_pc = discover_eval_return_edge_pc()

    for transcript_size in args.sizes:
        _, input_coeffs, _ = run_clean(transcript_size)

        for num_queries in args.queries:
            print(f"\n{'='*60}", flush=True)
            print(f"TRANSCRIPT_SIZE={transcript_size}  "
                  f"NUM_QUERIES={num_queries}", flush=True)
            print(f"FFT_EVAL_RETURN_EDGE_PC={return_edge_pc:#x}", flush=True)
            print(f"{'='*60}", flush=True)

            print(f"  {'prob':>6}  {'runs':>5}  {'extracted':>10}  "
                  f"{'crashes':>8}  {'avg triggers':>13}", flush=True)
            print(f"  {'-'*50}", flush=True)

            for prob in args.probs:
                extracted      = 0
                crashes        = 0
                total_triggers = 0

                for _ in range(args.runs):
                    root, evals, triggers, crashed = run_fi(
                        prob, transcript_size, return_edge_pc
                    )
                    total_triggers += triggers

                    if crashed:
                        crashes += 1
                    else:
                        if try_extract(evals, input_coeffs, num_queries):
                            extracted += 1

                avg_triggers = total_triggers / args.runs
                print(f"  {prob:>6.1f}  {args.runs:>5}  {extracted:>10}  "
                      f"{crashes:>8}  {avg_triggers:>13.1f}", flush=True)

if __name__ == "__main__":
    main()
