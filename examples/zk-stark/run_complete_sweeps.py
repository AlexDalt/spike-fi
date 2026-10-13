#!/usr/bin/env python3
"""Run the complete trace-padding fault-injection sweeps.

Each configuration pairs a padding layout with a Cooley-Tukey variant and
injects two faults, one per transform, each stopping the mixing step that
would otherwise combine trace-derived with padding-derived data.  The
iterative variants use a single-bit upset on the block index at a block
loop's entry test; the recursive variants skip the recursive call that would
perform the mixing, except at the first recursion step, where there is no
call to skip and the upset lands on the mixing loop's entry test instead.
"""

import argparse
import csv
import importlib
import os


EXAMPLE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT = os.path.join(
    EXAMPLE_DIR, "results", "trace_padding_fault_sweeps.csv"
)

N_SWEEP = [(size, 64) for size in (128, 256, 512, 1024)]
OPENINGS_SWEEP = [(512, openings) for openings in (32, 64, 128, 256)]
PROBABILITIES = [round(index * 0.1, 1) for index in range(11)]

FIELDS = [
    "configuration",
    "implementation",
    "decomposition",
    "padding",
    "sweep",
    "transcript_size",
    "openings",
    "fault_probability",
    "repetitions",
    "successes",
    "crashes",
    "total_triggers",
    "average_triggers",
    "fault_target",
]

CONFIGURATIONS = {
    "iterative_dit": {
        "module": "fi_sweep_extract",
        "implementation": "iterative",
        "decomposition": "DIT",
        "padding": "interleaved",
    },
    "iterative_dif": {
        "module": "fi_sweep_dif",
        "implementation": "iterative",
        "decomposition": "DIF",
        "padding": "interleaved",
    },
    "recursive_dit": {
        "module": "fi_sweep_recursive",
        "implementation": "recursive",
        "decomposition": "DIT",
        "padding": "concatenated",
    },
    "recursive_dif": {
        "module": "fi_sweep_recursive_dif",
        "implementation": "recursive",
        "decomposition": "DIF",
        "padding": "concatenated",
    },
    "polynomial_masking": {
        "module": "fi_sweep_masking",
        "implementation": "pointwise",
        "decomposition": "masking",
        "padding": "none",
    },
}


def row_key(row):
    return (
        row["configuration"],
        row["sweep"],
        int(row["transcript_size"]),
        int(row["openings"]),
        float(row["fault_probability"]),
    )


def read_completed(output_path):
    if not os.path.exists(output_path):
        return set()

    with open(output_path, newline="", encoding="utf-8") as output_file:
        return {row_key(row) for row in csv.DictReader(output_file)}


def format_target(faults):
    if isinstance(faults, dict):
        return "+".join(f"step:{step}" for step in faults["steps"])
    return f"step:{faults}"


def run_configuration(name, details, writer, output_file, completed, runs):
    print(f"\n[{name}] building", flush=True)
    module = importlib.import_module(details["module"])
    module.build()
    size_cache = {}

    experiments = (("n", N_SWEEP), ("openings", OPENINGS_SWEEP))
    for sweep, cases in experiments:
        for transcript_size, openings in cases:
            if transcript_size not in size_cache:
                print(f"[{name}] preparing n={transcript_size}", flush=True)
                size_cache[transcript_size] = module.prepare(transcript_size)

            faults, reference = size_cache[transcript_size]
            for probability in PROBABILITIES:
                key = (name, sweep, transcript_size, openings, probability)
                if key in completed:
                    print(
                        f"[{name}] {sweep} n={transcript_size} o={openings} "
                        f"p={probability:.1f}: already complete",
                        flush=True,
                    )
                    continue

                successes = 0
                crashes = 0
                total_triggers = 0
                for _ in range(runs):
                    _, evals, triggers, crashed = module.run_fi(
                        probability, transcript_size, faults
                    )
                    total_triggers += triggers
                    if crashed:
                        crashes += 1
                    elif module.try_extract(evals, reference, openings):
                        successes += 1

                writer.writerow({
                    "configuration": name,
                    "implementation": details["implementation"],
                    "decomposition": details["decomposition"],
                    "padding": details["padding"],
                    "sweep": sweep,
                    "transcript_size": transcript_size,
                    "openings": openings,
                    "fault_probability": f"{probability:.1f}",
                    "repetitions": runs,
                    "successes": successes,
                    "crashes": crashes,
                    "total_triggers": total_triggers,
                    "average_triggers": f"{total_triggers / runs:.6f}",
                    "fault_target": format_target(faults),
                })
                output_file.flush()
                os.fsync(output_file.fileno())
                completed.add(key)
                print(
                    f"[{name}] {sweep} n={transcript_size} o={openings} "
                    f"p={probability:.1f}: {successes}/{runs} successes, "
                    f"{crashes} crashes",
                    flush=True,
                )


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--configurations",
        nargs="+",
        choices=CONFIGURATIONS,
        default=list(CONFIGURATIONS),
    )
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--sizes", type=int, nargs="+", default=[128, 256, 512, 1024],
        help="transcript sizes for the size sweep",
    )
    parser.add_argument(
        "--fixed-openings", type=int, default=64,
        help="openings held fixed while sweeping transcript size",
    )
    parser.add_argument(
        "--openings", type=int, nargs="+", default=[32, 64, 128, 256],
        help="opening counts for the openings sweep",
    )
    parser.add_argument(
        "--fixed-size", type=int, default=512,
        help="transcript size held fixed while sweeping openings",
    )
    return parser.parse_args()


def main():
    global N_SWEEP, OPENINGS_SWEEP
    args = parse_args()
    N_SWEEP = [(size, args.fixed_openings) for size in args.sizes]
    OPENINGS_SWEEP = [(args.fixed_size, openings) for openings in args.openings]
    output_path = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    completed = read_completed(output_path)
    needs_header = (
        not os.path.exists(output_path) or os.path.getsize(output_path) == 0
    )

    with open(output_path, "a", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=FIELDS)
        if needs_header:
            writer.writeheader()
            output_file.flush()

        for name in args.configurations:
            run_configuration(
                name,
                CONFIGURATIONS[name],
                writer,
                output_file,
                completed,
                args.runs,
            )

    print(f"\nResults written to {output_path}", flush=True)


if __name__ == "__main__":
    main()
