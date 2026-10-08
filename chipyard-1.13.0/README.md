# LUMAX on Chipyard 1.13.0

**October 8 configuration sweep: 162 PASS, 18 FAIL across 180 tests.**
See the [sweep guide](CONFIG_SWEEP.md) and
[saved report](../LUMAX/software/tests/src/Log/config_sweep_20261008_020603_1120408/REPORT.md).
All 18 failures are 1x512 times 512x512. The address-width and parallel-sum-width
defects are documented in the
[investigation](../LUMAX/software/tests/src/Log/config_sweep_20261008_020603_1120408/investigation/REPORT.md)
and remain present in this snapshot.

Earlier normal and debug smoke tests passed with 64/64 exact matches;
see [the October 7 report](validation-20261007/REPORT.md).

The active Verilator workspace is `/home/kioannou/chipyard_1.13.0`.
The generator is LUMAX. Keep Chipyard 1.13's own build and Rocket sources.

## Integration

- Merge `../LUMAX/` into `generators/LUMAX/` (sources and test scripts).
- Apply `build.sbt.patch` to the Chipyard 1.13.0 build, or merge its two LUMAX additions.
- Copy this directory's `LUMAXConfigs.scala` to
  `generators/chipyard/src/main/scala/config/LUMAXConfigs.scala`.

The 1.13 configuration uses `freechips.rocketchip.rocket.WithNMedCores(1)`,
the medium Rocket core used by the successful local verification.
`DataReuseRocketConfig` remains an alias for historical commands.
The repository-root `Configs.scala`, `LUMAXConfigs.scala`, `build.sbt`,
and `fpga/` are older overlays; do not replace 1.13 files with them.

The generator retains the local October 6 fixes for packed DMA placement,
request backpressure, precision width, packed-weight selection, and signed
minimum inputs. The C test retains exact output checks and memory clobbers.
The optional checkpoint hooks in the runners apply only when the Chipyard
checkout contains `LUMAX_CHECKPOINT.txt`.

## Reproduce the smoke test

Use the historically passing case from
`../LUMAX/software/tests/src/Log/XS=2_YS=1_Mem_row_factor=8/RIN=1_CIN=64_COUT=64_INBITS=16_WBITS=4.txt`:
XS=2, YS=1, RF=8; N=1, K=64, M=64; input16, weight4, output32.
SCALE=false, DEBUG=true, DMA=64 bits, two request IDs, two weight buffers.

```bash
cd /home/kioannou/chipyard_1.13.0
# If conda is not already available:
source /home/kioannou/miniforge3/etc/profile.d/conda.sh
source ./env.sh
cd sims/verilator
make -j8 CONFIG=LUMAXROcketConfig
cd ../../generators/LUMAX/software/tests/src
./run_param_test.sh 1 64 64 16 4
# Waveform-enabled simulation:
./run_param_test.sh 1 64 64 16 4 debug
```

`env.sh` is the installed environment script. Tests now default to
`FAST_VECTORS=0`: the simulated RISC-V CPU prepares inputs and computes
the pure-C matrix multiplication reference. Every LUMAX output is compared
exactly with that CPU result. `FAST_VECTORS=1` selects the host-prepared reference
for individual cases. The configuration sweep accepts `--reference python` for
faster host preparation or `--reference riscv` for the default CPU reference.
The October 8 full sweep used Python; the October 7 smoke-test report records
the earlier host-reference runs.
The test exits nonzero on a simulator failure, logging failure, or output mismatch.
Relative `LUMAX_LOG_DIR` paths are resolved before entering the simulator directory.
Debug runs record waveforms; `TRACE_INSTRUCTIONS=1` additionally enables instruction dumps.
`QUIET_TEST=1` keeps output compact while retaining exact comparisons and the
hardware cycle record. The sweep saves total and per-stage hardware counters in
each test log and `summary.csv`. `--background` keeps a sweep running after SSH
disconnection, and `--keep-design` retains generated build artifacts when needed.

FPGA bitstream work remains in the existing older workspace. This procedure
validates Verilator integration only.
