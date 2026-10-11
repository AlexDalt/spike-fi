#!/usr/bin/env python3
"""
Fault injection sweep targeting the DIT inner (j) loop entry test in fft().

The old return-edge skip could not suppress the first DIT butterfly: for
len=2 the j loop executes once, so the return edge is reached only after
the damage is done.  This attack instead faults the register used by the
entry guard before "bgeu j, len/2, exit", forcing the guard true and
skipping the entire butterfly loop for that group.

Detection: optimistically assume the entry-guard fault fired for every
group needed by both FFT calls, compute the no-butterfly pipeline output,
and check whether verifier queries observe that predictable faulted value.
"""

import argparse
import os
import re
import random
import subprocess

P = (1 << 61) - 1
UINT64_MAX = (1 << 64)

def _bit_reverse(a):
    n = len(a); out = list(a); j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit; bit >>= 1
        j ^= bit
        if i < j:
            out[i], out[j] = out[j], out[i]
    return out

def _fft_no_butterflies(a):
    """DIT fft() with every inner butterfly loop skipped."""
    return _bit_reverse(list(a))

def _expected_faulted_evals(transcript_field, transcript_size):
    """
    Compute the evaluations under the entry-guard fault model, using only
    public data (Fibonacci constraints + field params).
    """
    n   = transcript_size
    inv_n = pow(n, P - 2, P)

    # Broken IFFT: bit-reversal still runs, but every butterfly is skipped.
    coeffs = _fft_no_butterflies(list(transcript_field))
    coeffs = [(x * inv_n) % P for x in coeffs]

    # Broken forward FFT on zero-padded coefficients, again with no butterflies.
    return _fft_no_butterflies(coeffs + [0] * n)

def try_extract(faulted_evals, transcript_size, expected_faulted, baseline_set, num_queries):
    """
    Simulate a malicious verifier who opens NUM_QUERIES random positions.
    The verifier knows the fault model and the public Fibonacci constraints,
    so they can predict the faulted evaluation at each position.

    A queried position confirms the attack if the actual faulted value
    matches the predicted faulted value AND differs from the honest baseline.

    Returns True if at least one queried position confirms extraction.
    """
    ext = 2 * transcript_size
    nq  = min(num_queries, ext)
    queries = random.sample(range(ext), nq)

    for q in queries:
        if faulted_evals[q] == expected_faulted[q] and faulted_evals[q] not in baseline_set:
            return True
    return False

# ── Sweep infrastructure ─────────────────────────────────────────────────────

EXAMPLE_DIR  = os.path.dirname(os.path.abspath(__file__))
ENTRY_GUARD_PC = 0x11954   # bgeu a5,s7,exit: if j >= len/2, skip the group
REG_J          = 15        # a5/x15 holds volatile j at the entry guard

def fibonacci(n):
    seq = [1, 1]
    for i in range(2, n):
        seq.append((seq[-1] + seq[-2]) % UINT64_MAX)
    return seq

def make(*args):
    return subprocess.run(
        ["make", *args],
        cwd=EXAMPLE_DIR,
        capture_output=True,
        text=True,
    )

def build():
    make("clean").check_returncode()
    make("build").check_returncode()

def parse_output(text):
    root        = re.search(r"merkle root: ([0-9a-f]+)", text)
    evals_block = re.search(r"evals:\n(.*?)(?:\n\n|\Z)", text, re.DOTALL)
    evals       = re.findall(r"\[\d+\] = (\d+)", evals_block.group(1)) if evals_block else []
    return (
        root.group(1) if root else None,
        [int(v) for v in evals],
    )

def run_clean(transcript_size):
    r = make("run", f"TRANSCRIPT_SIZE={transcript_size}")
    return parse_output(r.stdout + r.stderr)

def run_fi(prob, transcript_size):
    spec = f"pc:{ENTRY_GUARD_PC:#x}:{prob:.1f}:r:{REG_J}:1:FFFFFFFF"
    r    = make("run", f"FI=--fi-enable --fi-debug --fi-spec={spec}",
                f"TRANSCRIPT_SIZE={transcript_size}")
    text = r.stdout + r.stderr
    root, evals = parse_output(text)
    crashed  = root is None
    triggers = len(re.findall(r"trigger=1", text))
    return root, evals, triggers, crashed

def parse_args():
    ap = argparse.ArgumentParser(description="DIT entry-guard FI sweep")
    ap.add_argument("-n", "--sizes", type=int, nargs="+",
                    default=[8, 16, 32, 64, 128, 256, 512, 1024],
                    help="transcript sizes to test")
    ap.add_argument("-p", "--probs", type=float, nargs="+",
                    default=[round(i * 0.1, 1) for i in range(1, 11)],
                    help="fault probabilities to test")
    ap.add_argument("-q", "--queries", type=int, nargs="+",
                    default=[64],
                    help="number of queries (security parameter); swept as outer loop")
    ap.add_argument("-r", "--runs", type=int, default=30,
                    help="runs per (prob, queries) pair")
    return ap.parse_args()

def main():
    args = parse_args()

    print("  building ... ", end="", flush=True)
    build()
    print("done", flush=True)

    for transcript_size in args.sizes:
        fib = fibonacci(transcript_size)
        transcript_field = [f % P for f in fib]
        expected_faulted = _expected_faulted_evals(transcript_field, transcript_size)

        _, baseline_evals = run_clean(transcript_size)
        baseline_set = set(baseline_evals)

        for num_queries in args.queries:
            print(f"\n{'='*60}", flush=True)
            print(f"TRANSCRIPT_SIZE={transcript_size}  "
                  f"NUM_QUERIES={num_queries}", flush=True)
            print(f"{'='*60}", flush=True)

            print(f"  {'prob':>6}  {'runs':>5}  {'extracted':>10}  "
                  f"{'crashes':>8}  {'avg triggers':>13}", flush=True)
            print(f"  {'-'*50}", flush=True)

            for prob in args.probs:
                extracted      = 0
                crashes        = 0
                total_triggers = 0

                for _ in range(args.runs):
                    root, evals, triggers, crashed = run_fi(prob, transcript_size)
                    total_triggers += triggers

                    if crashed:
                        crashes += 1
                    else:
                        if try_extract(evals, transcript_size, expected_faulted,
                                       baseline_set, num_queries):
                            extracted += 1

                avg_triggers = total_triggers / args.runs
                print(f"  {prob:>6.1f}  {args.runs:>5}  {extracted:>10}  "
                      f"{crashes:>8}  {avg_triggers:>13.1f}", flush=True)

if __name__ == "__main__":
    main()
