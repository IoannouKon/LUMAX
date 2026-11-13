# Edited Gemmini — Vivado hardware results

Post-route results from **Vivado 2023.2**, Chipyard 1.13.0, ZCU106 (`xczu7ev-ffvc1156-2-e`). All six saved implementations routed successfully and met setup/hold timing at the **50 MHz target**.

The same edited Gemmini FP16 x integer implementation is used throughout. Array dimensions, memory capacities, DMA IDs and static weight precision differ; these results do not represent new RTL modifications.

| Configuration / precision | LUTs | Registers | BRAM tiles | DSPs | Accelerator dynamic power | Internal Fmax estimate |
|---|---:|---:|---:|---:|---:|---:|
| [d4_sp32_acc4_id8/w8](d4_sp32_acc4_id8/w8/result.json) | 45,504 | 22,800 | 16 | 167 | 265 mW | 64.008 MHz |
| [d4_sp32_acc4_id8/w4](d4_sp32_acc4_id8/w4/result.json) | 45,647 | 22,843 | 16 | 152 | 268 mW | 66.774 MHz |
| [d4_sp32_acc4_id8/w2](d4_sp32_acc4_id8/w2/result.json) | 44,720 | 22,850 | 16 | 152 | 251 mW | 67.231 MHz |
| [d4_sp32_acc4_id16/w2](d4_sp32_acc4_id16/w2/result.json) | 45,114 | 23,498 | 16 | 152 | 250 mW | 67.286 MHz |
| [d8_sp32_acc4_id16/w2](d8_sp32_acc4_id16/w2/result.json) | 91,924 | 36,186 | 32 | 159 | 875 mW | 67.123 MHz |
| [d8_sp64_acc16_id16/w2](d8_sp64_acc16_id16/w2/result.json) | 95,539 | 36,488 | 32 | 159 | 892 mW | 63.440 MHz |

```text
Hardware_Estimation/
├── README.md
├── summary.csv
├── d4_sp32_acc4_id8/
│   ├── w8/
│   ├── w4/
│   └── w2/
├── d4_sp32_acc4_id16/
│   └── w2/
├── d8_sp32_acc4_id16/
│   └── w2/
└── d8_sp64_acc16_id16/
    └── w2/
```

Every precision folder contains the complete `vivado.log`, `vivado_version.txt`, Chipyard/elaboration/driver logs, generated `configuration.scala`, and `reports/`. `result.json` records the configuration, measured/estimated metrics, source location and SHA256 hashes of copied files. Original report/log contents are unchanged.

Resources and dynamic power in the table cover the accelerator hierarchy. Full-system utilization, timing and power reports also include Rocket, the memory subsystem and FPGA shell. `summary.csv` retains both accelerator dynamic power and full-system power estimates.

Power was estimated at **50 MHz**, with **vectorless activity and Low confidence**. It is not a measured board value or workload-specific energy measurement. Internal Fmax is inferred from the routed same-clock internal setup path; it is not a verified whole-system operating clock. Only the 50 MHz implementation target has passed here.

Shared settings: FP16 activations, signed integer weights, FP32 output/accumulation, weight-stationary execution, four scratchpad banks, 64-bit DMA, 64-byte maximum transactions and tile latency 2; scale units and bias input disabled. DMA IDs are per read/write pool. Each static weight precision has its own hardware build.

The ID8 4x4 configurations cover W8/W4/W2. The new ID16 configurations cover **W2 only**; their W8/W4 hardware results do not exist.

Matching numerical and cycle results are in [verification/TEST_LLMS](../verification/TEST_LLMS/README.md). The shared [Vivado flow instructions](../../../Hardware_Estimation/README.md) describe Chipyard RTL generation followed by Vivado synthesis, placement, routing and reporting.

Detailed reports include `gemmini_utilization.rpt`, `system_utilization.rpt`, `gemmini_internal_timing.rpt`, `gemmini_boundary_timing.rpt`, `system_timing.rpt`, `power.rpt`, RAM utilization and routing status. Synthesis utilization is retained separately, so resource comparisons can use the same implementation stage.
