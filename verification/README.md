# LUMAX verification

All three suites run real LUMAX hardware in a make-built Chipyard 1.13.0
Verilator simulator. RISC-V programs use the original `preload_hw()` and
`matmul_hw_uniform()` helpers. Python prepares operands and independently
checks actual hardware outputs. Input precision is 16 bits; weights are
8, 4 and 2 bits; outputs are signed 32-bit integers.

| Suite | Runner | Full coverage | Saved results |
|---|---|---|---|
| Original regression | `run_regression.py` | Six designs × ten shapes × three weight precisions = 180 runs | [180 PASS](logs/regression/run_20261008_193240/total_results.md) |
| Detailed test cases | `run_test_cases.py` | TC01–TC11, six designs, 3,798 runs; TC11 performs six matmuls per run | [3,792 PASS, six TIMEOUT](logs/test_cases/run_20261009/) |
| Llama2 dimensions | `run_llama_shapes.py` | Six unique shapes × three weight precisions = 18 logical tests / 204 hardware operations | [Interrupted after 43 passing tiles](logs/llama_shapes/run_20261010_131036_621307.log) |

## Local GUI — one command

Run this from your normal SSH terminal, from any directory:

```bash
python3 /home/kioannou/chipyard_1.13.0/generators/LUMAX/verification/gui.py start --host 0.0.0.0
```

Open the private URL printed by the command in your browser. No pip/npm
installation is needed. The dashboard and its workers run in the background;
closing the browser or SSH leaves them running. Keep the printed access URL
private. To print it again or stop the dashboard:

```bash
python3 /home/kioannou/chipyard_1.13.0/generators/LUMAX/verification/gui.py status
python3 /home/kioannou/chipyard_1.13.0/generators/LUMAX/verification/gui.py stop
```

Stopping the dashboard leaves an active test worker running; queued suites
wait until the dashboard starts again. Use **Stop this run** in the Runs view
to stop a test worker. A machine reboot interrupts workers; interrupted tests
are not reported as passing and require a new run.

The three views are:

- **Build a suite:** select one or several existing designs, or create a named
  configuration with b, RF, input capacity K and output capacity M. Add rows
  for `X[1,K] × W[K,M] → Y[1,M]`, choose W8/W4/W2, data patterns, repeats and
  optional seeds, then save or run. Presets also launch the original regression,
  TC01–TC11, Llama2 and native 1024 runners.
- **Runs:** queued/running/completed tests, exact matches, mismatches, live
  execution phases, measured hardware cycles and a downloadable execution log.
- **Saved logs:** browse the existing suite history, including original total
  reports, timeouts and incomplete runs.

**Custom suite options:** Auto uses one complete HW operation when the shape
fits; Native rejects shapes above the design capacity; Tiles lets you choose
block dimensions. Output-column tiles concatenate. Splitting K runs partial
products in LUMAX and adds the partial outputs in host software; the preview
explicitly identifies this case. No matrix padding is applied.

Python reference is the fast default. RISC-V C reference calculates a separate
comparison on the simulated CPU, which adds simulation time. Both compare
actual LUMAX DMA outputs element by element. Skip comparison reports
**UNVERIFIED**, never PASS. Python reference answers are never embedded in the
RISC-V ELF. Original suite presets retain their original reference and checks;
custom options apply to custom matrix suites.

Cycle recording uses the actual LUMAX counters and the CPU cycle interval
around `matmul_hw_uniform`. Active HW cycles are the sum of Load X, Generate,
combined Load W/Select, and Store O; overlapping counters are not double counted.
Compilation, printing, Python reference calculation and optional C reference
are outside the reported compute interval. Tiled cycle totals sum the actual
HW operations and exclude host assembly and simulator startup. Per-operation
counters remain in the execution log.

Each selected design is built with `make` once per suite and reused for its
cases. Only the selected configuration fields change temporarily for the
build; they are restored afterwards. RTL logic and the original C source stay
unchanged. Every native operation compiles a `.riscv` ELF and invokes the
make-built Verilator simulator with that ELF. Custom tests load operands
directly into guarded buffers through `+loadmem`; they avoid large simulated
CPU copies. Full input integrity scans and matrix dumps are optional.

New jobs wait for the shared configuration lock held by an existing run.
Selecting a new configuration does not declare it verified: its tests must
pass first. Keep the normal Chipyard toolchain installed and available through
`/home/kioannou/chipyard_1.13.0/env.sh`.

Saved designs/suites are in `verification/gui_data/`. Each GUI run keeps its
suite snapshot, result metadata and logs in `verification/logs/gui/run_<date>/`.
Original preset results also retain their usual suite log paths. Temporary
ELFs and build directories are removed when the run ends. Private dashboard
runtime files are in the Git-ignored `verification/.gui/` directory.

