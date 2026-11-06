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

## Build-time FP16 activation variant

`src/main/scala/Config.scala` adds `FP16_ACTIVATIONS = false` to
`LUMAX_Config`. False elaborates the original signed-integer datapath. True
elaborates an FP16 activation datapath with signed W8/W4/W2 weights and FP32
outputs. Changing this Boolean requires rebuilding the Verilator simulator;
there is no runtime activation-format switch. FP16 requires input16, output32,
maximum weight precision 8 (runtime W8/W4/W2), `Cin<=65536` and `SCALE=false`.

The FP16 variant keeps the even-product lookup tables, weight lookup and odd
weight correction. It decodes the half significand, aligns selected products
into an exact fixed-point accumulator and rounds once to FP32, nearest with
ties to even. This is an exact dot product followed by one FP32 rounding;
it can differ from rounding every addition in an FP32 software loop. Subnormal
inputs are supported. NaNs produce canonical `0x7fc00000`; infinity times
zero and opposite infinities produce NaN. Exact cancellation returns +0.
The wider accumulator, alignment and output conversion add hardware; this
variant's synthesis area and timing have not been measured.
With the current `Cin=1024`, each output accumulator is 58 bits instead of
32, plus three special-value flag bits. None of this logic is elaborated in
the false variant.

`software/tests/src/Linear-sw.c` uses raw IEEE half bits in `uint16_t` inputs
and raw FP32 bits in `uint32_t` outputs for this build. Its integer-only C
reference needs no CPU floating-point unit. `build.sh` reads the Boolean from
the same Config file and sets `LUMAX_FP16_ACTIVATIONS` automatically. Temporary
builds can select their matching snapshot through `LUMAX_CONFIG_PATH`.
The existing CPU-FPU option is separate from the activation format.

Run the dedicated verifier from this directory:

```bash
python3 run_fp16.py --design b2_rf8 --mode both --sizes 32 128
```

Omit `--design` to verify all six existing b/RF designs. Each mode/design is
built once with make and reused for W8/W4/W2. For each design and weight width,
the 1×32 × 32×32 test must pass before the 1×128 × 128×128 test starts.
Each RISC-V ELF calls the original hardware helpers and checks the actual DMA
outputs against the C reference; every output is also compared with an exact
Python reference on the host. Python answers are not embedded in the ELF.
Logs retain simulator/ELF commands, source hashes, seeds, actual outputs,
matches and hardware cycle counters under `logs/fp16/run_<date>.log`.
Temporary builds and vectors are removed on success. This foreground runner
uses the shared configuration lock and restores the starting configuration.
`--quick` instead runs the small random and directed special-value smoke suite.

The original script suites use integer references. Keep
`FP16_ACTIVATIONS=false` for those standalone runners. The GUI custom matrix
and Llama2 fast suites support both formats and explicitly select this Boolean
for each design build. `run_fp16.py` remains the dedicated format regression.

Verified on 2026-10-23 in actual Verilator: all six designs × W8/W4/W2 ×
32×32/128×128 passed (**36 FP16 matmuls; 2,880 exact output matches**).
The [small directed smoke run](logs/fp16/run_20261023_020521_613279.log)
also passed all 39 cases, including subnormals, rounding and NaN/infinity
handling. The original `Linear-sw.c` entry point passed three additional
32×32 runs, one per weight precision, with automatic software format selection.
The FP32 conversion RTL passed 101,133 standalone checks. For the false build,
all 1,378 FIRRTL modules match the pre-change design exactly after removing
source-location annotations, and the preprocessed C tokens are unchanged.
The mirrored INT16 regression also passed all 36 matmuls: **72 PASS, 0 FAIL**
across both modes, with 5,760 exact output matches. The
[matrix verification log](logs/fp16/run_20261023_021731_113115.log) retains
the numerical comparisons, hardware cycle counts and supplementary evidence.

## Parallel rows, consecutive matmuls and DMA ordering

`y_slice` is an elaboration-time activation-row parallelism parameter. Each
active row has its own b activation banks, product tables and accumulators;
weight storage and decoding remain one shared b-bank group per weight buffer.
A fetched weight selects a product in every active row during the same cycle.
INT16 and FP16 inputs use signed W8/W4/W2 weights. FP16 partial sums remain in
the exact hardware accumulator and round once when the completed output is stored.

Set `Rin` to the maximum total input rows and require `Rin >= y_slice >= 1`.
For example, `y_slice=2, Rin=4` processes four rows in two groups sharing each
group's weights. A partial final group masks inactive rows. Row transitions
reset the packed-input offset and restore the bank span after a short K block.
Defaults remain `Rin=1, y_slice=1`.

