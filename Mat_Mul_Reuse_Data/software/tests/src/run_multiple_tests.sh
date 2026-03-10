#!/bin/bash

Cin_values=(8) # 16 32 64 128)
Cout_values=(8) # 16 32 64 128)
WBit_values=(8 4 2)

RIN=1
IN_BITS=16

PASS_COUNT=0
FAIL_COUNT=0
TOTAL_RUNS=0

# ----------- SESSION DIRECTORY -----------
BASE_LOG_DIR="Output_Log"
mkdir -p "$BASE_LOG_DIR"

SESSION_ID=1
while [ -d "$BASE_LOG_DIR/Session-$SESSION_ID" ]; do
  ((SESSION_ID++))
done

SESSION_DIR="$BASE_LOG_DIR/Session-$SESSION_ID"
mkdir -p "$SESSION_DIR"

FAIL_LOG="$SESSION_DIR/failed_runs.log"
PASS_LOG="$SESSION_DIR/passed_runs.log"

> "$FAIL_LOG"
> "$PASS_LOG"

echo "Logs stored in: $SESSION_DIR"

for CIN in "${Cin_values[@]}"
do
  for COUT in "${Cout_values[@]}"
  do
    for WBITS in "${WBit_values[@]}"
    do
      ((TOTAL_RUNS++))

      echo "--------------------------------------"
      echo "Running: RIN=$RIN CIN=$CIN COUT=$COUT IN_BITS=$IN_BITS W_BITS=$WBITS"

      OUTPUT=$(./run_param_test.sh $RIN $CIN $COUT $IN_BITS $WBITS)

      if echo "$OUTPUT" | grep -q "TEST \[PASS\]"; then
        echo "PASS"
        echo "RIN=$RIN CIN=$CIN COUT=$COUT IN_BITS=$IN_BITS W_BITS=$WBITS" >> "$PASS_LOG"
        ((PASS_COUNT++))
      else
        echo "FAIL"
        echo "RIN=$RIN CIN=$CIN COUT=$COUT IN_BITS=$IN_BITS W_BITS=$WBITS" >> "$FAIL_LOG"
        ((FAIL_COUNT++))
      fi
    done
  done
done

echo "======================================"
echo "Total Runs : $TOTAL_RUNS"
echo "PASS       : $PASS_COUNT"
echo "FAIL       : $FAIL_COUNT"
echo "======================================"

echo "Passed runs saved in : $PASS_LOG"
echo "Failed runs saved in : $FAIL_LOG"