# LUMAX Verilator correctness tests

Historical verification: **21/21 requested cases PASS**, plus **7/7 extra regressions PASS**.
Old logs were cleared on 2026-10-07; these are historical results, not results of a new run.

Hardware: Chipyard 1.13.0, `DataReuseRocketConfig`, XS=2, YS=1, Mem_row_factor=8.
The requested sweep uses RIN=1, CIN=COUT=K, K=4,8,16,32,64,128,256,
IN_BITS=16, and W_BITS=2,4,8 (21 cases).

## Run the entire sweep

```bash
cd /home/kioannou/chipyard_1.13.0
source env.sh
cd generators/LUMAX/software/tests/src
./run_multiple_tests.sh
```

The runner rebuilds missing or stale hardware once, compiles each test with -O2,
runs without waveforms or instruction dumps, checks every accelerator output
against an integer reference, and exits nonzero if any test fails. By default,
Python prepares the input/packed weight arrays and reference on the host, using
the same newlib random sequence as the original C test. The arrays are embedded
in the test ELF and copied into the DMA buffers on Rocket. `FAST_VECTORS=0`
restores random generation, transpose and reference multiplication on Rocket.
The host preparation was cross-checked against the original C routines in
31 cases, including all requested sizes and precisions. Logs and summary.csv
are saved under `Log/sweep_<timestamp>/`. Cases run sequentially because the
single-test runner updates the shared Linear-sw.c parameters.

## Six-configuration sweep in the background

`run_config_sweep.sh` builds b=2,4,8 with RF=4,8 (n=64*RF), then runs
30 exact-output tests per configuration, for 180 tests total. The default
reference is pure C on the simulated Rocket CPU. `--reference python` prepares
the same activations, packed weights and expected integer outputs on the host,
avoiding simulated CPU reference multiplication. Every output is still checked
against LUMAX RTL running in Verilator. Tests remain sequential because they
share the C source and ELF; `BUILD_JOBS` only parallelizes simulator compilation.

By default each configuration's `generated-src` directory is removed after the
build, including partial artifacts on a handled interruption. Add `--keep-design`
to retain generated RTL and build files. Simulator binaries and checksums,
Scala configuration snapshots, build logs, per-case runner and simulator logs,
the manifest, summary CSV and report are always retained. Full matrix dumps and
waveforms are not enabled. The generated vector header and test ELF are not
archived separately for every case.

Quiet logs retain PASS/FAIL, exact matches, mismatch details, and one
`HW_CYCLES` record per completed test. The same hardware counters are saved as
`hw_cycles`, `hw_load_x_cycles`, `hw_generate_brams_cycles`, `hw_load_w_cycles`,
`hw_select_accumulate_cycles`, `hw_store_o_cycles`, and
`hw_load_w_and_select_cycles` in `summary.csv`. `hw_cycles` is the sum of the
hardware Load X, Generate BRAMs, combined Load W/Select, and Store O counters;
it is not CPU reference time, CPU `rdcycle` timing, or host wall-clock time.
Load W alone and Select alone describe overlapping activity and must not be
added again to the combined counter. If simulation fails before reporting
counters, the corresponding CSV cells are empty, not zero. A would-be passing
test without its counters is reported as ERROR. Software timings remain hidden.

Start a new fast correctness sweep detached from the SSH terminal:

```bash
cd /home/kioannou/chipyard_1.13.0/generators/LUMAX/software/tests/src
BUILD_JOBS=24 ./run_config_sweep.sh --background --reference python
```

Add `--keep-design` to the launch command if generated design files are wanted.
`--background` uses `nohup` and `setsid`, redirects stdin away from the terminal,
and saves console output and a launcher PID in sibling `.console.log` and `.pid`
files. The printed result path is absolute. An explicit relative `--output` path
is resolved against the caller's working directory, not the Chipyard root.
This mode allows the sweep to continue after SSH disconnection or terminal
closure; it does not restart it after a reboot or fatal failure. Foreground mode
without `--background` is still available and is not disconnect-safe.

After reconnecting, set `RUN` to the printed result directory:

```bash
RUN="/absolute/path/printed/by/the/launcher"
tail -f "$RUN.console.log"
cat "$RUN/REPORT.md"
```

A stalled case is bounded by
`TIMEOUT_SECONDS` (default 3600) and `TIMEOUT_CYCLES` (default 1000000000).

If the process has stopped, resume the same directory with unchanged source,
configuration, reference, seed and timeout settings:

```bash
RUN="/absolute/path/printed/by/the/launcher"
BUILD_JOBS=24 ./run_config_sweep.sh --background --reference python --resume --output "$RUN"
```

Resume reuses matching simulator binaries and passing cases, and retries other
cases. It works without `generated-src`; interrupted/incomplete builds are
rebuilt. An active sweep holds a lock, so a second sweep cannot run concurrently.
Runs made before the hardware-cycle logging change cannot be resumed with these
sources because their manifests differ; start a new sweep and keep the old logs.

## Run a single case

```bash
./run_param_test.sh 1 64 64 16 4
```

`REBUILD=1` requests a simulator build; `REBUILD=0` reuses the current binary.
`TEST_SEED=2` selects a reproducible alternate input seed and packed-weight seed.
Default 8-bit weights retain the existing sequential pattern. `TEST_PATTERN=1`
uses input -32768 throughout; `TEST_PATTERN=2` also randomizes 8-bit weights.
`TIMEOUT_SECONDS` overrides the default 3600-second wall-clock limit per case;
`TIMEOUT_CYCLES` overrides the default 100000000 simulated-cycle limit.
`QUIET_TEST=0` enables the full software output. Host-prepared tests label the
reference explicitly and do not report software matrix-multiply speedup. An optional sixth argument
`debug` builds the debug simulator and records a waveform.
The default memory model matches the original script. `DRAMSIM=1` selects DRAMSim.

Resume passing logs in an existing sweep directory, with the same design,
software, seed and test flags:

```bash
LUMAX_LOG_DIR=/absolute/path/to/Log/sweep_<timestamp> ./run_multiple_tests.sh on
```

## Panic restore

```bash
/home/kioannou/chipyard_1.13.0/PANIC_RESTORE_LUMAX.sh
```

This stops registered task processes (including simulator descendants), verifies
the checkpoint checksum, and restores the saved LUMAX sources, software,
scripts, logs, simulator binaries and saved generated/build artifacts. It leaves
a STOP marker so this task cannot restart work after restoration. Keep the
checkpoint directory `/home/kioannou/lumax-rollback-20261006-224628` while you
want this rollback available.

## Verification artifacts

The incremental/final result table is
`/home/kioannou/lumax-rollback-20261006-224628/verified-sweep.csv`.
Additional signed-edge and alternate-seed regressions are recorded in the
checkpoint directory. `changes.patch` contains source differences against the
pre-task checkpoint. Only the deployed generator in Chipyard was changed;
the separate `/home/kioannou/LUMAX` repository was left unchanged.
Correctness results cover the requested configuration and dimensions. Tests
use the default memory model; they do not establish DRAMSim correctness or
performance benchmark numbers.