The GUI exposes **Y_SLICE**, **Maximum activation rows R**, and per-test
**R/K/M** for `X[R,K] × W[K,M] → O[R,M]`. K/column tiling preserves all rows
and checks every output element. Saved cycle-model parameters include actual
row count and Y_SLICE. The controller cycle model accounts for shared weights,
parallel row generation and partial final groups.

Run the small verifier from the generator root in the working Chipyard tree:

```bash
python3 verification/run_y_slice.py --mode both --y-slices 1 2 --b 2 --rf 4
python3 verification/run_y_slice.py --mode both --y-slices 1 2 --multirow-only
```

It builds each physical format/Y_SLICE configuration once with make, compiles
RISC-V programs using the original `Linear-sw.c` helpers, and compares actual
Verilator outputs with independent C/Python references. Successful runs remove
temporary build files; one dated log remains under `logs/y_slice/`.

### Consecutive operations without a hardware reset

```bash
python3 verification/run_y_slice.py --mode both --y-slices 1 2 --consecutive
python3 verification/run_y_slice.py --mode both --y-slices 1 2 --interleaved-checks
python3 verification/run_y_slice.py --mode fp16 --y-slices 1 --interleaved-checks --pair-only
```

The eight-call sequence changes dimensions and K-block sizes, repeats earlier
operands, exercises multiple row groups, then runs large signed/NaN values
followed by zero inputs. Each call has its own guarded output buffer.
`--consecutive` defers all printing, reference calculations and counter reads
until the complete hardware sequence finishes. `--interleaved-checks` performs
those software actions between calls; `--pair-only` keeps just A then B.

Software memory activity exposed a response-ordering bug: two activation DMA
responses could return in the opposite order, while the loader assigned them
sequential destination positions. `DMA.scala` now keeps a queue of issued
source IDs and captures each returned data/error response in its reserved
slot. LUMAX receives responses in request order. A source remains occupied
until ordered delivery, preventing premature reuse. The completion guard also
requires DMA to be empty before finishing or starting another row group.

`Config.scala`'s `Dma_Ids` is a positive build-time request-slot count. Queue
and response-storage depth follow that value; source width is
`max(1, log2Ceil(Dma_Ids))`, including the single-slot case. Returned source
IDs are range-checked in simulation. Changing `Dma_Ids` requires a rebuild.
The checked-in default remains **2**.

### Activation counters follow K capacity

Both `all_available_elems_x` and `j_x_start` are sized from `Cin`, rather than
`Cout`; `cin_reg` also initializes from `Cin`. With `Cin=256, Cout=32`, a
Cout-sized six-bit K-block index wrapped 64 to zero and repeatedly loaded the
first 64 activations. Both two- and six-ID builds failed identically. The final
fix keeps the original small output capacity; increasing Cout is not required.
These are control-width corrections, without changing LUT arithmetic or
weight-sharing logic.

### Retained verification evidence

| Verification | Scope | Result |
|---|---|---|
| [Row regression](logs/y_slice/run_20261023_225907_614283.log) | b2_rf4; INT16/FP16; W8/W4/W2; Y_SLICE=1 with two rows and Y_SLICE=2 with four rows, tails and repeated operations | 60/60 matmuls; 438/438 output elements |
| [GUI-generated HW checks](logs/y_slice/run_20261023_225709_843818.log) | Multirow native C programs, including bounded output reporting | 18/18 HW checks |
| [Ordered-DMA repair](logs/y_slice/run_20261101_140546_883039.log) | Three original failing schedules plus consecutive regressions; Dma_Ids=2 | 36/36 matmuls |
| [Final width/parameter fix](logs/y_slice/run_20261101_152317_457850.log) | b8_rf8, Y_SLICE=2, Cin=256, Cout=32, Dma_Ids=6; INT16/FP16 × W8/W4/W2 plus the exact earlier failing ELF | 21/21 matmuls; 1,792 exact output words |
| [DMA RTL parameter sweep](logs/y_slice/run_20261101_152317_457850.log) | Actual generated DMA RTL; request counts 1, 2, 3, 4 and 6 | 10,195 transactions; all slots used; reverse-order responses, source reuse, stalls and zero-latency responses checked |

Every final six-ID matmul used IDs 0–5 and reached six requests awaiting
responses simultaneously during its activation loads. The largest runtime
shape was `X[4,256] × W[256,32] → O[4,32]`; the first two-row operands were
repeated afterward without a reset. Hardware cycle/stage counters are saved
for these six-ID matmuls. The other ID-count checks are DMA protocol tests,
not comparable matmul-cycle measurements. The final log also preserves
intermediate failing revisions; its final-source results are 21 PASS, 0 FAIL.
ID=8 was cancelled at the user's request and is not claimed as verified.
These targeted checks do not replace the complete historical b/RF regression.

