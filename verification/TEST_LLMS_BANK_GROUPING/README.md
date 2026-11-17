# Flexible bank grouping: measured ON/OFF comparison

This is an immutable export of the completed LUMAX jobs from `/home/kioannou/chipyard_1.13.0/generators/LUMAX/verification/TEST_LLMS_BANK_GROUPING/runs/run_20261117_084821`. It is separate from the new group-scaling flags: these measurements predate group scaling and use unscaled FP16 x signed W8/W4/W2. The production source now also contains default-off group-scaling modes; use the frozen sources below to reproduce these historical results.

All TEST_LLMS cycles below are actual make-built Verilator hardware-counter measurements. The twelve shapes and saved operands are identical across ON/OFF. ON uses a model-selected runtime B/YS split within the same physical lane budget; the selection is not a proof of global optimality. OFF uses the base B/YS. Both use RF16, 16 shared DMA IDs and 8-byte beats/maximum requests. Software schedule-selection overhead is excluded from active cycles.

| Base B / maximum YS | Flag | W8 cycles | W4 cycles | W2 cycles | TEST_LLMS |
|---|---|---:|---:|---:|---|
| 5 / 4 | OFF | 1,080,676 | 653,267 | 643,321 | 36/36 PASS |
| 5 / 4 | ON | 1,055,755 | 640,989 | 579,173 | 36/36 PASS |
| 14 / 8 | OFF | 398,908 | 274,741 | 187,049 | 36/36 PASS |
| 14 / 8 | ON | 391,920 | 261,244 | 176,765 | 36/36 PASS |

| Base B / maximum YS | W8 cycles saved | W4 cycles saved | W2 cycles saved |
|---|---:|---:|---:|
| 5 / 4 | 2.31% | 1.88% | 9.97% |
| 14 / 8 | 1.75% | 4.91% | 5.50% |

## Completed Vivado attempts

Accelerator-only utilization, Vivado 2023.2, ZCU106, 50 MHz target. B5 rows are post-route; B14 rows are post-synthesis and cannot be compared as routed area. Both B14 designs exceed FPGA capacity, so neither has valid implementation power/timing. Power is a low-confidence vectorless estimate at 50 MHz; internal Fmax is routed internal-path headroom, not a verified higher operating clock.

| Base B / maximum YS | Flag | LUT | FF | BRAM tiles | DSP | Dynamic mW | Internal Fmax MHz | Result |
|---|---|---:|---:|---:|---:|---:|---:|---|
| 5 / 4 | OFF | 33552 | 6485 | 66 | 0 | 60 | 78.983 | Routed; 50 MHz met |
| 5 / 4 | ON | 56391 | 7333 | 96 | 0 | 92 | 67.096 | Routed; 50 MHz met |
| 14 / 8 | OFF | 457557 | 23530 | 149 | 0 | — | — | Capacity exceeded; synthesis only |
| 14 / 8 | ON | 667948 | 31045 | 149 | 0 | — | — | Capacity exceeded; synthesis only |

For B5, ON reduces cycles by only about 2% at W8/W4 and 10% at W2, but uses about 68% more LUTs, 45% more BRAM, and 53% higher estimated dynamic power. Keep it optional; OFF is the smaller default. B14 shows cycle gains but neither ON nor OFF fits this FPGA. No energy advantage is established by this comparison.

## Archive and reproduction

`runs/run_20261117_084821/` contains complete numerical logs/CSVs, schedules, guards/control/special checks, frozen source templates and source hashes. Completed LUMAX Vivado reports/logs are under `Hardware_Estimation/LUMAX/FLEXIBLE_BANK_GROUPING/run_20261117_084821/`. Original absolute paths and process/status values are retained as provenance, not as claims that this Git copy runs a worker.

The original comparison batch is still completing Gemmini Vivado jobs. All six Gemmini numerical jobs are complete and copied here. Queued/running Gemmini implementation outputs are deliberately not exported. The original REPORT/status snapshot may consequently say RUNNING; this README is authoritative about which artifacts are final.

`run_comparison.py` resolves the working checkout with `LUMAX_CHIPYARD` (default `/home/kioannou/chipyard_1.13.0`). It reuses that checkout’s TEST_LLMS, bank-grouping and Vivado helpers, plus `/tmp/lumax-stable-baseline-20261114/frozen` tool assemblies and existing Gemmini tests in this repository. `tested_run_comparison.py` preserves the original runner verbatim. The frozen Model.py used for schedule selection is retained; historical measurements must not be replayed with a different model/source without labelling the result as a new run.

```bash
LUMAX_CHIPYARD=/home/kioannou/chipyard_1.13.0 python3 run_comparison.py status --run runs/run_20261117_084821
# Starting another run is optional; this archive does not start or stop any job.
```
