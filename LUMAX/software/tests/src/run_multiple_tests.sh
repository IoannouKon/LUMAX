#!/bin/bash

set -e

SCRIPT="./run_param_test.sh"

RIN=1
SIZES=(32 64 128 256)
W_BITS_LIST=(8 4 2)
IN_BITS=16

for size in "${SIZES[@]}"; do
  CIN=$size
  COUT=$size

  for W_BITS in "${W_BITS_LIST[@]}"; do
    echo "----------------------------------------"
    echo "Running: RIN=$RIN CIN=$CIN COUT=$COUT IN_BITS=$IN_BITS W_BITS=$W_BITS"
    echo "----------------------------------------"

    $SCRIPT $RIN $CIN $COUT $IN_BITS $W_BITS
  done
done

echo "All runs completed."