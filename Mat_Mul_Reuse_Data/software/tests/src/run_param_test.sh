#!/bin/bash
# ------------------------------------------------------------------
# run_param_test.sh
# Automates changing RIN_MAX / CIN_MAX / COUT_MAX, building, and running in Verilator
# Usage:
#   ./run_param_test.sh <RIN_MAX> <CIN_MAX> <COUT_MAX> [debug]
# Example:
#   ./run_param_test.sh 1 9 3
#   ./run_param_test.sh 2 7 7 debug
# ------------------------------------------------------------------

set -e  # Stop if any command fails

# --- Paths ---
SRC_DIR="$HOME/Documents/Chipyard/kostis_latest/generators/Mat_Mul_Data_Reuse/software/tests/src"
SIM_DIR="$HOME/Documents/Chipyard/kostis_latest/sims/verilator"
C_FILE="$SRC_DIR/Linear-sw.c"
BINARY="$SRC_DIR/Linear-sw.riscv"    

# --- Inputs ---
if [ $# -lt 3 ] || [ $# -gt 4 ]; then
  echo "Usage: $0 <RIN_MAX> <CIN_MAX> <COUT_MAX> [debug]"
  exit 1
fi

RIN=$1
CIN=$2
COUT=$3
DEBUG_FLAG=$4

echo "-------------------------------------------"
echo ">>> Updating Linear-sw.c: RIN_MAX=$RIN, CIN_MAX=$CIN, COUT_MAX=$COUT"
echo "-------------------------------------------"

# --- Edit defines in C file ---
if grep -q "^#define[[:space:]]\+RIN_MAX" "$C_FILE"; then
  sed -i "s/^#define[[:space:]]\+RIN_MAX[[:space:]]\+[0-9]\+/#define RIN_MAX $RIN/" "$C_FILE"
else
  echo "#define RIN_MAX $RIN" >> "$C_FILE"
fi

sed -i "s/^#define[[:space:]]\+CIN_MAX[[:space:]]\+[0-9]\+/#define CIN_MAX $CIN/" "$C_FILE"
sed -i "s/^#define[[:space:]]\+COUT_MAX[[:space:]]\+[0-9]\+/#define COUT_MAX $COUT/" "$C_FILE"

# --- Rebuild the binary ---
cd "$SRC_DIR"
echo ">>> Building baremetal binary..."
./build.sh baremetal

# --- Run Verilator simulation ---
cd "$SIM_DIR"

if [ "$DEBUG_FLAG" == "debug" ]; then
  echo ">>> Running Verilator simulation in DEBUG mode..."
  make CONFIG=DataReuseRocketConfig run-binary-debug BINARY="$BINARY"
else
  echo ">>> Running Verilator simulation (no debug)..."
  make CONFIG=DataReuseRocketConfig # BINARY="$BINARY"
  ./simulator-chipyard.harness-DataReuseRocketConfig "$BINARY" # /home/riscv/Documents/Chipyard/kostis_latest/generators/Mat_Mul_Data_Reuse/software/tests/src/Linear-sw.riscv 
fi

echo "-------------------------------------------"
echo "✅ Done! Simulation completed for RIN=$RIN, CIN=$CIN, COUT=$COUT (Mode: ${DEBUG_FLAG:-normal})"
echo "-------------------------------------------"
