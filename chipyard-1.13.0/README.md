# LUMAX on Chipyard 1.13.0

**Final validation: 180 PASS, 0 FAIL.** The 18 previously failing cases passed first, followed by the remaining 162 tests.

[Final report](../LUMAX/software/tests/src/Log/run_20261008_193240/total_results.md) · [Per-test results](../LUMAX/software/tests/src/Log/run_20261008_193240/total_results.csv).

The hardware includes a 20-bit flattened matrix index and widening to 32 bits before parallel signed reduction. The standalone overlay and deployed generator contain the same corrected RTL.

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


## Reproduce the smoke test

Use the validated configuration: XS=2, YS=1, RF=8; N=1, K=64, M=64; input16, weight4, output32.
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
The final validation used Python for the reference; every accelerator output was produced by actual Verilator simulation.
The test exits nonzero on a simulator failure, logging failure, or output mismatch.
Relative `LUMAX_LOG_DIR` paths are resolved before entering the simulator directory.
Debug runs record waveforms; `TRACE_INSTRUCTIONS=1` additionally enables instruction dumps.
`QUIET_TEST=1` keeps output compact while retaining exact comparisons and the
hardware cycle record. The sweep saves total and per-stage hardware counters in
each test log and `total_results.csv`. `--background` keeps a sweep running after SSH
disconnection, and `--keep-design` retains generated build artifacts when needed.

FPGA bitstream work remains in the existing older workspace. This procedure
validates Verilator integration only.

## Six-configuration regression

```bash
cd /home/kioannou/chipyard_1.13.0/generators/LUMAX/software/tests/src
BUILD_JOBS=24 ./run_config_sweep.sh --background --reference python
```

This command builds a separate design with make for each b=2,4,8 and RF=4,8 pair, then runs 30 cases per design. The retained final validation reused six verified binaries built earlier with make; those binaries and build logs remain in the deployed final result directory under each configuration’s design/ directory. Python prepares the reference, while the compiled Verilator binary executes the RISC-V test.
