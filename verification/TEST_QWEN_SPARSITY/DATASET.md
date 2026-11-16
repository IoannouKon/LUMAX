# Actual Qwen coefficient samples

B5/RF16/YS4/ID16; 12 shapes per precision; FP16 activations. Actual contiguous checkpoint slices retain natural zero locations. No balancing or artificial zero insertion. Zero points are decoded using GPTQ v1 (+1) and per-K g_idx. Group scales/bias are not applied. These are raw integer-coefficient matmuls, not complete Qwen inference.

| Precision | Full checkpoint zeros | Sample zeros |
|---|---:|---:|
| W8 | 1.0398% | 1.0243% |
| W4 | 17.2787% | 17.1013% |
| W2 | 67.8935% | 67.2812% |

Official W8/W4 checkpoints; community W2 checkpoint. Exact revisions, tensor names, slice coordinates and per-case counts are in dataset.json. Weights are retained in weights.npz. The same inputs, weights and generated C must match by hash across flag settings. Both FPGA implementations use the same capacities and 50 MHz target; power will be vectorless. No DUT source changes are made by this runner.
