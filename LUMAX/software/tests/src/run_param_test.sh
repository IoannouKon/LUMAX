#!/bin/bash
# ------------------------------------------------------------------
# run_param_test.sh   (PORTABLE VERSION)
# Works on any machine as long as Chipyard directory structure
# remains the same. All paths auto-detected from script location.
# ------------------------------------------------------------------

set -e

# ----------- BASE PATHS (auto-detected) ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHIPYARD_DIR="$(realpath "$SCRIPT_DIR/../../../../..")"

GEN_DIR="$CHIPYARD_DIR/generators/LUMAX"
SRC_DIR="$GEN_DIR/software/tests/src"
SIM_DIR="$CHIPYARD_DIR/sims/verilator"
SCALA_CFG="$GEN_DIR/src/main/scala/Config.scala"

C_FILE="$SRC_DIR/Linear-sw.c"
BINARY="$SRC_DIR/Linear-sw.riscv"

# ----------- INPUTS -----------
if [ $# -lt 3 ] || [ $# -gt 6 ]; then
  echo "Usage: $0 <RIN_MAX> <CIN_MAX> <COUT_MAX> [IN_BITS] [W_BITS] [debug]"
  echo "  IN_BITS allowed values: 16, 8 (default:16)"
  echo "  W_BITS allowed values: 8, 4, 2 (default:8)"
  exit 1
fi

RIN=$1
CIN=$2
COUT=$3

# Optional parameters with defaults
IN_BITS=${4:-16}
W_BITS=${5:-8}
DEBUG_FLAG=$6

# ----------- VALIDATE OPTIONAL PARAMETERS -----------
if [[ "$IN_BITS" != "16" && "$IN_BITS" != "8" ]]; then
  echo "❌ ERROR: IN_BITS must be 16 or 8"
  exit 1
fi

if [[ "$W_BITS" != "8" && "$W_BITS" != "4" && "$W_BITS" != "2" ]]; then
  echo "❌ ERROR: W_BITS must be 8, 4, or 2"
  exit 1
fi

# ----------- SAFE EXTRACTION FROM SCALA FILE -----------
extract_number() {
  grep -E "$1[[:space:]]*=" "$SCALA_CFG" | sed -E "s/.*$1[[:space:]]*=[[:space:]]*([0-9]+).*/\1/"
}

XS=$(extract_number "x_slice")
YS=$(extract_number "y_slice")
RF=$(extract_number "Mem_row_factor")

extract_number() {
  grep -E "^[[:space:]]*$1[[:space:]]*=[[:space:]]*[0-9]+" "$SCALA_CFG" \
  | head -n1 \
  | sed -E "s/.*=[[:space:]]*([0-9]+).*/\1/"
}

if [[ -z "$XS" || -z "$YS" || -z "$RF" ]]; then
  echo "❌ ERROR: Missing HW parameters:"

  [[ -z "$XS" ]] && echo "  - x_slice not found or empty"
  [[ -z "$YS" ]] && echo "  - y_slice not found or empty"
  [[ -z "$RF" ]] && echo "  - Mem_row_factor not found or empty"

  exit 1
fi

# ----------- UPDATE C DEFINES -----------
update_define() {
  local name=$1
  local value=$2
  local file=$3

  if grep -q "^#define $name" "$file"; then
    sed -i "s/^#define $name.*/#define $name $value/" "$file"
  else
    echo "#define $name $value" >> "$file"
  fi
}

update_define "RIN_MAX"  "$RIN"  "$C_FILE"
update_define "CIN_MAX"  "$CIN"  "$C_FILE"
update_define "COUT_MAX" "$COUT" "$C_FILE"

update_define "XS" "$XS" "$C_FILE"
update_define "YS" "$YS" "$C_FILE"
update_define "RF" "$RF" "$C_FILE"

# BITWIDTH of elements from optional arguments
update_define "IN_BITS" "$IN_BITS" "$C_FILE"
update_define "W_BITS" "$W_BITS" "$C_FILE"

# ----------- BUILD SOFTWARE -----------
cd "$SRC_DIR"
./build.sh baremetal

# ----------- PREPARE LOG FILE -----------
BASE_LOG_DIR="$SRC_DIR/Log"
SUB_LOG_DIR="$BASE_LOG_DIR/XS=${XS}_YS=${YS}_RF=${RF}"
mkdir -p "$SUB_LOG_DIR"
LOG_FILE="$SUB_LOG_DIR/RIN=${RIN}_CIN=${CIN}_COUT=${COUT}_INBITS=${IN_BITS}_WBITS=${W_BITS}.txt"

# ----------- RUN VERILATOR -----------
cd "$SIM_DIR"

echo "-------------------------------------------"
if [ "$DEBUG_FLAG" == "debug" ]; then
  echo ">>> Running Verilator simulation (DEBUG)..."
  make CONFIG=DataReuseRocketConfig run-binary-debug BINARY="$BINARY" | tee "$LOG_FILE"
else
  echo ">>> Running Verilator simulation..."
  make CONFIG=DataReuseRocketConfig | tee "$LOG_FILE"
  ./simulator-chipyard.harness-DataReuseRocketConfig "$BINARY" | tee -a "$LOG_FILE"
fi
echo "-------------------------------------------"

echo "Log saved to $LOG_FILE"

# ----------- CHECK TEST RESULT -----------
if grep -q "PASS" "$LOG_FILE"; then
  echo "TEST [PASS]" | tee -a "$LOG_FILE"
else
  echo "TEST [FAIL]" | tee -a "$LOG_FILE"
fi

# echo "DONE (Mode: ${DEBUG_FLAG:-normal})"
