# LUMAX TEST_LLMS results

The configuration folders below archive the completed **FP16 x W2, ID16** Verilator runs from the optimized working LUMAX RTL. Each passed 12 TEST_LLMS matmuls plus three smoke checks: **90/90 PASS** across six configurations.

| Configuration | TEST_LLMS | Smoke checks | Active cycles, LLM only | Whole-call cycles, LLM only |
|---|---:|---:|---:|---:|
| [b16_rf8_ys1_id16](b16_rf8_ys1_id16/cycles.csv) | 12/12 PASS | 3/3 PASS | 713,171 | 714,533 |
| [b8_rf8_ys2_id16](b8_rf8_ys2_id16/cycles.csv) | 12/12 PASS | 3/3 PASS | 716,140 | 717,550 |
| [b4_rf8_ys4_id16](b4_rf8_ys4_id16/cycles.csv) | 12/12 PASS | 3/3 PASS | 791,259 | 792,645 |
| [b4_rf4_ys8_id16](b4_rf4_ys8_id16/cycles.csv) | 12/12 PASS | 3/3 PASS | 488,565 | 489,751 |
| [b8_rf4_ys4_id16](b8_rf4_ys4_id16/cycles.csv) | 12/12 PASS | 3/3 PASS | 412,706 | 414,092 |
| [b4_rf8_ys8_id16](b4_rf8_ys8_id16/cycles.csv) | 12/12 PASS | 3/3 PASS | 488,129 | 489,515 |

Each configuration folder contains `cycles.csv` and the complete `verilator.log`, copied byte-for-byte and hash-verified against the completed working run. CSVs include all 15 checks; totals above include only the twelve LLM cases.

All cycle counts here are **actual make-built Verilator/RISC-V measurements**, not Python cycle predictions. Independent host Python computes expected FP32 output words, which match exactly. Output guard checks also passed. Expected output arrays and software matmul reference calculations are not placed in the RISC-V ELF.

The LLM cases cover activation rows 4, 5 and 8, each combined with K/N pairs (224,224), (224,32), (224,1216), and (1216,224). The three sequential smoke matmuls use R/K/N=(2,17,9), (5,33,17), and (2,129,33). All designs replay the same saved W2 operand seeds/hashes.

Hardware: FP16 activations, signed INT2 weights, 16 shared DMA IDs, maximum Rin=8 and Cin=Cout=1216. All seven resource-optimization flags were enabled in the tested working RTL. This commit archives results; it does not update production Scala source or change tests.

Active cycles = Load X + product generation + combined LoadW/selection + Store O. LoadW-only and select-only diagnostics overlap and must not be added again. Whole-call core cycles are stored separately. Per-stage counts, operand seeds, source/ELF/simulator hashes and actual simulator commands remain in the CSVs/logs.

Matching edited-Gemmini Verilator results are in [Baseline_Comparison/Gemmini/verification/TEST_LLMS](../../Baseline_Comparison/Gemmini/verification/TEST_LLMS/README.md). Matching FPGA reports are in [Hardware_Estimation/LUMAX](../../Hardware_Estimation/LUMAX/README.md); all six passed the 50 MHz implementation target.

## Earlier ID8 results

The root `cycles.csv`, `verilator.log` and `model_accuracy.md` preserve the earlier ID8/W8/W4/W2 run: four LUMAX configurations, 144 LLM cases plus 60 bootstrap/gate checks. They are a separate source/configuration version and remain unchanged. `run_test_llms.py` is its configurable runner; the new ID16 batch used `run_w2_id16_comparison.py` in the working Chipyard verification directory.

Original new-run directory: `/home/kioannou/chipyard_1.13.0/generators/LUMAX/verification/W2_ID16_Comparison/run_20261113_003838_822227`.
