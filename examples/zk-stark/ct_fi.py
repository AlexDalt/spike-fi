#!/usr/bin/env python3
"""
Shared machinery for the trace-padding fault-injection sweeps.

Every sweep follows the same shape.  The prover pads a private trace with
random field elements, interpolates the padded trace, blows the coefficients
up with zeros and commits to the batch evaluations.  Faults are injected to
stop the mixing steps that would otherwise combine trace-derived data with
padding-derived data.  Where those steps sit depends on the decomposition and
on the padding layout:

    layout        variant         interpolation      batch evaluation
    interleaved   iterative DIT   largest block      block size 4
    interleaved   iterative DIF   smallest block     block size EXT/2
    concatenated  recursive DIT   first-step mixing  first-step mixing
    concatenated  recursive DIF   first-step mixing  both child calls

The iterative faults are single-bit upsets that set bit 31 of the register
holding the block index at a block loop's entry test, so the unsigned "i < n"
test fails and that one stage is skipped while every other stage still runs.
Both targets are reached through the FI_MARK at the head of each stage: the
interpolation fault lands on the last stage and the evaluation fault on the
second, whichever way the block sizes run.

The recursive faults suppress a recursive call by instruction skip, except at
the first recursion step, where there is no call to skip and the upset lands
on the mixing loop's entry test instead.  Recursive DIF needs both child calls
of its evaluation transform gone: skipping only one leaves the surviving
subtree to remix the interleaved coefficients, since in a DIF the parity split
is bridged at the deepest nodes rather than at the subtree root.  The third
fault costs nothing in success probability: skipping the first call already
freezes the lower half, and its leaking positions agree with the three-fault
prediction, so a run leaks as soon as the first two faults land.  What the
third fault buys is coverage, doubling the leaking positions from n to 2n -
though not the functional classes, which stay at n.

Fault steps are absolute instruction counts and every landed fault shifts the
ones after it, so each fault is discovered once per combination of earlier
outcomes and the sweep samples which faults are attempted rather than handing
the probability to the simulator.  That is the same Bernoulli(p) per fault
opportunity, with exact step bookkeeping.  A fault may declare "requires" when
its marker only heads the intended occurrence after an earlier fault landed;
it is then attempted only in those runs, which leaves the probability of all
faults landing unchanged.
"""

import os
import random
import re
import signal
import subprocess

EXAMPLE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_HOME = os.path.abspath(os.path.join(EXAMPLE_DIR, "..", ".."))
ASM_PATH = os.path.join(REPO_HOME, "build", "zk-stark", "test.asm")

FIELD_MODULUS = 2013265921
FIELD_GENERATOR = 31

# Setting bit 31 of the block index makes the unsigned "i < n" test fail.
BLOCK_INDEX_HIGH_BIT_MASK = "80000000"

ABI_REGISTERS = {
    "zero": 0, "ra": 1, "sp": 2, "gp": 3, "tp": 4,
    "t0": 5, "t1": 6, "t2": 7, "s0": 8, "fp": 8, "s1": 9,
    "a0": 10, "a1": 11, "a2": 12, "a3": 13, "a4": 14, "a5": 15,
    "a6": 16, "a7": 17, "s2": 18, "s3": 19, "s4": 20, "s5": 21,
    "s6": 22, "s7": 23, "s8": 24, "s9": 25, "s10": 26, "s11": 27,
    "t3": 28, "t4": 29, "t5": 30, "t6": 31,
}

# Field helpers and transform models

