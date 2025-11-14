#!/bin/bash
# ------------------------------------------------------------------
# run_all_tests_with_pass_count.sh
# Automatically runs CIN_MAX = COUT_MAX tests from 2x2 → 32x32
# Counts how many simulations passed (look for "PASS (All elements)")
#
# Usage:
#   ./run_all_tests_with_pass_count.sh [debug]
# Example:
#   ./run_all_tests_with_pass_count.sh       # normal mode
#   ./run_all_tests_with_pass_count.sh debug # debug mode
# ------------------------------------------------------------------

set -e  # Stop if any command fails

# --- Paths ---
SRC_DIR="$HOME/Documents/Chipyard/kostis_latest/generators/Mat_Mul_Data_Reuse/software/tests/src"
SINGLE_TEST_SCRIPT="$SRC_DIR/run_param_test.sh"

DEBUG_FLAG=$1  # optional argument

PASS_COUNT=0
TOTAL_TESTS=0

# --- Info banner ---
echo "==========================================="
echo ">>> Starting full matrix test sweep"
if [ "$DEBUG_FLAG" == "debug" ]; then
    echo ">>> Mode: DEBUG (run-binary-debug enabled)"
else
    echo ">>> Mode: NORMAL"
fi
echo "==========================================="

# --- Loop over square matrix sizes ---
Rin=1 
for N in $(seq 16 64); do
    echo "-------------------------------------------"
    echo ">>> Running test for RIN=$Rin, CIN=$N, COUT=$N"
    echo "-------------------------------------------"

    # Run the single test and capture output (pass debug flag if provided)
    if [ "$DEBUG_FLAG" == "debug" ]; then
        TEST_OUTPUT=$($SINGLE_TEST_SCRIPT $Rin $N $N debug 2>&1)
    else
        TEST_OUTPUT=$($SINGLE_TEST_SCRIPT $Rin $N $N 2>&1)
    fi

    echo "$TEST_OUTPUT"  # Print simulation output

    TOTAL_TESTS=$((TOTAL_TESTS + 1))

    # Check if PASS string exists
    if echo "$TEST_OUTPUT" | grep -q "PASS (All elements)"; then
        echo ">>> Test PASSED for N=$N ✅"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        echo ">>> Test FAILED for N=$N ❌"
    fi

    echo ""
done

# --- Summary ---
echo "==========================================="
echo "✅ All tests completed!"
echo "Mode: ${DEBUG_FLAG:-normal}"
echo "Total tests run: $TOTAL_TESTS"
echo "Total tests passed: $PASS_COUNT"
echo "Total tests failed: $((TOTAL_TESTS - PASS_COUNT))"
echo "==========================================="
