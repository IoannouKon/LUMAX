#!/bin/bash

# ============================================================
# Usage:
#
#   ./run_multiple_tests.sh
#       -> Runs all tests normally
#
#   ./run_multiple_tests.sh on
#       -> Skips a test immediately if its output file exists
#
# The "on" argument enables "skip existing results" mode.
# Useful for resuming interrupted sweeps.
# ============================================================

# Exit immediately if a command exits with a non-zero status
set -e

# Optional flag:
# If "on" is provided, existing results will be skipped.
SKIP_EXISTING=${1:-off}

# Path to your execution script
SCRIPT="./run_param_test.sh"

# Define triplets in "RIN,CIN,COUT" format
TRIPLETS=(
  "1,4,4"
  "1,8,8"
  # "1,16,16"
  # "1,32,32"
  # "1,64,64"
  # "1,128,128"
  # "1,256,256"
  # "1,512,512"
  # "1,1024,1024"
  # "1,2048,2048"
)

# Other parameters
W_BITS_LIST=(8 4 2)
IN_BITS=16

# Loop through each triplet
for item in "${TRIPLETS[@]}"; do

  # Split "RIN,CIN,COUT"
  IFS=',' read -r RIN CIN COUT <<< "$item"

  # Loop through weight bit widths
  for W_BITS in "${W_BITS_LIST[@]}"; do

    # Expected output file
    OUTPUT_FILE="results_R${RIN}_CIN${CIN}_COUT${COUT}_IN${IN_BITS}_W${W_BITS}.txt"

    # FIRST THING: skip immediately
    if [[ "$SKIP_EXISTING" == "on" && -f "$OUTPUT_FILE" ]]; then
      echo "Skipping existing file: $OUTPUT_FILE"
      continue
    fi

    echo "------------------------------------------------"
    echo "Running: RIN=$RIN CIN=$CIN COUT=$COUT IN_BITS=$IN_BITS W_BITS=$W_BITS"
    echo "------------------------------------------------"

    # Execute test
    $SCRIPT "$RIN" "$CIN" "$COUT" "$IN_BITS" "$W_BITS"

  done
done

echo "All runs completed successfully."