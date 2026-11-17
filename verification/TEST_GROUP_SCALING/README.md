# Optional hardware group scaling

Optional Chipyard prototype; no PPA claim yet. **68/68 actual Verilator tests passed, with 1,604 exact FP32 output matches.** See [validation details](VALIDATION.md) and `runs/` for logs.

Configure `src/main/scala/Config.scala` and rebuild RTL:

| GROUP_SCALE_ALIGNED | GROUP_SCALE_DEFERRED | Behavior |
|---|---|---|
| false | false | Existing unscaled matmul |
| true | false | Bound K windows at quantization-group boundaries; reduce banks before scaling |
| false | true | Keep physical K windows; retain bank/group partials and scale fragments through FIFOs |
| true | true | Rejected during elaboration |

The initial supported contract is **finite FP16 activations, signed W2/W4, finite FP16 scales, G=32/64/128, FP32 outputs**. For each row and column:

`O[r,n] = round_FP32_RNE(sum_g scale[g,n] * sum_(k in group g) X[r,k] * W[k,n])`.

Partials are exact in fixed24 units and scaled sums exact in fixed48 units. There is one final round to FP32; this does not promise the intermediate rounding of a particular PyTorch/GPTQ kernel. Zero points, bias, activation functions, NaN/Inf and W8 group scaling are outside this first milestone. Existing unscaled W8/W4/W2 paths remain available with both flags false.

Use `BRAM_OUTPUTS=true`, `FP16_ACTIVATIONS=true`. The initial prototype rejects combining group scaling with flexible bank grouping, zero skipping, early buffer release or DMA bursts. It shares the existing DMA engine. Scale metadata is preloaded once per matmul into synchronous SRAM, and scaling drains at each K-window boundary. Initial scale-preload cycles are included in the LoadX counter.

Software must be compiled by `software/tests/src/build.sh` against the matching configuration. It derives `LUMAX_GROUP_SCALE_MODE` (0=disabled, 1=aligned, 2=deferred). While idle, call:

```c
// Raw IEEE binary16 values, group-major [ceil(K/G)][N], base aligned to 8 bytes.
// The DMA reads a final full 8-byte beat; provide padding through that beat.
if (lumax_set_group_scales(scales, G)) return 1;
// Then use the existing preload_hw/load_w/matmul_hw_uniform interface.
```

RoCC funct11 sets the scale base; funct12 sets G. The normal matmul-start command preloads metadata before activation loading. Scales may vary per group and output column and may be zero or negative. Scales stay cached across activation-row groups within that matmul.

## Actual hardware checks

```bash
cd /home/kioannou/chipyard_1.13.0/generators/LUMAX/verification/TEST_GROUP_SCALING
python3 run_group_scaling.py start
python3 run_group_scaling.py status
# Optional K511 / B3 / YS1 / ID2 checks, after the main run finishes:
python3 run_group_scaling.py start --boundary
```

The detached runner uses B4/RF4/YS2/ID8, builds real Verilator executables through make, compiles RISC-V ELFs, reads hardware output bits and compares against an independent exact Python reference. It runs 8 unscaled controls and 24 tests per scaled mode, with G32/G64/G128 and W2/W4. Shapes (R,K,N): (2,17,5), (5,65,9), (3,129,7), (4,257,9). Consecutive matmuls share an ELF, including partial rows, odd output columns, K tails and group crossings. Seeds and operand hashes, per-value results, commands and stage counters are logged. An additional `run_boundary_k511` checks both scaled modes at B3/RF4/YS1/ID2, K=511, N=3, with the same three group sizes and both weight widths. Two build jobs and two CPU cores are used; no unrelated jobs are stopped.

Hardware cost and timing have not yet been measured. Deferred mode adds per-bank fragment FIFOs/local partials, a scale cache, row-parallel scale-significand multipliers and wider exact output accumulators. Aligned mode needs one fragment stream after bank reduction, with more K windows when G is smaller than physical capacity. Both reuse the original product tables, weight buffers and DMA engine.

The existing Python cycle model has not yet been extended for these two scaling modes. The Python reference in this suite validates numerical outputs only; cycle counts in `cycles.csv` come from actual hardware counters.
