# Group-scaling RTL validation

**Final result: 68/68 matmuls passed; 1,604 exact FP32 output-element matches.**

Main run: `runs/run_20261117_200641`.

Real make-built Verilator executables run compiled RISC-V ELFs. Expected outputs remain on the host and are not embedded in the ELF. Hardware output bits are compared against an independent exact integer reference. No cycle-model predictions or Vivado results are included here.

| Mode | Matmuls | Exact FP32 outputs checked | Result |
|---|---:|---:|---|
| off | 8 | 224 | PASS |
| aligned | 24 | 672 | PASS |
| deferred | 24 | 672 | PASS |

Main configuration: B4/RF4/YS2/ID8. Scaled tests cover W2/W4, G32/G64/G128, partial K groups, multiple activation-row groups, odd output columns, group crossings inside bank spans and consecutive matmuls with varying dimensions/scales. Values include finite FP16 normals/subnormals, both signs, zero and large scales. All checked outputs match bit for bit; absolute value error is zero.

The off-mode checks use the original unscaled reference. They are functional controls, not a performance baseline for the different scaled operation.

## Identical scaled workloads: measured active cycles

Each row sums the four main shapes. Aligned and deferred use identical input, weight and scale hashes. These are small functional workloads, not representative full LLM performance measurements. Lower cycles is better; no frequency claim is made.

| G | Weight bits | Aligned cycles | Deferred cycles | Deferred cycle reduction |
|---:|---:|---:|---:|---:|
| 32 | 2 | 5,482 | 4,491 | 18.08% |
| 32 | 4 | 6,328 | 5,332 | 15.74% |
| 64 | 2 | 4,903 | 4,445 | 9.34% |
| 64 | 4 | 5,697 | 5,330 | 6.44% |
| 128 | 2 | 4,667 | 4,464 | 4.35% |
| 128 | 4 | 5,410 | 5,324 | 1.59% |

Active cycles include metadata preload (in LoadX), activation loading, LUT generation, combined weight/selection/scaling work and output storage. Standalone LoadW and Select counters overlap and must not be added independently to the combined counter.

## Extra boundary checks

The `runs/run_boundary_k511` follow-up passed all 12 checks: B3/RF4/YS1/ID2, X=1x511, W=511x3, both weights and all three group sizes. This checks non-power-of-two B, single-row control, carry-preserving ceil(K/G), partial bank tails and multiple windows.

## Limits

Finite FP16 X/scales only; signed symmetric W2/W4, zero-point zero. Exact fixed48 accumulation with one final FP32 round. W8 scaling, asymmetric zero points, framework-specific intermediate rounding, special FP values, flexible bank grouping, zero skipping, DMA bursts and early weight release are not validated combinations. Group modes require 8-byte DMA beats. No PPA/clock/energy results have been measured for these new modes. The production Python cycle model does not yet model them.

Each mode preserves its own frozen Scala/software tree and source hashes. See the main run `source_revisions.md` for the small carry-width correction made between aligned and deferred builds. The follow-up uses the final live source.

## Boundary correction found during validation

The first B3/YS1/K511 run stopped at `Weight request exceeded its column window`. Investigation found that the inherited packed-weight ceiling expression `K + elements_per_byte - 1` could wrap the dimension-width counter at K511. The group-enabled path now keeps the carry; the flags-off expression remains unchanged. The corrected aligned boundary run passed all six cases. The corrected deferred boundary run also passed all six cases. The original failure remains in the boundary run log rather than being discarded.

## Files changed for this implementation

Four Scala files: `Config.scala`, `LUMAX_Top.scala`, `FP16.scala`, and new `GroupScaleEngine.scala`. Two software/build files: `Linear-sw.c` and `build.sh`. The test runner, exact reference, documentation and validation logs are under `verification/TEST_GROUP_SCALING/`. The source and validation artifacts are archived together in this repository.