The software/GUI suite contains 49 checks:

```bash
python3 -m unittest discover -s verification/tests -v
```

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

The dashboard views are:

- **Build a suite:** select one or several existing designs, or create a named
  configuration with b, RF, Y_SLICE, row capacity R, input capacity K and output
  capacity M. Add tests for `X[R,K] × W[K,M] → Y[R,M]`, choose W8/W4/W2, data patterns, repeats and
  optional seeds, then save or run. Presets also launch the original regression,
  TC01–TC11, Llama2 and native 1024 runners.
- **Runs:** queued/running/completed tests, exact matches, mismatches, live
  execution phases, measured hardware cycles and a downloadable execution log.
- **Saved logs:** browse the existing suite history, including original total
  reports, timeouts and incomplete runs.
- **Cycle model:** select saved runs and individual native operations, then
  compare their recorded hardware cycles with the editable Python model.

### INT16 and FP16 activations

The Designs panel contains six INT16 and six FP16 presets. Select either or
both formats in a custom suite. **Create design → Activation format** also
saves INT16 or FP16 with a custom b/RF/capacity. This controls the Scala Boolean
at build time; each selected design gets its own make-built Verilator binary.
The C operand datatype and compiler flags are checked against that design's
saved configuration, even after the working Config.scala has been restored.
Old saved designs and jobs without a format field continue to mean INT16.

FP16 inputs use genuinely varying finite IEEE half values, including both
signs and different exponents; the signed weight ranges remain W8/W4/W2.
Minimum/maximum input patterns mean −65504/+65504; a one-hot input uses 1.0.
Python decodes these half bits and calculates the exact dot before one FP32
rounding. It never supplies the reference outputs to the RISC-V test. The
program calls the original preload/matmul helpers and returns actual LUMAX DMA
outputs, hardware cycles and guard results. With the RISC-V reference option,
the FP16 C helper independently checks each operation; the assembled full
result is still checked against the full Python dot.

**FP16 output comparison** offers exact FP32 bits (default) or numeric
tolerance. Tolerant mode requires, for every finite output:

```text
abs(HW - reference) <= atol + rtol * abs(reference)
```

INT16 always requires exact equality. Expand an FP32 comparison in Runs to
see exact matches, matches within tolerance, maximum absolute/relative error,
the selected thresholds and mismatched values with their FP32 bits. Failed
numeric comparisons show the minimum atol with rtol fixed and the minimum
rtol with atol fixed. A nonzero error against a zero reference may require
atol; NaN/infinity mismatches cannot be repaired by a finite tolerance.
Identical NaN bits and same-signed infinities match. Tolerant mode treats
+0/−0 as numerically equal; exact mode compares their bits. Suggested values
are never applied automatically or used to rewrite a saved FAIL. Execution,
guard and cycle-check failures remain failures regardless of tolerance.

Output-column tiling concatenates FP32 words. Splitting K rounds each partial
in hardware, then sums the actual FP32 partials exactly in host software and
rounds once to FP32. The full reference still uses one exact dot followed by
one rounding, so K tiling can create a small rounding difference. The GUI
reports that error and the tolerance needed. Fast Llama2 verifies only its
sample tile; extrapolated cycles retain their ESTIMATED label.

Original regression/TC/full-Llama2/native-1024 script presets remain INT16;
choose Custom matrices or Llama2 fast for FP16. The current Python cycle model
supports both INT16 and FP16. Its format selector describes the elaborated
hardware; it does not switch a running design. The current FP16 arithmetic is
combinational, so identical controller parameters and memory responses produce
the same cycle counts in both formats. This does not imply equal clock frequency.

After installing this GUI update, run `python3 verification/gui.py restart`
from your normal SSH terminal and refresh the browser. If new frontend files
are served by an older running backend, the GUI shows this restart instruction
and keeps FP16 creation disabled until the backend supports it.

The [GUI format integration check](logs/gui_fp16/run_20261023_090447_601412.log)
passed 12 logical INT16/FP16 cases using 33 actual Verilator invocations,
including native and K/output tiling with W8/W4/W2, the FP16 C reference and
a directed rounding case with a verified required tolerance. Every native
operation matched its reference bits exactly. The software/API checks passed
42 tests; frontend syntax and format/tolerance rendering were also checked.

### Compare the Python cycle model with saved runs

