#!/usr/bin/env python3
"""
Concatenated-padding fault-injection sweep against the recursive DIT NTT.

Rather than stopping the padding from mixing, this attack stops it being read.
Each call gathers its input by parity into two buffers, walking the array in
pair-sized strides until a walking pointer meets a bound computed before the
loop.  One instruction skip removes the loop's back edge at the iteration that
has just copied the last private element: control falls through instead of
looping, the gather stops at the midpoint of the padded transcript, and the
padding never enters the computation.

Everything else runs honestly on the parity split of [trace | 0 ... 0], so the
prover produces exactly the commitment of an unblinded prover - which is why
one fault is enough.  No padding is left for the batch evaluation to
reintroduce, so that transform needs no fault and still emits trace-derived
data.  Three quarters of the committed positions then depend on the private
trace, and the trace appears verbatim at every other position of the first
half.

The back edge executes once per gathered pair and its body calls
data-dependent helpers, so the step of the wanted iteration is found by
counting occurrences in a trace rather than from a marker offset.  Attacking
the loop's continuation rather than its bound keeps the fault independent of
where the allocator placed the array.
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
            "kind": "skip",
            # the gather consumes a pair per iteration, so the private trace is
            # complete after half of them
            "resolve": lambda transcript_size, applied: ct_fi.trace_step(
                ct_fi.discover_gather_back_edge("fft", 3),
                transcript_size // 2,
                transcript_size,
                applied,
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
