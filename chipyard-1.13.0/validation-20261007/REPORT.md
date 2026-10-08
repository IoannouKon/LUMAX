# Chipyard 1.13.0 LUMAX smoke test — 7 October 2026

**Normal: PASS. Debug: PASS. Both match 64/64 outputs exactly.**

Built fresh normal and debug simulators for `chipyard.LUMAXROcketConfig`
after sourcing `./env.sh` in `/home/kioannou/chipyard_1.13.0`.
Tools: Verilator 5.022, RISC-V GCC 13.2.0, OpenJDK 20.0.2.

The test uses the historically passing LUMAX configuration:
N=1, K=64, M=64; input16/weight4/output32;
XS=2, YS=1, Mem_row_factor=8; SCALE=false, DEBUG=true;
64-bit DMA, two request IDs and two weight buffers.
The medium Rocket core is the one used by the October 6 verification.
The original custom medium-core overlay is retained as an older integration
file; Chipyard 1.13 uses its native Rocket configuration API.

Both runs used deterministic host-prepared vectors (seed 1). The RISC-V
program copies the vectors into real DMA buffers, runs the accelerator,
and compares every returned output with an integer dot-product reference.
This is a correctness check with the default memory model, without DRAMSim.
These runs do not establish performance benchmark numbers or FPGA operation.

- [Normal run log](normal.txt)
- [Debug run log](debug.txt)
- [Source, binary and waveform metadata](manifest.json)

Waveform: `/home/kioannou/chipyard_1.13.0/generators/LUMAX/software/tests/src/Log/smoke_20261007/debug/XS=2_YS=1_Mem_row_factor=8/RIN=1_CIN=64_COUT=64_INBITS=16_WBITS=4.vcd`
(122,314,717 bytes). The VCD contains signal declarations and
time advancement. It remains in Chipyard rather than in the source repository.

The 1.13 configuration now exposes `LUMAXROcketConfig`, with
`DataReuseRocketConfig` as a compatibility alias. The build already contained
the required LUMAX SBT dependency and project; the version-specific patch
records those additions. Existing accelerator correctness fixes were preserved
and copied into the LUMAX repository with the matching C test, RoCC header,
build scripts and vector generator. Relative log paths are resolved before
changing directories, logging errors fail the runner, and debug instruction
dumps are optional (`TRACE_INSTRUCTIONS=1`).

Reproduce:

```bash
cd /home/kioannou/chipyard_1.13.0
source /home/kioannou/miniforge3/etc/profile.d/conda.sh
source ./env.sh
cd generators/LUMAX/software/tests/src
./run_param_test.sh 1 64 64 16 4
QUIET_TEST=1 ./run_param_test.sh 1 64 64 16 4 debug
```

Pre-edit backup: `/home/kioannou/lumax-integration-backup-20261007-204037`.
The LUMAX repository's pre-edit state is its existing Git HEAD.
Changes remain local and uncommitted; no remote push was performed.
Chipyard 1.11.0 and the FPGA workspaces were not edited.
