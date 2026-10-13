#!/usr/bin/env python3
"""
Interleaved-padding fault-injection sweep against the iterative DIT NTT.

The prover interleaves random padding with the private trace, so trace and
padding sit at even and odd positions of the interpolation input.  A DIT
transform bit-reverses its input, which turns that even/odd split into a
half/half one, so the mixing step that bridges trace and padding is the
LARGEST block of the interpolation.  A single-bit upset on the block index at
that stage's entry test skips the stage and leaves the trace-derived
coefficients in the lower half of the coefficient vector.

The blow-up then appends zeros, so the trace-derived and padding-derived
coefficients are separated by bit log2(n) of a 4n-long array, and the step
that bridges them in the batch evaluation is the stage with block size 4.  A
second upset there leaves evaluations of the private polynomial at the
positions whose index is 0 or 1 modulo 4; a verifier opening one of those
learns a witness value.
"""

import ct_fi

CONFIG = {
    "source": "test.c",
    "layout": ct_fi.interleaved,
    "transform": ct_fi.iterative_dit,
    "interpolation_skip": lambda padded_len: padded_len,
    "evaluation_skip": lambda extended_len: 4,
    "faults": [
        {
            "function": "fft",
            "mark": 0,
            "kind": "upset",
            "stage": lambda marks: marks[-1],
        },
        {
            "function": "fft_eval",
            "mark": 1,
            "kind": "upset",
            "stage": lambda marks: marks[1],
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
