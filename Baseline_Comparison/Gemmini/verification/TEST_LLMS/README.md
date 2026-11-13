# Gemmini TEST_LLMS — results by configuration

These runs use the **same edited Gemmini FP16 x integer implementation**. The differences are array dimensions, scratchpad/accumulator capacities, DMA IDs and tested weight precision. No new Gemmini RTL changes are part of this organization.

All numbers below are actual make-built Verilator controller-active cycles, summed over the same twelve TEST_LLMS matrix shapes per weight precision. A dash means that precision was not run for that configuration.

| Configuration | Array | SP / ACC KiB | IDs per read/write pool | W8 cycles | W4 cycles | W2 cycles |
|---|---|---:|---:|---:|---:|---:|
| [d4_sp32_acc4_id8](d4_sp32_acc4_id8/cycles.csv) | 4x4 | 32 / 4 | 8 | 953,112 | 948,770 | 948,880 |
| [d4_sp32_acc4_id16](d4_sp32_acc4_id16/cycles.csv) | 4x4 | 32 / 4 | 16 | — | — | 948,262 |
| [d8_sp32_acc4_id16](d8_sp32_acc4_id16/cycles.csv) | 8x8 | 32 / 4 | 16 | — | — | 332,939 |
| [d8_sp64_acc16_id16](d8_sp64_acc16_id16/cycles.csv) | 8x8 | 64 / 16 | 16 | — | — | 345,685 |

Archived results: **72/72 TEST_LLMS matmuls PASS**, plus **9/9 smoke checks PASS**.

```text
TEST_LLMS/
├── README.md
├── run_test_llms.py
├── d4_sp32_acc4_id8/
│   ├── cycles.csv
│   └── verilator.log
├── d4_sp32_acc4_id16/
│   ├── cycles.csv
│   └── verilator.log
├── d8_sp32_acc4_id16/
│   ├── cycles.csv
│   └── verilator.log
└── d8_sp64_acc16_id16/
    ├── cycles.csv
    └── verilator.log
```

Each folder contains the complete original Verilator log and CSV, preserved byte-for-byte. The ID8 data is from 2026-11-08 and covers W8/W4/W2. The ID16 data is from the completed 2026-11-13 batch and covers W2 only. Each ID16 CSV includes three smoke checks; the cycle totals above exclude them.

X[R,K] times W[K,N]: rows R=4,5,8, each combined with K/N pairs (224,224), (224,32), (224,1216) and (1216,224). These are Qwen2.5-inspired independent projections at reduced dimensions, not full-model inference. The ID16 smoke shapes are (2,17,9), (5,33,17) and (2,129,33).

Shared settings: FP16 activations, signed integer weights, FP32 output/accumulation, weight-stationary execution, 64-bit DMA, 64-byte maximum transactions, four scratchpad banks, tile latency 2, scaling and bias input disabled. ID counts are per read/write pool, not a shared pool.

The CSVs include whole-call core cycles, exclusive controller events, overlapping load/execute/store busy counters, guards, per-run provenance hashes and operand seeds. Do not add overlapping busy counts. Logs preserve actual simulator commands, output values and numerical errors.

Independent references execute only in host Python; expected outputs are not embedded in the RISC-V ELF. Numerical PASS uses the predeclared FP32 forward error bound and intact guards. It does not mean every output is bit-identical to an exact-wide dot product, and tolerances were not relaxed after observing results.

To run another configuration, edit the settings at the top of `run_test_llms.py`, then use `python3 run_test_llms.py check` and `python3 run_test_llms.py start`. Use `status` or `stop` afterward. New runs are saved under the matching configuration folder in `runs/run_DATE/`, preserving these archived measurements. The runner uses the prepared Chipyard 1.13.0 checkout and sibling Gemmini source/software assets.

Original working result directories:

- ID8: `/home/kioannou/chipyard_1.13.0/generators/LUMAX/verification/TEST_LLMS/runs/run_20261108_131221_994034`.
- ID16: `/home/kioannou/chipyard_1.13.0/generators/LUMAX/verification/W2_ID16_Comparison/run_20261113_003838_822227/gemmini_*`.

Vivado results are separate from this cycle-results folder.