### Llama2 fast cycle estimate

Choose **Llama2 · fast cycle estimate** in the suite selector. For each of the
six shapes and each of W8/W4/W2, the runner selects the largest tile that fits
the selected design's configured Cin/Cout capacities:

```text
tile_K = min(shape_K, design_max_K)
tile_M = min(shape_M, design_max_M)
required_tiles = ceil(shape_K / tile_K) × ceil(shape_M / tile_M)
estimated_full_HW_cycles = measured_tile_HW_cycles × required_tiles
```

Each shape/precision runs only that one tile in real Verilator and compares
its actual output using the selected reference method. A design builds once
and is reused for all 18 sampled cases. There is no fixed 512-column tile size.
For a 1024×1024-capacity design, LQ04 samples `1×288 × 288×1024` once per
precision and estimates the full classifier using `tile_cycles × 32`.

Smaller edge tiles are charged at the full sample-tile cost, so this is an
approximation. It excludes host assembly and simulator startup. The log and
GUI keep measured cycles, required tile count and estimated totals separately.
Only sampled outputs are verified; shapes requiring multiple tiles report
**ESTIMATED**, not a full-shape PASS. A shape fitting into one tile can report
PASS because the whole shape was checked. Disabling comparison still reports
UNVERIFIED. Failed or timed-out samples have no extrapolated cycle total.
The original **Llama2 dimensions** option still runs all tiles for full coverage.

After installing a GUI code update, restart the web server from your SSH
terminal and refresh the browser:

```bash
python3 /home/kioannou/chipyard_1.13.0/generators/LUMAX/verification/gui.py restart
```

Restart preserves the current host, port and access key. Active verification
workers continue; this command only restarts the dashboard server.

For SSH-only browser access, start without `--host` (localhost only), forward
port 8765 from your local computer with
`ssh -N -L 8765:127.0.0.1:8765 kioannou@SERVER_IP`, using the same server IP or
hostname as your working SSH connection. Keep this tunnel terminal open and
use `http://127.0.0.1:8765/?token=YOUR_PRINTED_TOKEN` in your local browser.
An alternative port can
be selected with `--port 8766` on startup.

## Start a suite

```bash
cd /home/kioannou/chipyard_1.13.0/generators/LUMAX/verification

# Choose ONE of these commands:
python3 run_regression.py start
python3 run_test_cases.py start
python3 run_llama_shapes.py start --design b8_rf8
```

Each `start` detaches automatically, so you may close SSH. Run one suite at a
time: all three use the same configuration lock. They build each selected
design once and reuse it for all of that design's tests. Geometry is selected
temporarily for make and then restored; hardware logic and the original C
source are not edited. Temporary builds and executables are removed at the
end. A server shutdown/reboot interrupts a run; these runners do not resume
automatically. Starting again creates a fresh run and preserves old logs.

The working copy is installed inside the Chipyard generator at the path above.
The Git copy is `/home/kioannou/LUMAX/verification`; the scripts and retained
history are also available there. New results are written beside whichever
copy of the runner you start.

## Check progress or stop

Use the same runner with `status` or `stop`:

```bash
python3 run_regression.py status
python3 run_test_cases.py status
python3 run_llama_shapes.py status

# Example: stop the Llama2 suite safely
python3 run_llama_shapes.py stop
```

`status` shows the active run or the latest saved results. `check` validates
prerequisites and prints the plan without building hardware or running a
simulation. `--help` lists options. Simulator wall timeout defaults to 7,200
seconds per invocation; `--timeout 14400` allows four hours. TIMEOUT is an
unverified result, not a demonstrated output mismatch.

The regression and TC runners default to six parallel simulator jobs. Use
`--workers 1` for sequential execution. `--design b8_rf8` selects just one of
the six designs. To select individual test cases:

```bash
python3 run_test_cases.py check --tc TC01 TC02 --design b8_rf8
python3 run_test_cases.py start --tc TC02 --workers 1
```

The regression runner imports its execution functions from `run_test_cases.py`;
keep the three scripts together in this folder. The active Chipyard tree is
`/home/kioannou/chipyard_1.13.0`.

## Native 1024×1024 capacity test

`Config.scala` now sets `Cin=Cout=1024`. The additional `run_1024.py` runner
builds one selected design once, then runs three complete, untiled
`X[1,1024] × W[1024,1024] → Y[1,1024]` multiplications, with input16 and
W8/W4/W2. It reuses `run_llama_shapes.py` for operand packing, C generation,
Verilator execution and exact host comparison; keep that file alongside it.

```bash
python3 run_1024.py start --design b8_rf8
python3 run_1024.py status
python3 run_1024.py stop
```

