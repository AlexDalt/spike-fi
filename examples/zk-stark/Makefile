export GCC_PREFIX  = ${RISCV}/bin/riscv64-unknown-elf-

export GCC_FLAGS  += -march='rv32imafdc_zicsr_zifencei_zicntr' -mabi='ilp32' -DXLEN='32'

TRANSCRIPT_SIZE ?= 8
TEST_SRC        ?= test.c
export GCC_PATHS  += -I ${REPO_HOME}
export GCC_LIBS   +=

BUILD_DIR = ${REPO_HOME}/build/zk-stark

${BUILD_DIR}:
	@mkdir -p ${BUILD_DIR}

${BUILD_DIR}/test.o    : ${TEST_SRC} test.h | ${BUILD_DIR}
	@${GCC_PREFIX}gcc ${GCC_FLAGS} ${GCC_PATHS} $(addprefix -I ,${INCLUDES}) -Wall -std='gnu99' -O3 -c -o ${@} $(filter-out %.h, ${^}) ${GCC_LIBS}
${BUILD_DIR}/test.elf  : ${BUILD_DIR}/test.o
	@${GCC_PREFIX}gcc ${GCC_FLAGS} ${GCC_PATHS} $(addprefix -I ,${INCLUDES}) -Wall -std='gnu99' -O3    -o ${@}                   ${^}  ${GCC_LIBS}

${BUILD_DIR}/test.bin  : ${BUILD_DIR}/test.elf
	@${GCC_PREFIX}objcopy --output-target='binary' ${<}   ${@}
${BUILD_DIR}/test.asm  : ${BUILD_DIR}/test.elf
	@${GCC_PREFIX}objdump --disassemble-all        ${<} > ${@}
${BUILD_DIR}/test.size : ${BUILD_DIR}/test.elf
	@${GCC_PREFIX}size -G -d                       ${<} > ${@}

build : ${BUILD_DIR}/test.elf ${BUILD_DIR}/test.asm

run   : ${BUILD_DIR}/test.elf
	${RISCV}/bin/spike    --isa='rv32imafdc_zicsr_zifencei_zicntr' ${FI} ${RISCV}/riscv32-unknown-elf/bin/pk ${<} $(TRANSCRIPT_SIZE)

debug : ${BUILD_DIR}/test.elf
	${RISCV}/bin/spike -d --isa='rv32imafdc_zicsr_zifencei_zicntr' ${FI} ${RISCV}/riscv32-unknown-elf/bin/pk ${<} $(TRANSCRIPT_SIZE)

trace : ${BUILD_DIR}/test.elf
	${RISCV}/bin/spike -l --isa='rv32imafdc_zicsr_zifencei_zicntr' ${FI} ${RISCV}/riscv32-unknown-elf/bin/pk ${<} $(TRANSCRIPT_SIZE)

clean :
	@rm --force --recursive ${BUILD_DIR}
