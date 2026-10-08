# Six-configuration LUMAX correctness sweep

This snapshot records the complete October 8 run before the diagnosed RTL
width fixes: **162 PASS, 18 FAIL, no errors or timeouts, 180 completed tests**.
Each hardware configuration has 27 PASS and 3 FAIL. All failures are
1x512 times 512x512 at weight2/4/8.

- [Complete report](../LUMAX/software/tests/src/Log/config_sweep_20261008_020603_1120408/REPORT.md)
- [Per-case results and hardware cycles](../LUMAX/software/tests/src/Log/config_sweep_20261008_020603_1120408/summary.csv)
- [Console output](../LUMAX/software/tests/src/Log/config_sweep_20261008_020603_1120408.console.log)
- [Failure investigation](../LUMAX/software/tests/src/Log/config_sweep_20261008_020603_1120408/investigation/REPORT.md)
- [Evidence for both width defects](../LUMAX/software/tests/src/Log/config_sweep_20261008_020603_1120408/investigation/evidence.csv)

The October 7 partial configuration sweep is also archived separately. Existing
historical logs retain their original paths and results.

## Run after integration into Chipyard

The scripts run from the deployed generator in a working Chipyard 1.13.0
checkout. Follow [the integration guide](README.md) first; the standalone Git
repository is an overlay and does not contain Chipyard's simulator or environment.

```bash
cd /home/kioannou/chipyard_1.13.0/generators/LUMAX/software/tests/src
BUILD_JOBS=24 ./run_config_sweep.sh --background --reference python
```

The wrapper loads `env.sh`. `--background` uses `nohup` and `setsid` with console
output redirected to a file, so terminal closure and SSH disconnection do not
stop the run. It prints the absolute results directory, console path and PID.
This does not protect against a server reboot or a terminated process.

`--reference python` computes expected integer outputs on the host and embeds
the same activations and packed weights in the RISC-V executable. LUMAX RTL
runs in Verilator and every output is compared exactly. `--reference riscv`
uses the simulated Rocket CPU's pure-C reference and remains the default.
PASS requires exact agreement, successful execution, and hardware counter output.

## Configurations and shapes

The order is b=2/RF=4, b=2/RF=8, b=4/RF=4, b=4/RF=8, b=8/RF=4, b=8/RF=8.
`b` sets `x_slice`; RF sets `Mem_row_factor`. Product-memory depth n is 64*RF,
so n=256 or 512; `y_slice=1` throughout.

Every configuration runs 30 cases:

- X=1xK and W=KxK for K=4,8,16,32,64,128,256,512.
- X=1x64 and W=64x16; X=1x128 and W=128x32.
- Every shape uses activation16 and weight2/4/8, producing output32.

There are six hardware builds and 180 tests. Tests run sequentially because
they share C source and executable files. `BUILD_JOBS` controls parallel build
jobs only. The default memory model is used, without DRAMSim or waveforms.

## Results and hardware cycles

Each configuration retains its Scala snapshot, build log and per-case logs.
`summary.csv` records dimensions, precision, status, exact matches, exit status,
log path and these hardware counter columns:

- `hw_cycles`: Load X + Generate BRAMs + combined Load W/Select + Store O.
- `hw_load_x_cycles`, `hw_generate_brams_cycles`, `hw_store_o_cycles`.
- `hw_load_w_and_select_cycles`, `hw_load_w_cycles`, `hw_select_accumulate_cycles`.

The individual Load W and Select counters overlap; do not add them again to
the combined counter. `seconds` records host elapsed time. Failed or timed-out
runs that never report counters have empty cycle cells. Failing-case counters
are diagnostic measurements, not validated performance results.

The live sweep retains simulator executables for resume, but removes
`generated-src` after each build by default. Add `--keep-design` to retain it.
The Git archive includes text logs, manifests, checksums, configuration/source
snapshots and investigation artifacts. It excludes simulator binaries, object
files, test executables, generated build trees and stale PID files. Archived
paths and source hashes retain their original run provenance; the archived
investigation reproducer expects the original deployed Chipyard layout.

## Monitor and resume

For the recorded October 8 run, these commands work from the deployed test
directory:

```bash
RUN="$PWD/Log/config_sweep_20261008_020603_1120408"
tail -f "$RUN.console.log"
cat "$RUN/REPORT.md"
```

For a new run, use the results path printed by its launcher. Ctrl-C while
watching `tail` stops the viewer and leaves the background sweep running.

If an unfinished process has stopped and its source/configuration/test flags
are unchanged, resume with its existing result path:

```bash
BUILD_JOBS=24 ./run_config_sweep.sh --background --reference python --resume --output "$RUN"
```

Resume reuses matching simulator binaries and passing cases, retrying the
remaining cases. The Git text archive alone lacks those simulator binaries;
builds will be needed if running from an archive. After RTL changes, use a new
result directory because source hashes no longer match the old manifest.

Each case defaults to a 3600-second wall-clock timeout and a 1,000,000,000-cycle
limit, configurable with `TIMEOUT_SECONDS` and `TIMEOUT_CYCLES`. A handled
interruption restores the original Scala configuration and test files.
