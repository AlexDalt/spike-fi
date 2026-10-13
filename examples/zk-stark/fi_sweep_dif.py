#!/usr/bin/env python3
"""
Interleaved-padding fault-injection sweep against the iterative DIF NTT.

Gentleman-Sande reads its input in natural order, so with padding interleaved
into the trace the values that must not mix are neighbours, and the step that
bridges them is the SMALLEST block of the interpolation - the mirror of the
DIT case, where the input bit-reversal moves that same edge to the largest
block.  A single-bit upset on the block index at that stage's entry test skips
it and leaves the trace-derived coefficients in the lower half of the
coefficient vector.

After the zero blow-up the trace-derived and padding-derived coefficients are
separated by half the extended length, so the batch evaluation's bridging step
is the stage with block size EXT/2.  A second upset there leaves evaluations
of the private polynomial at the positions whose index is 0 or 1 modulo 4.

Both stages are the last and the second stage mark respectively, exactly as in
the DIT sweep; only the block sizes those marks correspond to differ.
"""

import ct_fi

CONFIG = {
    "source": "test_dif.c",
    "layout": ct_fi.interleaved,
    "transform": ct_fi.iterative_dif,
    "interpolation_skip": lambda padded_len: 2,
    "evaluation_skip": lambda extended_len: extended_len // 2,
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
