#!/usr/bin/env python3
"""
Concatenated-padding fault-injection sweep against the recursive DIF NTT.

Gentleman-Sande mixes a block before descending into its children, so with the
private trace in the lower half of the interpolation input and random padding
in the upper half, the only step that bridges them is the mixing loop of the
FIRST recursion step.  That is the exception to the call-skip rule: there is
no recursive call to suppress, so the fault is a single-bit upset on the block
index at the mixing loop's entry test, which skips the loop and lets the two
halves be transformed independently.  The trace-derived coefficients then land
on the even positions of the coefficient vector, courtesy of the output
bit-reversal.

In the batch evaluation the mixing that must be prevented happens inside one
subtree, so there the fault is an instruction skip on the recursive call that
would descend into it.
"""

import ct_fi

CONFIG = {
    "source": "test_recursive_dif.c",
    "layout": ct_fi.concatenated,
    "transform": ct_fi.recursive_dif,
    "interpolation_skip": lambda padded_len: "mix",
    "evaluation_skip": lambda extended_len: {"L", "R"},
    "faults": [
        {
            "function": "fft_recursive",
            "mark": 0,
            "kind": "upset",
            "stage": lambda marks: marks[0],
        },
        {
            "function": "fft_eval_recursive",
            "mark": 1,
            "kind": "skip",
            "stage": lambda marks: marks[0],
        },
        {
            # The sibling call is in tail position, so it is compiled into a
            # loop back edge; skipping that edge drops it.  Its marker fires
            # once per node, after that node's first child returns, so the
            # root's occurrence heads the list only when the first call was
            # skipped; otherwise the lower subtree emits 2n-1 markers first.
            "function": "fft_eval_recursive",
            "mark": 2,
            "kind": "skip",
            "stage_for": lambda marks, landed, size: (
                marks[0] if landed & 0b10 else marks[2 * size - 1]
            ),
        },
    ],
}


def build():
    ct_fi.build(CONFIG["source"])


def prepare(transcript_size):
    return ct_fi.prepare(CONFIG, transcript_size)


def run_fi(probability, transcript_size, faults):
    return ct_fi.run_fi(probability, transcript_size, faults)


def try_extract(faulted_evals, reference, num_queries):
    return ct_fi.try_extract(faulted_evals, reference, num_queries)


def main():
    ct_fi.sweep(CONFIG, ct_fi.parse_args(__doc__))


if __name__ == "__main__":
    main()
