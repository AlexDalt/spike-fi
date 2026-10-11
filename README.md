# `spike-fi`: `spike` + (experimental) fault injection

<!--- ==================================================================== --->

## Overview

Although the concept of
[fault injection (or induction)](https://en.wikipedia.org/wiki/Fault_injection)
can be viewed as related to hardware and software testing, within the
context of cryptography it refers to a class of active implementation 
attack.
The idea is that an attacker somehow actively influence execution of a 
target device, e.g., via mechanisms which include
clock glitches, over- or under-supply of power, electro-magnetic pulses, etc.,
in such a way that a "useful" fault is caused.
That is, the target device behaves in some way that is advantageous to 
the attacker; this behaviour is used to support an associated attack,
including goals such as recovery of cryptographic key material.

Advances in cryptographic fault injection attacks span two aspects,
namely an
experimental aspect, i.e., the concrete mechanism for injection of faults with given properties,
and
  analytical aspect, i.e., how one leverages that ability to satisfy the attack goal.
Within this field, tools which support simulated fault injection can
therefore be useful; such tools allow one to decouple the two aspects,
e.g., allowing progress with the analytical aspect independently from
the experimental aspect.
[Adhikary et al.](https://eprint.iacr.org/2024/1944.pdf)
survey and classify related tools, among which are type-S4 instances:
these simulate execution of
"*a binary file [...] compiled for a specific target*".
Various simulators of this type exist, for various 
[ISAs](https://en.wikipedia.org/wiki/Instruction_set_architecture).

`spike-fi`
is one further example: it focuses on the 
[RISC-V](https://en.wikipedia.org/wiki/RISC-V)
ISA, using the 
[`spike`](https://github.com/riscv-software-src/riscv-isa-sim)
instruction set simulator as a basis.
Note that alternatives exist: see, for example
the [gem5](https://www.gem5.org)-based [InjectV](https://arxiv.org/pdf/2606.12011)
tool.

<!--- ==================================================================== --->

# Configure

1. Clone the repo.

   ```sh
   git clone https://github.com/bristolcrypto/spike-fi.git ./spike-fi
   cd ./spike-fi
   git submodule update --init --recursive
   source ./bin/conf.sh
   ```

2. Fix paths, e.g., 
  
   ```sh
   export RISCV="${REPO_HOME}/build/riscv"
   ```

3. Build a multi-architecture 
   [tool-chain](https://github.com/riscv/riscv-gnu-toolchain)
   into `${RISCV}`:
  
   ```sh
   git clone https://github.com/riscv/riscv-gnu-toolchain.git ${REPO_HOME}/build/riscv-gnu-toolchain
   cd ${REPO_HOME}/build/riscv-gnu-toolchain
   sed -i '/shallow = true/d' .gitmodules
   sed -i 's/--depth 1//g' Makefile.in
   ./configure --prefix="${RISCV}" --enable-multilib --with-multilib-generator="rv32gc-ilp32--;rv64gc-lp64--"
   make
   ```

   noting that the `sed` lines are to deal with an
   [issue](https://github.com/riscv-collab/riscv-gnu-toolchain/issues/1669) 
   related to shallow cloning sub-modules.

4. Build `spike` and `pk`
   into `${RISCV}`:

   ```sh
   make --directory="${REPO_HOME}/spike" clone
   make --directory="${REPO_HOME}/spike" apply
   make --directory="${REPO_HOME}/spike" build
   ```

  noting that currently the implementation is
  [patch](https://savannah.gnu.org/projects/patch)-based,
  making changes to it is somewhat tricky.  
  The idea, for each component,
  (i.e., `pk` and `spike`)
  referred to as `${COMPONENT}` is as follows:

  - perform a fresh clone of the component repository,
  - apply the existing patch to the cloned component repository,
  - implement the change in the cloned component repository,
  - stage the change via `git add`, but do *not* commit it, in the cloned component repository,
  - execute `${COMPONENT}-update.sh` to produce an updated patch,
  - optionally commit and push the updated patch.

<!--- ==================================================================== --->

# Implementation

- The implementation follows a basic high-level strategy:

  - The central concept is a so-called step counter, whose state forms part of the simulated core.  The idea is that execution of instructions will update the step counter, and, when the step counter matches a specified fault, that fault is triggered; the triggering is conditional, in the sense it only occurs 1) when the fault injection mechanism is enabled and, beyond that, 2) in a non-deterministic manner subject to a specified probability.  One might wonder why, e.g., the cycle counter CSR `mcycle`, is not used instead.  The way `spike` manages this, and also relates counters such as `instret`, makes it difficult (or at least seems to), with the separate step counter the simpler solution.
  - When triggered, a function modelling the fault action is invoked.  This is possible by inserting a hook into the mechanism for simulation of instruction semantics; the modular approach used by `spike`, where the semantics of each instruction is modelled by a header file included into a wrapper, makes this easier in the sense the insertion is quite localised.  Note that currently the action is invoked *before* the instruction semantics, although there may be use-cases or fault models where invoking it afterwards is either attractive or necessary.
  - The fault action essentially has access to the entire state of the core, trivially allowing injection of faults into, e.g., general-purpose registers.  Faults which impact the program counter and so control-flow are more difficult, in the sense they require more careful consideration of the hook insertion; currently only instruction skip is supported, which essentially means conditional execution of the instruction semantics under control of the fault action.

- The implementation is captured in only a few changes:

  - `${SPIKE}/spike_main/spike.cc`
    manages some additional command line arguments:
  
    - `--fi-enable`
    - `--fi-debug`
    - `--fi-trace`
    - `--fi-seed=<seed:int>`
    - `--fi-spec=<spec:string>`

  - `${SPIKE}/riscv/riscv.mk.in`
    adds additional files to the build system.
  - `${SPIKE}/riscv/cfg.{h,cc}`
    defines some additional (global) state for configuration.
  - `${SPIKE}/riscv/processor.h`
    defines some additional  (local) state, i.e., the step counter.
  - `${SPIKE}/riscv/insn_template.{h,cc}`
    is altered (using the `PROLOGUE` and `EPILOGUE` macros) to insert a hook into the fault injection mechanism (if enabled) *before* execution of the semantics of a given instruction.
  - `${SPIKE}/riscv/fi_spec.{h,cc}`
    implements support for fault injection specifications.
  - `${SPIKE}/riscv/fi_impl.{h,cc}`
    implements support for fault injection        actions, i.e., the mechanism itself.

- The following types of fault specification are supported:

  1. Instruction skip (step-based):
     `<spec>` is `<step>:<prob>:<type>`
     where

     - `<step>` is an integer step value
     - `<prob>` is a floating point injection probability (so between 0.0 and 1.0)
     - `<type>` is `s`

     For example

  2. Register update (step-based):
     `<spec>` is `<step>:<prob>:<type>:<addr>:<mode>:<mask>`
     where

     - `<step>` is an integer step value
     - `<prob>` is a floating point injection probability (so between 0.0 and 1.0)
     - `<type>` is `r`
     - `<addr>` is an integer register address (so between 0 and 31)
     - `<mode>` is `0`, `1`, or `?` to updated to zero, one, or random respectively
     - `<mask>` is a hexadecimal mask, which controls the bits updated (if the i-th mask bit equal to 1, the i-th register bit is updated)

  3. Instruction skip (PC-based):
     `<spec>` is `pc:<addr>:<prob>:<type>`
     where

     - `<addr>` is a hexadecimal program counter value (e.g. `0x11aea`)
     - `<prob>` is a floating point injection probability (so between 0.0 and 1.0)
     - `<type>` is `s`

     Unlike step-based specs, which trigger at a single absolute point in
     execution, a PC-based spec triggers **every time** the given address is
     fetched, with independent probability `<prob>` on each occurrence.  This
     makes it suitable for injecting faults into loops or repeatedly-called
     functions without prior knowledge of step counts.

<!--- ==================================================================== --->

# Example

1. Build  
   example program:

   ```sh
   make --directory="${REPO_HOME}/examples/sum" build
   ```

2. Execute
   example program as is:

   ```sh
   make --directory="${REPO_HOME}/examples/sum" run
   ```

3. Execute
   example program, but make use of fault induction support:

   - enable fault induction, enable fault induction debugging,
     *and*
     enable fault induction trace to, e.g., show the step values and identify a target instruction:

     ```sh
     make --directory="${REPO_HOME}/examples/sum" run FI="--fi-enable --fi-debug --fi-trace"
     ```

   - enable fault induction, enable fault induction debugging,
     *and*
     specify a "skip instruction" fault at step 215907
     (meaning the loop terminates after 1 iteration):

     ```sh
     make --directory="${REPO_HOME}/examples/sum" run FI="--fi-enable --fi-debug --fi-spec='215907:1.0:s'"
     ```

   - enable fault induction, enable fault induction debugging,
     *and*
     specify a "set register to zero" fault at step 215903
     (meaning the 1st loop iteration adds 0 rather than 9):

     ```sh
     make --directory="${REPO_HOME}/examples/sum" run FI="--fi-enable --fi-debug --fi-spec='215903:1.0:r:14:0:FFFFFFFF'"
     ```

   - enable fault induction, enable fault induction debugging,
     *and*
     specify a "set register to random" fault at step 215903
     (meaning the 1st loop iteration adds something random rather than 9):

     ```sh
     make --directory="${REPO_HOME}/examples/sum" run FI="--fi-enable --fi-debug --fi-spec='215903:1.0:r:14:?:FFFFFFFF'"
     ```

   - enable fault induction, enable fault induction debugging,
     *and*
     specify a PC-based "skip instruction" fault at address `0x100f0`
     (the loop back-edge in `main`) with probability 0.5 per occurrence
     (meaning each time the loop back-edge is reached it is skipped with 50% probability,
     so the loop terminates early on average half the time it is hit):

     ```sh
     make --directory="${REPO_HOME}/examples/sum" run FI="--fi-enable --fi-debug --fi-spec='pc:0x100f0:0.5:s'"
     ```

     To discover candidate PC values, run with `--fi-trace` and inspect the
     output for the instruction addresses of interest, or use `objdump` on the
     compiled ELF:

     ```sh
     make --directory="${REPO_HOME}/examples/sum" build
     riscv64-unknown-elf-objdump -d "${REPO_HOME}/build/sum/test.elf" | grep -A2 "<target_function>"
     ```

<!--- ==================================================================== --->