def root_of_unity(order):
    return pow(FIELD_GENERATOR, (FIELD_MODULUS - 1) // order, FIELD_MODULUS)


def bit_reverse(values):
    """The permutation applied by bit_reverse() in the victim programs."""
    n = len(values)
    permuted = list(values)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            permuted[i], permuted[j] = permuted[j], permuted[i]
    return permuted


def iterative_dit(values, root, skip_block=None):
    """test.c's fft(); skip_block=L drops the block loop for block size L."""
    a = bit_reverse(values)
    n = len(a)
    block = 2
    while block <= n:
        wlen = pow(root, n // block, FIELD_MODULUS)
        if block != skip_block:
            half = block // 2
            for i in range(0, n, block):
                w = 1
                for j in range(half):
                    u = a[i + j] % FIELD_MODULUS
                    v = a[i + j + half] * w % FIELD_MODULUS
                    a[i + j] = (u + v) % FIELD_MODULUS
                    a[i + j + half] = (u - v) % FIELD_MODULUS
                    w = w * wlen % FIELD_MODULUS
        block *= 2
    return a


def iterative_dif(values, root, skip_block=None):
    """test_dif.c's fft(); stages run from the largest block to the smallest."""
    a = list(values)
    n = len(a)
    block = n
    while block >= 2:
        half = block // 2
        wlen = pow(root, n // block, FIELD_MODULUS)
        if block != skip_block:
            for i in range(0, n, block):
                w = 1
                for j in range(half):
                    u = a[i + j] % FIELD_MODULUS
                    v = a[i + j + half] % FIELD_MODULUS
                    a[i + j] = (u + v) % FIELD_MODULUS
                    a[i + j + half] = (u - v) * w % FIELD_MODULUS
                    w = w * wlen % FIELD_MODULUS
        block //= 2
    return bit_reverse(a)


def _skipped(skip, token):
    """A fault target may name one site or several."""
    if skip is None:
        return False
    if isinstance(skip, (set, frozenset, list, tuple)):
        return token in skip
    return skip == token


def _recursive_dit(values, root, skip, path):
    """
    test_recursive.c's fft().  skip is "mix" for the mixing loop of the first
    recursion step, or a path of L/R characters naming a recursive call.  The
    children transform copies, so skipping a mixing loop discards their
    results and leaves that node's input untouched.
    """
    n = len(values)
    if n <= 1:
        return list(values)
    half = n // 2
    even = [values[2 * i] % FIELD_MODULUS for i in range(half)]
    odd = [values[2 * i + 1] % FIELD_MODULUS for i in range(half)]
    squared = root * root % FIELD_MODULUS
    if not _skipped(skip, path + "L"):
        even = _recursive_dit(even, squared, skip, path + "L")
    if not _skipped(skip, path + "R"):
        odd = _recursive_dit(odd, squared, skip, path + "R")

    out = list(values)
    if not _skipped(skip, "mix" + path):
        w = 1
        for j in range(half):
            t = odd[j] * w % FIELD_MODULUS
            out[j] = (even[j] + t) % FIELD_MODULUS
            out[j + half] = (even[j] - t) % FIELD_MODULUS
            w = w * root % FIELD_MODULUS
    return out


def _recursive_dif(values, root, skip, path):
    """test_recursive_dif.c's fft_recursive(); mixes before descending."""
    n = len(values)
    if n <= 1:
        return list(values)
    half = n // 2
    out = list(values)
    if not _skipped(skip, "mix" + path):
        w = 1
        for j in range(half):
            u = out[j] % FIELD_MODULUS
            v = out[j + half] % FIELD_MODULUS
            out[j] = (u + v) % FIELD_MODULUS
            out[j + half] = (u - v) * w % FIELD_MODULUS
            w = w * root % FIELD_MODULUS

    squared = root * root % FIELD_MODULUS
    lower, upper = out[:half], out[half:]
    if not _skipped(skip, path + "L"):
        lower = _recursive_dif(lower, squared, skip, path + "L")
    if not _skipped(skip, path + "R"):
        upper = _recursive_dif(upper, squared, skip, path + "R")
    return lower + upper


def recursive_dit(values, root, skip=None):
    return _recursive_dit(values, root, skip, "")


def recursive_dif(values, root, skip=None):
    return bit_reverse(_recursive_dif(values, root, skip, ""))


def interpolate(values, transform, skip_block=None):
    """ifft(): the inverse transform followed by the 1/n scaling."""
    n = len(values)
    inverse_root = pow(root_of_unity(n), FIELD_MODULUS - 2, FIELD_MODULUS)
    inverse_n = pow(n, FIELD_MODULUS - 2, FIELD_MODULUS)
    return [
        value * inverse_n % FIELD_MODULUS
        for value in transform(values, inverse_root, skip_block)
    ]


def padding_values(count, transcript_size):
    """The xorshift stream the victims use for the random padding."""
    state = 0x9E3779B97F4A7C15 ^ transcript_size
    mask = (1 << 64) - 1
    out = []
    for _ in range(count):
        state ^= (state << 13) & mask
        state ^= state >> 7
        state ^= (state << 17) & mask
        out.append(state % FIELD_MODULUS)
    return out


def interleaved(trace, padding):
    return [value for pair in zip(trace, padding) for value in pair]


def concatenated(trace, padding):
    return list(trace) + list(padding)

# Running the victim

def make(*args):
    return subprocess.run(
        ["make", *args],
        cwd=EXAMPLE_DIR,
        capture_output=True,
        text=True,
    )


def build(source):
    make("clean").check_returncode()
    make("build", f"TEST_SRC={source}").check_returncode()


def run(transcript_size, specs=(), enable=False):
    arguments = []
    if enable or specs:
        arguments = ["--fi-enable", "--fi-debug"]
        arguments += [f"--fi-spec={spec}" for spec in specs]
    call = ["run", f"TRANSCRIPT_SIZE={transcript_size}"]
    if arguments:
        call.append("FI=" + " ".join(arguments))
    result = make(*call)
    return result.stdout + result.stderr


def parse_output(text):
    root = re.search(r"merkle root: ([0-9a-f]+)", text)
    transcript_block = re.search(
        r"transcript:\n(.*?)(?:\ncoeffs:|\Z)", text, re.DOTALL
    )
    evals_block = re.search(r"\nevals:\n(.*?)(?:\n\n|\Z)", text, re.DOTALL)
    transcript = (
        re.findall(r"\[\d+\] = (\d+)", transcript_block.group(1))
        if transcript_block
        else []
    )
    evals = (
        re.findall(r"\[\d+\] = (\d+)", evals_block.group(1))
        if evals_block
        else []
    )
    return (
        root.group(1) if root else None,
        [int(value) for value in transcript],
        [int(value) for value in evals],
    )

# Locating the faults

def function_body(asm, function):
    header = re.compile(rf"^[0-9a-f]+ <{function}(?:\.[^>]*)?>:")
    boundary = re.compile(r"^[0-9a-f]+ <[^>]+>:")
    body = []
    for line in asm:
        if body:
            if boundary.match(line):
                break
            body.append(line)
        elif header.match(line):
            body.append(line)
    return body


def discover_block_guard(function, mark_id):
    """
    Find the block loop's entry test that follows FI_MARK( mark_id ), and
    report the register holding the block index along with the number of
    instructions between the mark and the test.
    """
    with open(ASM_PATH, "r", encoding="utf-8") as asm_file:
        body = function_body(asm_file.readlines(), function)

    mark = re.compile(rf"\bslti\s+zero,zero,{mark_id}\b")
    instruction = re.compile(r"\s*([0-9a-f]+):\s+[0-9a-f]+\s")
    load = re.compile(r"\blw\s+([a-z0-9]+),-?\d+\(")
    branch = re.compile(r"\bb(?:ltu|geu|gtu|leu)\s+([a-z0-9]+),([a-z0-9]+),")

    distance = None
    index_register = None
    for line in body:
        if distance is None:
            if mark.search(line):
                distance = 0
            continue
        if not instruction.match(line):
            continue
        distance += 1
        loaded = load.search(line)
        if loaded:
            index_register = loaded.group(1)
            continue
        guard = branch.search(line)
        if guard and index_register in guard.groups():
            return ABI_REGISTERS[index_register], distance

    raise RuntimeError(f"could not find the block loop entry test in {function}")


def mark_steps(transcript_size, specs=()):
    """Collect the step of every FI_MARK, keyed by marker id, in order."""
    text = run(transcript_size, specs, enable=True)
    steps = {}
    for step, mark_id in re.findall(
        r"fi: mark => step = (\d+),.*?id = (\d+)", text
    ):
        steps.setdefault(int(mark_id), []).append(int(step))
    return steps


def upset_spec(step, register):
    return f"{step}:1.0:r:{register}:1:{BLOCK_INDEX_HIGH_BIT_MASK}"


def skip_spec(step):
    return f"{step}:1.0:s"


def discover_call(function, mark_id):
    """
    Instructions from FI_MARK( mark_id ) to the control transfer that performs
    the recursive call it marks.  A call in tail position is compiled into a
    loop back edge rather than a jal, so any transfer counts: skipping the
    back edge drops the call just as skipping a jal would.
    """
    with open(ASM_PATH, "r", encoding="utf-8") as asm_file:
        body = function_body(asm_file.readlines(), function)

    mark = re.compile(rf"\bslti\s+zero,zero,{mark_id}\b")
    instruction = re.compile(r"\s*([0-9a-f]+):\s+[0-9a-f]+\s")
    transfer = re.compile(r"\b(jal|jalr|j|b(?:eq|ne|lt|ge|ltu|geu)z?)\s")

    distance = None
    for line in body:
        if distance is None:
            if mark.search(line):
                distance = 0
            continue
        if not instruction.match(line):
            continue
        distance += 1
        if transfer.search(line):
            return None, distance

    raise RuntimeError(f"could not find the marked call in {function}")


def discover_gather_bound(function, mark_id):
    """
    Locate the gather loop that follows FI_MARK( mark_id ) and report the
    register holding its loop-invariant bound, with the number of instructions
    from the marker to the first point where that bound is live.

    The loop walks the source array in pair-sized strides and exits when the
    walking pointer equals the bound, so the bound is written once and read
    only by the back edge: one upset on it changes the loop's extent for every
    remaining iteration.
    """
    with open(ASM_PATH, "r", encoding="utf-8") as asm_file:
        body = function_body(asm_file.readlines(), function)

    mark = re.compile(rf"\bslti\s+zero,zero,{mark_id}\b")
    instruction = re.compile(r"\s*([0-9a-f]+):\s+[0-9a-f]+\s")
    stride = re.compile(r"\baddi\s+([a-z0-9]+),\1,16\b")
    back_edge = re.compile(r"\bbne\s+([a-z0-9]+),([a-z0-9]+),")
    definition = re.compile(r"\badd\s+([a-z0-9]+),")

    seen = []
    walker = None
    for line in body:
        if not seen:
            if mark.search(line):
                seen.append(line)
            continue
        if not instruction.match(line):
            continue
        seen.append(line)
        if stride.search(line):
            walker = stride.search(line).group(1)
        edge = back_edge.search(line)
        if edge and walker in edge.groups():
            bound = [r for r in edge.groups() if r != walker][0]
            for offset, earlier in enumerate(seen):
                defined = definition.search(earlier)
                if defined and defined.group(1) == bound:
                    return ABI_REGISTERS[bound], offset + 1
            raise RuntimeError(f"bound {bound} is not defined after the marker")

    raise RuntimeError(f"could not find the gather loop in {function}")


def discover_gather_back_edge(function, mark_id):
    """Address of the back edge closing the gather loop after FI_MARK( mark_id )."""
    with open(ASM_PATH, "r", encoding="utf-8") as asm_file:
        body = function_body(asm_file.readlines(), function)

    mark = re.compile(rf"\bslti\s+zero,zero,{mark_id}\b")
    instruction = re.compile(r"\s*([0-9a-f]+):\s+[0-9a-f]+\s")
    stride = re.compile(r"\baddi\s+([a-z0-9]+),\1,16\b")
    back_edge = re.compile(r"\bbne\s+([a-z0-9]+),([a-z0-9]+),")

    marked = False
    walker = None
    for line in body:
        if not marked:
            marked = bool(mark.search(line))
            continue
        found = instruction.match(line)
        if not found:
            continue
        if stride.search(line):
            walker = stride.search(line).group(1)
        edge = back_edge.search(line)
        if edge and walker in edge.groups():
            return int(found.group(1), 16)

    raise RuntimeError(f"could not find the gather back edge in {function}")


def trace_step(pc, occurrence, transcript_size, applied=()):
    """
    Step at which the given address executes for the occurrence-th time.

    Some faults have to be aimed at one iteration of a loop whose body calls
    data-dependent helpers, so the step cannot be derived from a marker and an
    instruction count.  The run is traced and the occurrences counted instead;
    the trace is abandoned as soon as the wanted one appears.
    """
    pattern = re.compile(rf"fi: trace => step = (\d+), pc =\s+{pc:x},")
    arguments = ["--fi-enable", "--fi-debug", "--fi-trace"]
    arguments += [f"--fi-spec={spec}" for spec in applied]
    process = subprocess.Popen(
        ["make", "run", f"TRANSCRIPT_SIZE={transcript_size}",
         "FI=" + " ".join(arguments)],
        cwd=EXAMPLE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=True,
    )
    seen = 0
    try:
        for line in process.stdout:
            found = pattern.search(line)
            if not found:
                continue
            seen += 1
            if seen == occurrence:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait()
                return int(found.group(1))
        process.wait()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait()

    raise RuntimeError(f"address {pc:#x} did not execute {occurrence} times")


def locate(descriptor):
    """Resolve the faulted register, if any, and the mark-relative distance."""
    if descriptor["kind"] == "skip":
        return discover_call(descriptor["function"], descriptor["mark"])
    if descriptor["kind"] == "bound":
        return discover_gather_bound(descriptor["function"], descriptor["mark"])
    return discover_block_guard(descriptor["function"], descriptor["mark"])


def fault_spec(descriptor, step, register, transcript_size):
    if descriptor["kind"] == "skip":
        return skip_spec(step)
    mask = descriptor.get("mask", lambda _: BLOCK_INDEX_HIGH_BIT_MASK)(transcript_size)
    mode = descriptor.get("mode", "1")
    return f"{step}:1.0:r:{register}:{mode}:{mask}"


def discover_faults(transcript_size, descriptors):
    """
    Resolve the fault specifications for one transcript size.

    descriptors is the ordered list of faults the attack injects.  Each names
    the function it fires in, the marker that heads the candidate steps, a
    selector picking which occurrence of that marker heads the step being
    attacked, and the fault kind; "requires" marks a fault that is only
    attempted when an earlier one landed, because otherwise its marker does
    not head the right occurrence.

    Steps are absolute instruction counts and every landed fault shifts the
    ones after it, so each fault is resolved once per combination of earlier
    outcomes: a run is made with each reachable subset of the earlier faults
    applied, and the steps of the remaining faults are read from its markers.
    """
    located = [
        (None, None) if "resolve" in descriptor else locate(descriptor)
        for descriptor in descriptors
    ]
    count = len(descriptors)
    specs = {}

    for prior_mask in range(1 << (count - 1)):
        applied = []
        reachable = True
        for earlier in range(count - 1):
            if not prior_mask & (1 << earlier):
                continue
            key = (earlier, prior_mask & ((1 << earlier) - 1))
            if key not in specs:
                reachable = False
                break
            applied.append(specs[key])
        if not reachable:
            continue

        marks = mark_steps(transcript_size, applied)
        for index, descriptor in enumerate(descriptors):
            if prior_mask >> index:
                continue
            requires = descriptor.get("requires")
            if requires is not None and not prior_mask & (1 << requires):
                continue
            key = (index, prior_mask)
            if key in specs:
                continue
            register, distance = located[index]
            if "resolve" in descriptor:
                step = descriptor["resolve"](transcript_size, applied)
            elif "stage_for" in descriptor:
                # the occurrence that heads the intended site can depend on
                # which earlier faults landed
                step = descriptor["stage_for"](
                    marks[descriptor["mark"]], prior_mask, transcript_size
                ) + distance
            else:
                step = descriptor["stage"](marks[descriptor["mark"]]) + distance
            specs[key] = fault_spec(descriptor, step, register, transcript_size)

    return {
        "descriptors": descriptors,
        "specs": specs,
        "steps": [
            specs[(index, (1 << index) - 1)].split(":")[0]
            for index in range(count)
        ],
    }


def run_fi(probability, transcript_size, faults):
    """
    One prover run.  Each fault opportunity is taken with the given
    probability, and the step used for each depends on which of the earlier
    faults were taken.  A fault declaring "requires" is only attempted when
    that earlier fault landed; since it is attempted with the same probability
    whenever it is reachable, all faults still land together with probability
    p raised to their number.
    """
    landed = 0
    specs = []
    for index, descriptor in enumerate(faults["descriptors"]):
        requires = descriptor.get("requires")
        if requires is not None and not landed & (1 << requires):
            continue
        if random.random() < probability:
            specs.append(faults["specs"][(index, landed)])
            landed |= 1 << index

    text = run(transcript_size, specs)
    root, _, evals = parse_output(text)
    triggers = len(re.findall(r"trigger=1", text))
    return root, evals, triggers, root is None


def try_extract(faulted_evals, reference, num_queries):
    """
    One attempt: open num_queries random positions and report whether the run
    leaked.  An attempt succeeds when an opened position carries the predicted
    trace-dependent value, which the honest prover would not have published
    there.  Repetitions estimate the success probability of the attack; they
    are not an attacker opening the same prover repeatedly.
    """
    expected, leaks, baseline_evals = reference
    if not faulted_evals:
        return False
    opened = random.sample(
        range(len(faulted_evals)), min(num_queries, len(faulted_evals))
    )
    return any(
        index in leaks
        and faulted_evals[index] == expected[index]
        and faulted_evals[index] != baseline_evals[index]
        for index in opened
    )

# Per-victim driver

def predict(config, transcript, transcript_size):
    """
    Predict the committed evaluations under both faults, and work out which
    positions carry values that depend on the private trace alone.  The
    dependence is measured rather than assumed: the pipeline is re-run with a
    perturbed trace and with perturbed padding, and a position counts as a
    leak when it follows the trace and ignores the padding.
    """
    padded_len = 2 * transcript_size
    extended_len = 2 * padded_len
    padding = padding_values(transcript_size, transcript_size)

    effective = config.get("effective_padding", lambda values: values)

    def pipeline(trace, pad):
        padded = config["layout"](
            [v % FIELD_MODULUS for v in trace], effective(pad)
        )
        coeffs = interpolate(
            padded,
            config["transform"],
            config["interpolation_skip"](padded_len),
        )
        blown = coeffs + [0] * (extended_len - padded_len)
        return config["transform"](
            blown,
            root_of_unity(extended_len),
            config["evaluation_skip"](extended_len),
        )

    def probe(values, salt):
        deltas = padding_values(len(values), salt)
        return [(v + d) % FIELD_MODULUS for v, d in zip(values, deltas)]

    # The transforms are linear, so a constant perturbation only moves the DC
    # coefficient and would hide most of the dependence.  Probe with two
    # pseudo-random deltas per side instead, and keep the positions that
    # follow the trace under both while ignoring the padding under both.
    expected = pipeline(transcript, padding)
    moved_trace = [
        pipeline(probe(transcript, salt), padding) for salt in (101, 202)
    ]
    moved_padding = [
        pipeline(transcript, probe(padding, salt)) for salt in (303, 404)
    ]
    leaks = {
        index
        for index in range(len(expected))
        if all(expected[index] != moved[index] for moved in moved_trace)
        and all(expected[index] == moved[index] for moved in moved_padding)
    }

    return expected, leaks


def prepare(config, transcript_size):
    """Resolve both fault specifications and build the extraction reference."""
    faults = discover_faults(transcript_size, config["faults"])
    _, transcript, baseline_evals = parse_output(run(transcript_size))
    expected, leaks = predict(config, transcript, transcript_size)
    return faults, (expected, leaks, baseline_evals)


def sweep(config, args):
    build(config["source"])
    print("  built", config["source"], flush=True)

    for transcript_size in args.sizes:
        faults, reference = prepare(config, transcript_size)
        expected, leaks, _ = reference

        for num_queries in args.queries:
            print(f"\n{'=' * 60}", flush=True)
            print(f"TRANSCRIPT_SIZE={transcript_size}  "
                  f"NUM_QUERIES={num_queries}", flush=True)
            print("FAULT_STEPS=" + "+".join(faults["steps"]), flush=True)
            print(f"LEAKING_POSITIONS={len(leaks)}/{len(expected)}", flush=True)
            print(f"{'=' * 60}", flush=True)
            print(f"  {'prob':>6}  {'runs':>5}  {'successes':>10}  "
                  f"{'crashes':>8}  {'avg triggers':>13}", flush=True)
            print(f"  {'-' * 50}", flush=True)

            for probability in args.probs:
                successes = 0
                crashes = 0
                total_triggers = 0
                for _ in range(args.runs):
                    _, evals, triggers, crashed = run_fi(
                        probability, transcript_size, faults
                    )
                    total_triggers += triggers
                    if crashed:
                        crashes += 1
                    elif try_extract(evals, reference, num_queries):
                        successes += 1
                print(f"  {probability:>6.1f}  {args.runs:>5}  {successes:>10}  "
                      f"{crashes:>8}  {total_triggers / args.runs:>13.1f}",
                      flush=True)


def parse_args(description):
    import argparse
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("-n", "--sizes", type=int, nargs="+",
                        default=[8, 16, 32, 64, 128, 256, 512, 1024])
    parser.add_argument("-p", "--probs", type=float, nargs="+",
                        default=[round(i * 0.1, 1) for i in range(1, 11)])
    parser.add_argument("-q", "--queries", type=int, nargs="+", default=[64])
    parser.add_argument("-r", "--runs", type=int, default=30)
    return parser.parse_args()
