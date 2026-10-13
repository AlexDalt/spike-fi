#!/usr/bin/env python3
"""
Concatenated-padding fault-injection sweep against the recursive DIT NTT.

Rather than stopping the padding from mixing, this attack stops it being read.
Each call gathers its input by parity into two buffers, walking the array in
pair-sized strides until a walking pointer meets a bound computed once before
the loop.  A single-bit upset on that bound, at the first recursion step, pulls
it back by half the array: the gather copies the private trace and stops, and
the padding never enters the computation.

Everything else runs honestly on the parity split of [trace | 0 ... 0], so the
prover produces exactly the commitment of an unblinded prover - which is why
one fault is enough.  There is no padding left for the batch evaluation to
reintroduce, so the evaluation transform needs no fault at all and still emits
trace-derived data.  Three quarters of the committed positions then depend on
the private trace, and the trace itself appears verbatim at every other
position of the first half.

The bit to clear is the one worth 8n bytes: smaller bits leave the bound
unreachable by the stride, larger ones stop the gather before any trace is
read.  Under interleaved padding the same truncation captures alternating trace
and padding, so the attack is specific to the concatenated layout.
"""

import ct_fi

CONFIG = {
    "source": "test_recursive.c",
    "layout": ct_fi.concatenated,
    "transform": ct_fi.recursive_dit,
    # the fault stops the padding being read, so the computation proceeds as
    # though the prover had never generated it
    "effective_padding": lambda padding: [0] * len(padding),
    "interpolation_skip": lambda padded_len: None,
    "evaluation_skip": lambda extended_len: None,
    "faults": [
        {
            "function": "fft",
            "mark": 3,
            "kind": "bound",
            "stage": lambda marks: marks[0],
            "mask": lambda transcript_size: f"{8 * transcript_size:08x}",
            "mode": "0",
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
