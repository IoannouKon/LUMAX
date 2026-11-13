# Optimized LUMAX — Vivado hardware results

Eight completed implementations using Vivado 2023.2, Chipyard 1.13.0 and ZCU106 (`xczu7ev-ffvc1156-2-e`). All routed and passed setup/hold timing at the **50 MHz target**. FP16 activations; capacities Rin=8 and Cin=Cout=1216; all seven resource optimizations enabled, including the compact validity-count width fix.

| Configuration | LUTs | Registers | BRAM tiles | DSPs | Accelerator dynamic power | Internal Fmax estimate |
|---|---:|---:|---:|---:|---:|---:|
| [b16_rf8_ys1_id8](b16_rf8_ys1_id8/configuration.json) | 39,113 | 5,463 | 60 | 0 | 72 mW | 69.132 MHz |
| [b8_rf8_ys2_id8](b8_rf8_ys2_id8/configuration.json) | 29,614 | 5,221 | 48 | 0 | 46 mW | 73.904 MHz |
| [b16_rf8_ys1_id16](b16_rf8_ys1_id16/configuration.json) | 39,235 | 6,011 | 60 | 0 | 56 mW | 66.304 MHz |
| [b8_rf8_ys2_id16](b8_rf8_ys2_id16/configuration.json) | 30,644 | 5,741 | 48 | 0 | 48 mW | 73.035 MHz |
| [b4_rf8_ys4_id16](b4_rf8_ys4_id16/configuration.json) | 24,350 | 5,711 | 48 | 0 | 54 mW | 75.403 MHz |
| [b4_rf4_ys8_id16](b4_rf4_ys8_id16/configuration.json) | 58,860 | 8,573 | 48 | 0 | 70 mW | 72.616 MHz |
| [b8_rf4_ys4_id16](b8_rf4_ys4_id16/configuration.json) | 62,278 | 8,385 | 32 | 0 | 62 mW | 68.752 MHz |
| [b4_rf8_ys8_id16](b4_rf8_ys8_id16/configuration.json) | 54,048 | 8,685 | 88 | 0 | 85 mW | 73.206 MHz |

Each configuration directory contains original Vivado/Chipyard logs, tool version and `reports/`, plus:

- `configuration.json`: effective B/RF/YS/ID, matrix capacities, activation format, target clock and optimization flags.
- `Config.scala`: exact frozen base configuration template. Its default B/RF/YS/ID/capacities are overridden by the FPGA configuration/environment; use configuration.json for the effective implemented values.
- `result.json`: collected metrics, original result location and SHA256 hashes of copied logs/reports.

Raw reports/logs and the base Scala template were copied byte-for-byte and hash-verified. `summary.csv` provides machine-readable metrics, including full-system power and timing slacks.

Table resources and power describe the accelerator hierarchy. Full-system reports additionally cover Rocket, memory and the FPGA shell. Power is a **Low-confidence vectorless estimate at 50 MHz**, not measured board power or workload energy. Internal Fmax estimates come from the routed internal critical path; they are not separately verified whole-system operating frequencies.

LUMAX hardware supports W8/W4/W2. The ID8 follow-up verification covered all three precisions. The ID16 TEST_LLMS batch exercised W2 only; the resource estimates describe the same multi-precision-capable LUMAX RTL.

This folder archives Vivado results only. It does not update the repository production Scala files or archive new TEST_LLMS cycle logs. Earlier unsuccessful/older implementation versions remain in ../logs/ for historical comparison.

Use the shared [hardware flow instructions](../README.md) and runners in the parent Hardware_Estimation folder. Matching Gemmini reports are in [Baseline_Comparison/Gemmini/Hardware_Estimation](../../Baseline_Comparison/Gemmini/Hardware_Estimation/README.md).