Open **Cycle model**, or click **Compare cycle model** in a run or saved-log
entry. Select runs and click **Load recorded tests**. All usable measurements
start selected. Filter by design, weight precision or test/shape, then click
**Select matching only** to replace the selection with that subset. Individual
checkboxes select specific operations. Click **Run Python model**.

Results show measured and predicted cycles, minimum/maximum absolute errors
in cycles and percent, mean absolute error (MAE), mean absolute percentage
error (MAPE), and the fraction of predictions within ±10% of hardware cycles.
This last fraction is explicitly labelled as the accuracy tolerance, rather
than treating `100 - MAPE` as an accuracy probability. Enable **Include cycles
and errors per stage** for stage summaries and expandable per-operation
counters. Missing counters display as unavailable. Overlapping Load W-only
and Select-only counters are diagnostic and are never added to the total.
Download the current comparison as JSON directly from your browser; comparison
does not create extra server logs or modify the original run.

**Quick plot** summarizes the complete selected comparison, including operations
on other result pages. Group by total, stage, design, activation format, weight
bits or design plus activation and weight bits. Choose mean/minimum/maximum absolute error in percent or cycles,
or compare summed **HW vs Python cycles**. The Phase selector compares a specific
stage across designs/precisions; grouping by Stage shows all available stages.
Each bar shows its measurement count. Stage counters that are unavailable are
excluded; overlapping Load W-only/Select-only bars must not be summed.

Use **Copy plot** to copy a PNG image. If clipboard access is unavailable (common
on a remote HTTP dashboard), a preview lets you right-click **Copy image**.
**Download PNG** exports a high-resolution image; **Download SVG** exports a
scalable image. Plots are generated only in browser memory. Nothing is saved on
the server or in Git; closing the page discards the plot unless you explicitly
copy or download it. No extra Python-model evaluation or HW simulation is needed
when changing the plot controls.

The single working model source is:

```text
/home/kioannou/chipyard_1.13.0/generators/LUMAX/Performance Modeling/Model.py
```

It was initially copied byte-for-byte from the LUMAX Git model. Edit **this
working file**, save it and click **Run Python model** again: the GUI rereads
and executes its current source on every comparison without a restart or
cached module/bytecode. The source SHA-256 accompanies each result. No cycle
equations or fitted corrections are duplicated in the GUI. The loader skips
only unused `matplotlib`, `ipywidgets` and `seaborn` notebook imports; all other
top-level definitions and the original calculation functions execute from the
source file. The revised event model follows controller edges, finite DMA IDs,
weight-buffer overlap, literal RTL tail spans and serialized output stores.
Keep its `compute_cycles` interface and returned cycle keys when
editing formulas. A broken model produces an error, without old predictions
as a fallback.

The default memory profile models the current Chipyard magic-memory/LLC setup:
seven-cycle controller-visible hits, 64-byte cache lines, cold buffers and no
CPU interference. Current GUI operations record ELF buffer addresses for beat
alignment and cache-line sharing. Older records without addresses use disjoint,
aligned defaults. Historical direct-response DMA suites use a six-cycle hit
setting; explicit snapshots override defaults. Cache ownership, initial residency
and CPU traffic remain prediction uncertainties. DMA-latency replay is a separate
controller diagnostic and is excluded from independent accuracy statistics.
See [the cycle-model validation report](../Performance%20Modeling/Accuracy/Cycle_Model_Validation.md)
for measured accuracy, stage errors, coverage and limitations.

Parameters use the saved b/RF, input/weight bits and actual native dimensions.
For tiles, predictions use the actual tail dimensions and totals sum only the
selected measured operations. Fast Llama2 comparisons use the measured sample,
with extrapolated full-shape cycles excluded from accuracy. New custom GUI
runs snapshot physical settings and per-operation model arguments. Older GUI
runs reconstruct tile geometry from their saved suite/design; historical logs
without a physical snapshot use the original runner contract (YS=1, DMA IDs=2,
64-bit DMA, maximum input/weight bits 16/8, six-cycle direct-response hits).
Every operation's arguments
can be inspected in its expandable row. Missing TC01 timing, TC10 timeouts,
misaligned TC11 counters and incomplete native-1024 results are not invented.

The Python comparison does not start a make build or Verilator simulation.
HW correctness runs and their independent Python matrix references continue
through the existing execution path. After installing the GUI code update,
restart the dashboard once from SSH, then refresh your browser:

```bash
python3 /home/kioannou/chipyard_1.13.0/generators/LUMAX/verification/gui.py restart
```

Restarting the dashboard preserves its host/port and access key and leaves
independent verification workers running. Subsequent model-file edits require
only another comparison click.

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
