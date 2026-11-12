# Edited Gemmini TEST_LLMS

Completed Chipyard 1.13.0 Verilator run: **36/36 PASS** on 2026-11-08. Recorded elapsed time: **7772.044 seconds (2 hours 9 minutes)**.

Configuration: FP16 activations, FP32 output/accumulation, signed W8/W4/W2 in three separate RTL builds. Weight-stationary 4x4 array, 32 KiB scratchpad, 4 KiB accumulator, 64-bit DMA, eight read IDs plus eight write IDs; scale units and bias input disabled.

Qwen2.5-0.5B-inspired projections use K/N dimensions reduced by four. These are independent matmuls, not full-model inference. The archived LUMAX TEST_LLMS replays these exact operand seeds and hashes.

| R x K x N | W8 active cycles | W4 active cycles | W2 active cycles |
|---|---:|---:|---:|
| 4 x 224 x 224 | 19,443 | 18,229 | 18,229 |
| 4 x 224 x 32 | 3,712 | 3,713 | 3,713 |
| 4 x 224 x 1216 | 98,414 | 98,261 | 98,300 |
| 4 x 1216 x 224 | 99,692 | 96,528 | 96,631 |
| 5 x 224 x 224 | 30,730 | 30,730 | 30,723 |
| 5 x 224 x 32 | 5,196 | 5,197 | 5,196 |
| 5 x 224 x 1216 | 166,277 | 166,310 | 166,318 |
| 5 x 1216 x 224 | 161,653 | 161,664 | 161,671 |
| 8 x 224 x 224 | 32,550 | 32,548 | 32,547 |
| 8 x 224 x 32 | 5,523 | 5,523 | 5,525 |
| 8 x 224 x 1216 | 166,616 | 166,642 | 166,642 |
| 8 x 1216 x 224 | 163,306 | 163,425 | 163,385 |
| **Total** | **953,112** | **948,770** | **948,880** |

Controller-active cycles exclude CPU submission and final-fence overhead. The CSV also preserves core cycles, exclusive state combinations, overlapping load/execute/store diagnostics, seeds, hashes, guards and numerical errors. Overlapping busy counts must not be added together.

Correctness compares real Gemmini RTL output against an independent host-Python reference using the predeclared FP32 accumulation error bound (`fp32_bound`). Gemmini rounds FP32 partial additions; LUMAX rounds its exact wide accumulation once. PASS therefore does not require every Gemmini output word to be bit-identical to the exact reference. No tolerance was increased after observing results, and expected outputs were not embedded in the RISC-V ELF.

Files: `run_test_llms.py`, `verilator.log`, `cycles.csv`, and this README. The log and CSV are byte-identical copies of the completed run. The runner only adjusts archive-relative asset/replay paths; arithmetic, configurations and checking logic are unchanged.

From this folder, use `python3 run_test_llms.py check` to check prerequisites or `python3 run_test_llms.py start` to launch a new detached run. Use `status` or `stop` afterward. Configurations are at the top of the script. New outputs go into `runs/run_DATE/`; the archived outputs above are retained. By default new runs generate new random operands and log their seeds.

Source result directory: `/home/kioannou/chipyard_1.13.0/generators/LUMAX/verification/TEST_LLMS/runs/run_20261108_131221_994034`.
