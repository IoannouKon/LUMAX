#!/bin/bash

SRC_PATH="/home/riscv/Documents/Chipyard/kostis_latest/generators/Mat_Mul_Reuse_Data/software/tests/src"
SIM_PATH="/home/riscv/Documents/Chipyard/kostis_latest/sims/verilator"

cd "$SRC_PATH" || { echo "SRC_PATH not found"; exit 1; }

# Back up original file before modifications
cp Linear-sw.c Linear-sw.c.bak

OUTPUT_FILE="all_runs_output.txt"
# Clear file at the start
> "$OUTPUT_FILE"

for IN_BITS in  16  ; do
  for W_BITS in 8; do
    echo "Running with IN_BITS=$IN_BITS, W_BITS=$W_BITS" | tee -a "$OUTPUT_FILE"

    # Update defines in Linear-sw.c
    sed -i "s/#define IN_BITS.*/#define IN_BITS  $IN_BITS/" Linear-sw.c
    sed -i "s/#define W_BITS.*/#define W_BITS   $W_BITS/" Linear-sw.c

    # # Build commands
    # riscv64-unknown-elf-gcc -fno-common -fno-builtin-printf -specs=htif_nano.specs -c Linear-sw.c -o Linear-sw.o
    # riscv64-unknown-elf-gcc -static -specs=htif_nano.specs Linear-sw.o -o Linear-sw.riscv

    # Compile with hardware floating point support
    riscv64-unknown-elf-gcc -march=rv64imafdc -mabi=lp64d \
      -fno-common -fno-builtin-printf -specs=htif_nano.specs \
      -c Linear-sw.c -o Linear-sw.o

    # Link and include math library
    riscv64-unknown-elf-gcc -march=rv64imafdc -mabi=lp64d \
      -static Linear-sw.o -o Linear-sw.riscv -lm


    # Run simulator and append output
    echo "=== Output for IN_BITS=$IN_BITS, W_BITS=$W_BITS ===" >> "$OUTPUT_FILE"
    "$SIM_PATH"/simulator-chipyard.harness-DataReuseRocketConfig "$SRC_PATH"/Linear-sw.riscv >> "$OUTPUT_FILE" 2>&1
    echo -e "\n\n" >> "$OUTPUT_FILE"
  done
done
