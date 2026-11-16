# Qwen weight-sparsity test: zero skipping off/on

Configuration: **LUMAX B5/RF16/YS4/ID16**, FP16 activations, W8/W4/W2. Capacities Rin8/Cin1216/Cout1216. All 72 Verilator matmuls passed against exact host Python references, including boundary guards.

Actual contiguous coefficient slices from official Qwen2.5-0.5B-Instruct GPTQ W8/W4 and community irish-quant W2 retain their natural zero locations. Early/middle/late layers 0/12/23 cover all seven projection types across twelve shapes: R=4/5/8 with KxN=224x224,224x32,224x1216,1216x224. Activations are deterministic random finite FP16. Group scales and bias are excluded; this is raw FP16 times centered signed integer matmul, not complete Qwen inference. No zero balancing, pruning or rate adjustment was performed.

| Weights | Sample zeros | Flag off cycles | Flag on cycles | Change |
|---|---:|---:|---:|---|
| W8 | 1.0243% | 1,066,488 | 1,114,206 | 4.47% slower |
| W4 | 17.1013% | 652,375 | 657,673 | 0.81% slower |
| W2 | 67.2812% | 643,362 | 334,881 | 47.95% fewer cycles |

Cycles are summed active accelerator cycles from real make-built Verilator runs. Each flag folder contains cycles.csv, records.json and verilator.log (commands, simulator/ELF/C hashes and every actual/reference output word). cycles_comparison.csv contains all 36 matched comparisons. Identical operand and generated-C hashes were required across flag settings. Python reference arrays were not embedded in the RISC-V test ELF.

## Tested version and reproduction

These runs predate the later configuration cleanups and parameter renames. tested_source/scala is the exact historical RTL source, with the six resource flags enabled, BRAM_OUTPUTS=true, EARLY_WEIGHT_BUFFER_RELEASE=false and WEIGHT_DMA_BURSTS=false. Only SKIP_ZERO_WEIGHTS differs between compared builds. tested_source/software preserves the production C/helpers used. tested_runner.py is the original executed runner; run_test_qwen_sparsity.py adapts paths to replay the bundled dataset and tested sources. It does not benchmark the current live generator by accident. dataset.json records checkpoint revisions, tensor names, slice coordinates and zero counts; plan.json records seeds; weights.npz preserves the sampled signed coefficients.

The runner requires the working Chipyard 1.13.0 toolchain/verification helpers and matching frozen base assemblies/compiler metadata from the original server. Base assemblies and build caches are intentionally not stored in Git. LUMAX_FROZEN_BASELINE can override the default /tmp/lumax-stable-baseline-20261114/frozen path; its parent options.json must retain the original compiler-jar metadata expected by the runner. Historical result analysis needs none of those build dependencies.

```bash
/home/kioannou/chipyard_1.13.0/.conda-env/bin/python /home/kioannou/LUMAX/verification/TEST_QWEN_SPARSITY/run_test_qwen_sparsity.py start
```

Runs detach from SSH, use two build jobs/two Vivado threads, and keep results in runs/. For a newly started run, replace start with status to inspect progress. Both hardware implementations are attempted after numerical verification. Existing historical results above are preserved.

[Paired ZCU106 hardware results](../../Hardware_Estimation/LUMAX/b5_rf16_ys4_id16_zero_skip/README.md). artifact_manifest.json checksums the archived package.
