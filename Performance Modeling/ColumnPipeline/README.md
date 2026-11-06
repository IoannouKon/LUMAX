# Historical column pipeline scheduling experiment

Originally generated: 2026-11-05T18:44:17.182466+00:00

**These are historical Python predictions, not Verilator results.** They predate the accepted RTL. Production Model.py is now v4 and models its four-cycle tags and one-cycle buffer-release delay. The experiment below keeps its original five-cycle hypothetical timing and uses frozen archive/Model_v3.py. PASS means scheduling checks passed; no numerical matrix multiplication is verified by this experiment.

## Models and assumptions

- Reference: `controller-events-v3`, the archived controller before tagged-column selection.
- Candidate: `column-pipeline-experimental-v1`, in [Model_ColumnPipeline.py](../Model_ColumnPipeline.py).
- Keep five cycles from the end of a column's reads to retirement. Allow the next column to issue during that latency.
- Release a weight buffer after its final read is captured. Keep the same two weight buffers, DMA capacity, memory ports, b, RF, Y_SLICE and output DMA scheduling.
- Assume tagged valid data and output-column indices, and activation/weight addressing that can switch columns without extra bubbles. Area and clock frequency are not estimated.
- Keep weight-ready stalls, source-ID limits and the memory profile. The final pipeline drain is required before changing K window or storing outputs.
- INT16 and FP16 have the same controller timing assumptions; test both formats explicitly.
- V3's known Y_SLICE=1 tail-padding behavior is retained in both schedulers.

## Sweep

- 1,296 model pairs; b=[2, 4, 8], RF=[4, 8], Y_SLICE=[1, 2, 4], DMA IDs=[6].
- W bits=[8, 4, 2]; activations=['int16', 'fp16']; memory profile=`chipyard_llc`.
- X uses 16-bit activations; outputs use 32-bit words. Shapes are X[R,K] × W[K,M].
- Default, disjoint, cache-line-aligned addresses and the same cold-cache assumptions in each pair.
- Runtime: 266.31 seconds.

| Shape | X | W |
|---|---|---|
| small4 | 1×4 | 4×4 |
| small32 | 1×32 | 32×32 |
| square128 | 1×128 | 128×128 |
| square512 | 1×512 | 512×512 |
| square1024 | 1×1024 | 1024×1024 |
| expand | 1×288 | 288×768 |
| contract | 1×768 | 768×288 |
| odd3 | 3×7 | 7×17 |
| rows4 | 4×128 | 128×128 |
| rows8 | 8×512 | 512×512 |
| odd5 | 5×129 | 129×257 |
| single_column | 1×1024 | 1024×1 |

## Results

- 1,296/1,296 scheduling checks passed. The five-cycle-gap control exactly matches every v3 result field except the version label for every pair.
- Faster: 1,028; unchanged: 268; slower: 0.
- Per-case cycle reduction: min **0.00%**, median **1.90%**, max **21.37%**.
- Sum across this synthetic sweep: 158,679,002 → 152,287,412 cycles; **4.03%** reduction weighted by baseline cycle count. This weighting does not represent an application workload.

A zero reduction is valid: one output column has no inter-column handoff, and weight-memory stalls can absorb the removed pipeline bubbles. These predictions are opportunities for an RTL experiment, not measured hardware speedups.

### By Weight precision

| Weight precision | Cases | Cycle-weighted reduction | Median reduction | Min | Max |
|---|---:|---:|---:|---:|---:|
| 2 | 432 | 3.23% | 3.69% | 0.00% | 21.37% |
| 4 | 432 | 5.03% | 3.67% | 0.00% | 14.65% |
| 8 | 432 | 3.91% | 0.31% | 0.00% | 20.27% |

### By b

| b | Cases | Cycle-weighted reduction | Median reduction | Min | Max |
|---|---:|---:|---:|---:|---:|
| 2 | 432 | 5.42% | 3.32% | 0.00% | 20.27% |
| 4 | 432 | 3.05% | 3.13% | 0.00% | 21.37% |
| 8 | 432 | 1.60% | 0.07% | 0.00% | 18.89% |

### By RF

| RF | Cases | Cycle-weighted reduction | Median reduction | Min | Max |
|---|---:|---:|---:|---:|---:|
| 4 | 648 | 2.12% | 0.72% | 0.00% | 21.37% |
| 8 | 648 | 6.29% | 2.70% | 0.00% | 21.37% |

### By Y_SLICE

| Y_SLICE | Cases | Cycle-weighted reduction | Median reduction | Min | Max |
|---|---:|---:|---:|---:|---:|
| 1 | 432 | 4.32% | 1.90% | 0.00% | 21.37% |
| 2 | 432 | 3.94% | 1.90% | 0.00% | 21.37% |
| 4 | 432 | 3.65% | 1.88% | 0.00% | 21.37% |

### 1024×1024 weights, X=1×1024

Examples below use INT16, RF=8, Y_SLICE=2 and DMA IDs=6 when those settings are in the sweep.

| b | W bits | V3 cycles | Pipeline cycles | Saved cycles | Reduction |
|---|---:|---:|---:|---:|---:|
| 2 | 8 | 1,018,216 | 870,728 | 147,488 | 14.48% |
| 2 | 4 | 548,944 | 528,484 | 20,460 | 3.73% |
| 2 | 2 | 532,018 | 526,903 | 5,115 | 0.96% |
| 4 | 8 | 608,040 | 591,528 | 16,512 | 2.72% |
| 4 | 4 | 275,260 | 265,030 | 10,230 | 3.72% |
| 4 | 2 | 269,362 | 264,247 | 5,115 | 1.90% |
| 8 | 8 | 484,680 | 484,600 | 80 | 0.02% |
| 8 | 4 | 182,364 | 182,359 | 5 | 0.00% |
| 8 | 2 | 138,034 | 132,919 | 5,115 | 3.71% |

## Stages and full data

[results.csv](results.csv) contains every parameter pair, total cycles, additive stages, the overlapping load-W/select counters, request counts and predicted reductions. Do not add the standalone load-W and selection counters to the combined stage.

The production GUI uses Model.py/v4. This archived experiment does not replace it.

## Reproduce

From the generator/repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 verification/compare_column_pipeline.py
```

V3 SHA256: `666e732187a249de58ff4fcbb6180488d7dc777691e6e0d52a097dc7dab2bbdd`
Original experiment SHA256 (before redirecting its helper import to archived v3): `38d2ec57c2c7d2c24fb894cf4b954b71216f6894e963c05a39910becd4b11a94`

The comparison validates the event model, not achievable Fmax or final hardware correctness. A real implementation must preserve SRAM latency, valid/tag alignment, buffer reuse and output accumulation.
