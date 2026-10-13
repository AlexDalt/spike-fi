#!/usr/bin/env python3
"""Fault-injection sweep for the polynomial-masking call-skip attack."""

import argparse
import os
import random
import re
import signal
import subprocess


EXAMPLE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_HOME = os.path.abspath(os.path.join(EXAMPLE_DIR, "..", ".."))
ASM_PATH = os.path.join(REPO_HOME, "build", "zk-stark", "test.asm")


def prepare(transcript_size):
    """Resolve the fault step and the extraction reference for one size."""
    call_pc = discover_add_mask_call_pc()
    fault_step = discover_add_mask_call_step(transcript_size, call_pc)
    _, unmasked_evals, masked_evals = run_clean(transcript_size)
    return fault_step, (unmasked_evals, masked_evals)


def try_extract(faulted_evals, reference, num_queries):
    """One attempt: did any opening come back unmasked?"""
    unmasked_evals, masked_evals = reference
    ext = len(faulted_evals)
    queries = random.sample(range(ext), min(num_queries, ext))
    return any(
        masked_evals[index] != unmasked_evals[index]
        and faulted_evals[index] == unmasked_evals[index]
        for index in queries
    )


def make(*args):
    return subprocess.run(
        ["make", *args],
        cwd=EXAMPLE_DIR,
        capture_output=True,
        text=True,
    )


def build(source=None):
    make("clean").check_returncode()
    make("build", "TEST_SRC=test_masking.c").check_returncode()


def parse_output(text):
    root = re.search(r"merkle root: ([0-9a-f]+)", text)
    unmasked_block = re.search(
        r"^unmasked evals:\n(.*?)(?:\nmerkle root:|\Z)",
        text,
        re.DOTALL | re.MULTILINE,
    )
    evals_block = re.search(
        r"^evals:\n(.*?)(?:\n\n|\Z)",
        text,
        re.DOTALL | re.MULTILINE,
    )
    unmasked = (
        re.findall(r"\[\d+\] = (\d+)", unmasked_block.group(1))
        if unmasked_block
        else []
    )
    evals = (
        re.findall(r"\[\d+\] = (\d+)", evals_block.group(1))
        if evals_block
        else []
    )
    return (
        root.group(1) if root else None,
        [int(value) for value in unmasked],
        [int(value) for value in evals],
    )


def run_clean(transcript_size):
    result = make("run", f"TRANSCRIPT_SIZE={transcript_size}")
    return parse_output(result.stdout + result.stderr)


def discover_add_mask_call_pc():
    with open(ASM_PATH, "r", encoding="utf-8") as asm_file:
        asm = asm_file.readlines()

    in_main = False
    seen_fault_mark = False
    for line in asm:
        if re.match(r"^[0-9a-f]+ <main>:", line):
            in_main = True
            continue
        if in_main and re.match(r"^[0-9a-f]+ <[^>]+>:", line):
            break
        if not in_main:
            continue

        if re.search(r"\bslti\s+zero,zero,2\b", line):
            seen_fault_mark = True
            continue

        if seen_fault_mark:
            call = re.match(
                r"\s*([0-9a-f]+):.*\bjal\b.*<add_mask(?:\.[^>]*)?>",
                line,
            )
            if call:
                return int(call.group(1), 16)

    raise RuntimeError("could not find marked add_mask call PC")


def discover_add_mask_call_step(transcript_size, call_pc):
    call_re = re.compile(rf"fi: trace => step = (\d+), pc =\s+{call_pc:x},")
    process = subprocess.Popen(
        [
            "make",
            "run",
            "FI=--fi-enable --fi-debug --fi-trace",
            f"TRANSCRIPT_SIZE={transcript_size}",
        ],
        cwd=EXAMPLE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )

    try:
        for line in process.stdout:
            call = call_re.search(line)
            if call:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait()
                return int(call.group(1))
        process.wait()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait()

    raise RuntimeError("could not find add_mask call step")


def run_fi(probability, transcript_size, fault_step):
    spec = f"{fault_step}:{probability:.1f}:s"
    result = make(
        "run",
        f"FI=--fi-enable --fi-debug --fi-spec={spec}",
        f"TRANSCRIPT_SIZE={transcript_size}",
    )
    text = result.stdout + result.stderr
    root, _, evals = parse_output(text)
    crashed = root is None
    triggers = len(re.findall(r"trigger=1", text))
    return root, evals, triggers, crashed


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-n",
        "--sizes",
        type=int,
        nargs="+",
        default=[8, 16, 32, 64, 128, 256, 512, 1024],
    )
    parser.add_argument(
        "-p",
        "--probs",
        type=float,
        nargs="+",
        default=[round(index * 0.1, 1) for index in range(11)],
    )
    parser.add_argument("-q", "--queries", type=int, nargs="+", default=[64])
    parser.add_argument("-r", "--runs", type=int, default=30)
    return parser.parse_args()


def main():
    args = parse_args()

    print("  building ... ", end="", flush=True)
    build()
    print("done", flush=True)
    call_pc = discover_add_mask_call_pc()

    for transcript_size in args.sizes:
        fault_step = discover_add_mask_call_step(transcript_size, call_pc)
        _, unmasked_evals, masked_evals = run_clean(transcript_size)

        for num_queries in args.queries:
            print(f"\n{'=' * 60}", flush=True)
            print(
                f"TRANSCRIPT_SIZE={transcript_size}  "
                f"NUM_QUERIES={num_queries}  FAULT_STEP={fault_step}",
                flush=True,
            )
            print(f"ADD_MASK_CALL_PC={call_pc:#x}", flush=True)
            print(f"{'=' * 60}", flush=True)

            print(
                f"  {'prob':>6}  {'runs':>5}  {'extracted':>10}  "
                f"{'crashes':>8}  {'avg triggers':>13}",
                flush=True,
            )
            print(f"  {'-' * 50}", flush=True)

            for probability in args.probs:
                extracted = 0
                crashes = 0
                total_triggers = 0

                for _ in range(args.runs):
                    _, evals, triggers, crashed = run_fi(
                        probability, transcript_size, fault_step
                    )
                    total_triggers += triggers
                    if crashed:
                        crashes += 1
                    elif try_extract(
                        evals, (unmasked_evals, masked_evals), num_queries
                    ):
                        extracted += 1

                print(
                    f"  {probability:>6.1f}  {args.runs:>5}  "
                    f"{extracted:>10}  {crashes:>8}  "
                    f"{total_triggers / args.runs:>13.1f}",
                    flush=True,
                )


if __name__ == "__main__":
    main()
