
# riscv64-unknown-elf-gcc -fno-common -fno-builtin-printf -specs=htif_nano.specs -c Linear-sw.c
# riscv64-unknown-elf-gcc -static -specs=htif_nano.specs Linear-sw.o -o Linear-sw.riscv 


# riscv64-unknown-linux-gnu-gcc -c Linear-sw.c -o executable.o
# riscv64-unknown-linux-gnu-gcc -static executable.o -o Linear-sw.riscv

#!/bin/bash
# =============================================
# Build script for Linear-sw.c (RISC-V)
# Usage:
#   ./build.sh baremetal   -> build for bare-metal (no OS)
#   ./build.sh linux       -> build for Linux RISC-V
# =============================================

set -e  # stop on first error
SRC="Linear-sw.c"
OUT="Linear-sw.riscv"

# Check argument
if [ "$#" -ne 1 ]; then
  echo "Usage: $0 [baremetal|linux]"
  exit 1
fi

MODE="$1"

if [ "$MODE" = "baremetal" ]; then
  echo "🔧 Building bare-metal version..."
  riscv64-unknown-elf-gcc -fno-common -fno-builtin-printf -specs=htif_nano.specs -c "$SRC" -o Linear-sw.o
  riscv64-unknown-elf-gcc -static -specs=htif_nano.specs Linear-sw.o -o "$OUT"
  echo "✅ Bare-metal build complete: $OUT"

elif [ "$MODE" = "linux" ]; then
  echo "🐧 Building Linux version..."
  riscv64-unknown-linux-gnu-gcc -c "$SRC" -o executable.o
  riscv64-unknown-linux-gnu-gcc -static executable.o -o "$OUT"
  echo "✅ Linux build complete: $OUT"

else
  echo "❌ Unknown mode: $MODE"
  echo "Usage: $0 [baremetal|linux]"
  exit 1
fi
