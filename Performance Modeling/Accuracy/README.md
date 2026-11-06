# Cycle-model accuracy

The current model is [Model.py](../Model.py), version **controller-events-v4**. It includes overlapping output DMA writes and the accepted four-stage tagged selection pipeline. Older model sources and scheduling experiments remain in Git history.

## Current revision: measured scope

One make-built Chipyard 1.13.0 Verilator case was checked before accepting the tagged-column change:

- b2/RF8/Y_SLICE1, DMA IDs6, INT16 activations and W4 weights.
- X=1×32, W=32×32, O=1×32.
- The original and edited RTL ran the same RISC-V executable and random vectors.
- Both produced 32/32 exact Python matches, matched the C reference, and preserved memory guards.
- Measured active cycles decreased from **832 to 717**, a **13.82%** reduction.

The current model uses the existing default memory assumptions and nominal buffer addresses:

| Counter | Edited Verilator | V4 prediction | Absolute error |
|---|---:|---:|---:|
| Load X | 31 | 30 | 1 |
| Generate products | 80 | 80 | 0 |
| Combined Load W/Select | 558 | 558 | 0 |
| Store output | 48 | 36 | 12 |
| **Total active cycles** | **717** | **704** | **13** |
| Select alone (overlaps) | 544 | 544 | 0 |
| Load W alone (overlaps) | 408 | 408 | 0 |

The total absolute percentage error is **1.81% for this one case**. This is not an aggregate accuracy result for v4. No memory-latency constants were fitted to the measurement. Other hardware configurations and FP16 were not run for the tagged-column edit. The standalone Load W and Select counters overlap and must not be added to the four-stage total.

[Native Verilator commands, returned outputs, counters and source diff](../../verification/logs/column_pipeline/run_20261105_211028_031082.log).

The production model and comparison integration passed 25 Python tests after cleanup. These test scheduling and the comparison API; they are separate from hardware numerical verification.

## Historical reports

These reports refer to older model/controller revisions. Their accuracy percentages do not describe current v4.

| Report | Scope |
|---|---|
| [Controller model validation](Cycle_Model_Validation.md) | 2026-11-02: 144 native matmuls, 4.07% address-aware MAPE for that earlier model |
| [Validation plot](Cycle_Model_Validation.png) | Total and stage errors from that validation |
| [Saved-suite accuracy](Legacy_Suite_Accuracy.md) | 2026-10-21: older-model comparison with regression, TC suites, native 1024 and Llama2 timings |
| [HTML accuracy report](LUMAX_Cycle_Model_Accuracy.html) | Historical detailed report with plots and source provenance |

Use [the current model documentation](../README.md) for equations, parameters and execution instructions.