The default simulator timeout is six hours per precision. Actual hardware
stage counters and core compute cycles are saved in one dated log under
`logs/matrix_1024/`, along with seeds, data distributions and exact-match
counts. Matrix dumps and temporary build files are removed. References are
calculated in Python and checked to fit int32; they are never put in the ELF.

## Test coverage

The regression tests `X[1,K] × W[K,M]` for square K=M=4,8,16,32,64,128,256,512,
plus K×M=64×16 and 128×32, on b=2/4/8 and RF=4/8. New runs use independent
random seeds and the same shapes/precisions; they do not replay historical
vectors automatically.

| TC | Shapes/data | Simulator runs |
|---|---|---:|
| TC01 | Small random squares: K=M=4,8,16 | 54 |
| TC02 | Irregular rectangles: 12×20, 28×36, 60×12 | 54 |
| TC03 | Long rectangles: 4×260, 260×4, 16×512, 512×16 | 72 |
| TC04 | Input lengths around boundaries: 7/8/9 through 63/64/65; M=8 | 216 |
| TC05 | Output lengths around boundaries: M=3,4,5,7,8,9,15,16,17; K=16 | 162 |
| TC06 | Zero input, zero weights, both zero; 16×8 | 54 |
| TC07 | Signed extremes and alternating signs; 16×8 and 256×4 | 180 |
| TC08 | Every isolated input and weight position; 16×8 | 2,592 |
| TC09 | Ten independent random seed pairs per shape; 12×20 and 60×12 | 360 |
| TC10 | Large squares: 256×256 and 512×512 | 36 |
| TC11 | Six operations in one boot: random, zero, new random, dimension changes, return to original shape | 18 |

TC11 uses 16×8, 16×8, 16×8, 12×20, 60×12, 16×8 without resetting the
accelerator between operations. Weight precision stays constant within a boot.
Its new harness wraps the hardware counter-read selector and differences the
cumulative combined phase so per-operation cycle records remain meaningful.

The Llama2 suite uses b8_rf8 by default, T=16, and starts with LQ04:

| Shape | Input × weights → output |
|---|---|
| LQ01 | 1×288 × 288×288 → 1×288 |
| LQ02 | 1×288 × 288×768 → 1×768 |
| LQ03 | 1×768 × 768×288 → 1×288 |
| LQ04 | 1×288 × 288×32,000 → 1×32,000 |
| LQ05 | 1×48 × 48×16 → 1×16 |
| LQ06 | 1×16 × 16×48 → 1×48 |

LQ04 uses 62 column tiles of 512 plus one of 256, with the same complete input
for every tile. Concatenating the tile outputs gives the full logical result.
There is no dimension padding or reduction splitting. Every native operation
boots Verilator separately. These are integer shape tests, not complete
`runq.c` inference or its floating-point/group-scaling behavior. Llama2 logs
retain seeds, distributions, hashes, matches and per-tile hardware cycles,
without input, weight or output matrix dumps.

## Logs and retained history

```text
verification/
├── run_regression.py
├── run_test_cases.py
├── run_llama_shapes.py
└── logs/
    ├── regression/run_<date>/
    ├── test_cases/run_<date>/Log_TC01.log … Log_TC11.log
    └── llama_shapes/run_<date>.log
```

New regression runs keep `Log_REGRESSION.log` in their dated folder. The saved
180-run regression retains its original total/per-design reports and logs.
All historical files were moved without changing their contents, including
source snapshots and original command/path records. Some recorded absolute
paths refer to the original execution location and are historical metadata.

The saved TC01 log is a recovered original summary: full matrix/UART blocks for
53 of its 54 cases are unavailable. The six historical TC10 timeouts are all
512×512/W8 cases and remain inconclusive. The older 2026-10-10 Llama2 run passed 43 W8
classifier tiles (22,016 exact outputs), then stopped during tile 44; it has
no final completion record and does not prove a complete LQ shape passed.

The Git copy also includes snapshots copied from the working generator on
2026-10-20: GUI run records, the newer Llama2 log and native 1024 logs. Files
copied while a job is active are partial records; a saved RUNNING status is
the status at copy time, not proof that a worker is running from this checkout.
The working generator continues to own and update its original run files.
GUI access keys, server runtime files, stop markers and lock files are excluded.

The newer [full Llama2 run](logs/llama_shapes/run_20261018_201948_372793.log)
is now retained through completion on 2026-10-21: **18/18 logical tests PASS,
204/204 native HW operations PASS**, on b8_rf8 with input16 and W8/W4/W2.
All six shapes were fully verified, with measured per-operation and summed
cycle counts. The log confirms unchanged HW/SW sources and restored Config.scala;
the corresponding GUI run record also reports PASS. These are full verification
results, separate from the sampled estimates produced by the new fast mode.
