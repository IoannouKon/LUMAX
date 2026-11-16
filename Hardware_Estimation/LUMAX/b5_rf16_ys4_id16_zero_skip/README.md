# B5/RF16/YS4/ID16: zero-weight skipping FPGA comparison

Both implementations completed placement/routing and met **50 MHz** on ZCU106 (Vivado 2023.2). They use the same Rocket/platform, FP16 activation mode, Rin8/Cin1216/Cout1216 capacities and tested source; only SKIP_ZERO_WEIGHTS changes. The single accelerator supports signed W8/W4/W2, so these are two implementations, not separate per-precision designs.

| Accelerator metric | Flag off | Flag on |
|---|---:|---:|
| LUTs | 33,528 | 43,052 |
| Registers | 6,485 | 8,871 |
| BRAM tiles | 66 | 66 |
| DSPs | 0 | 0 |
| Estimated dynamic power | 59 mW | 122 mW |
| Estimated internal Fmax | 79.340 MHz | 80.451 MHz |

Resource values refer to the accelerator hierarchy. Full-system reports include Rocket, memory controller and FPGA shell. Power is a vectorless estimate, not Qwen-workload activity or measured board power. Internal Fmax is same-clock routed path headroom, not an implementation verified at a higher system clock. Both actual implementations target 50 MHz.

comparison.csv contains machine-readable metrics. Each flag folder contains the Vivado log, version/driver/state/provenance logs, and reports for accelerator/system utilization, SRAM allocation, power, clocks, setup/hold paths, boundary/internal timing and routing. Absolute original work paths in historical logs/state are preserved as provenance; all generated reports needed for analysis are archived here.

[Verilator tests, exact tested RTL/software snapshots, dataset and runner](../../../verification/TEST_QWEN_SPARSITY/README.md). W2 reduced cycles 47.95%, while W8/W4 regressed; LUTs increased28.4% and registers36.8%. Keep the option workload-dependent. No area/power claim is made for the later naming-only source cleanups beyond the checked RTL equivalence described separately.
