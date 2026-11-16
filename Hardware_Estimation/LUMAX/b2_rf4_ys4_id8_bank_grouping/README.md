# Runtime bank grouping: paired ZCU106 implementation

Same eight-lane LUMAX, RF4, maximum YS4, ID8 and matrix capacities R9/K129/N65. Only `FLEXIBLE_BANK_GROUPING` changes. Fixed B2/YS4 versus optional B8/YS1, B4/YS2, B2/YS4. Both full Rocket/platform designs completed routing and met **50 MHz**, using Vivado 2023.2 and two threads. One multi-precision accelerator supports FP16 activations and W8/W4/W2; there are two implementations, not six per-precision implementations.

| Accelerator metric | Flag off | Flag on |
|---|---:|---:|
| LUTs | 13,543 | 22,692 |
| Registers | 3,592 | 3,867 |
| BRAM tiles | 4 | 4 |
| DSPs | 0 | 0 |
| Estimated dynamic power | 33 mW | 33 mW |
| Estimated internal Fmax | 87.719 MHz | 80.645 MHz |
| Implementation seconds | 2,694.1 | 2,834.7 |

LUTs increased 67.56%, registers 7.66%; estimated internal Fmax decreased 8.06%. These utilization/power values refer to the accelerator hierarchy. The archived reports also include full-system utilization/power and setup/hold timing, clocks, SRAM allocation and routing. Both actual implemented clocks are 50 MHz. Internal Fmax is same-clock routed path headroom, not a verified higher-clock system implementation. Power is low-confidence vectorless estimation; equal rounded estimates do not establish equal workload power or energy.

Each flag folder includes original Vivado/elaboration/driver/version/state logs, full reports, and exact source snapshots. Historical absolute work paths in logs/state are preserved. Generated checkpoints, vendor build directories, jars and simulator binaries are excluded.

[Verilator results, model selection, exact tested sources and runner](../../../verification/TEST_BANK_GROUPING/README.md). All 72 numerical checks passed; aggregate active-cycle speedups were W8 1.140x, W4 1.063x and W2 1.079x. These exclude CPU selection/preload costs. Keep the option disabled by default because the cost/benefit depends on workload.
