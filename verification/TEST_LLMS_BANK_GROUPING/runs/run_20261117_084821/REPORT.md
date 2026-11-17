# TEST_LLMS: arbitrary-B flexible grouping and edited Gemmini

FP16 activations; W8/W4/W2. LUMAX has 16 shared DMA slots, Gemmini has 8 read + 8 write slots. Both interfaces use 8-byte beats and maximum 8-byte transactions. Slot partitioning remains different. Both Verilator and FPGA wrappers use the same medium Rocket core and no TileLink monitors. All new cycle numbers below come from actual make-built Verilator runs; model-selected splits are predictions checked numerically, not claims of globally measured optimality. No historical ID16-per-pool/64-byte Gemmini cycles are mixed into this batch.

Common twelve TEST_LLMS shapes: R=4/5/8, K/N=(224,224),(224,32),(224,1216),(1216,224). Same saved operands/hashes across all accelerators. LUMAX uses exact FP32 output-word checks; Gemmini retains its predeclared FP32 forward-error bound. Active cycles exclude software model/lookup/preload overhead.

| Design | Numerical status | W8 cycles | W4 cycles | W2 cycles |
|---|---|---:|---:|---:|
| lumax_b5_rf16_ys4_id16_flex1 | PASS | 1055755 | 640989 | 579173 |
| lumax_b14_rf16_ys8_id16_flex1 | PASS | 391920 | 261244 | 176765 |
| lumax_b5_rf16_ys4_id16_flex0 | PASS | 1080676 | 653267 | 643321 |
| lumax_b14_rf16_ys8_id16_flex0 | PASS | 398908 | 274741 | 187049 |
| gemmini_d4_sp32_acc4_id8p_req8_w8 | PASS | 981516 | n/a | n/a |
| gemmini_d4_sp32_acc4_id8p_req8_w4 | PASS | n/a | 949528 | n/a |
| gemmini_d4_sp32_acc4_id8p_req8_w2 | PASS | n/a | n/a | 949526 |
| gemmini_d8_sp32_acc4_id8p_req8_w8 | PASS | 642925 | n/a | n/a |
| gemmini_d8_sp32_acc4_id8p_req8_w4 | PASS | n/a | 398266 | n/a |
| gemmini_d8_sp32_acc4_id8p_req8_w2 | PASS | n/a | n/a | 358159 |

Speedup = new Gemmini cycles / LUMAX cycles, for the same precision and twelve shapes.

| LUMAX | Gemmini array | W8 speedup | W4 speedup | W2 speedup |
|---|---|---:|---:|---:|
| lumax_b5_rf16_ys4_id16_flex1 | 4x4 | 0.930x | 1.481x | 1.639x |
| lumax_b5_rf16_ys4_id16_flex1 | 8x8 | 0.609x | 0.621x | 0.618x |
| lumax_b14_rf16_ys8_id16_flex1 | 4x4 | 2.504x | 3.635x | 5.372x |
| lumax_b14_rf16_ys8_id16_flex1 | 8x8 | 1.640x | 1.524x | 2.026x |
| lumax_b5_rf16_ys4_id16_flex0 | 4x4 | 0.908x | 1.454x | 1.476x |
| lumax_b5_rf16_ys4_id16_flex0 | 8x8 | 0.595x | 0.610x | 0.557x |
| lumax_b14_rf16_ys8_id16_flex0 | 4x4 | 2.461x | 3.456x | 5.076x |
| lumax_b14_rf16_ys8_id16_flex0 | 8x8 | 1.612x | 1.450x | 1.915x |

## ZCU106 Vivado

50 MHz target, two threads, full system and accelerator reports. Internal Fmax is routed path headroom, not a verified higher clock. Vectorless dynamic power is not workload energy. A synthesis-only resource result does not establish placeability, timing or power.

| Design | Status | Stage | LUT | FF | BRAM | DSP | Dynamic W | Internal Fmax MHz |
|---|---|---|---:|---:|---:|---:|---:|---:|
| lumax_b5_rf16_ys4_id16_flex1 | PASS | post_route | 56391.0 | 7333.0 | 96.0 | 0.0 | 0.092 | 67.096 |
| lumax_b14_rf16_ys8_id16_flex1 | FAILED | post_synthesis | 667948.0 | 31045.0 | 149.0 | 0.0 | None | None |
| lumax_b5_rf16_ys4_id16_flex0 | PASS | post_route | 33552.0 | 6485.0 | 66.0 | 0.0 | 0.06 | 78.983 |
| lumax_b14_rf16_ys8_id16_flex0 | FAILED | post_synthesis | 457557.0 | 23530.0 | 149.0 | 0.0 | None | None |
| gemmini_d4_sp32_acc4_id8p_req8_w8 | pending | pending | pending | pending | pending | pending | pending | pending |
| gemmini_d4_sp32_acc4_id8p_req8_w4 | pending | pending | pending | pending | pending | pending | pending | pending |
| gemmini_d4_sp32_acc4_id8p_req8_w2 | pending | pending | pending | pending | pending | pending | pending | pending |
| gemmini_d8_sp32_acc4_id8p_req8_w8 | pending | pending | pending | pending | pending | pending | pending | pending |
| gemmini_d8_sp32_acc4_id8p_req8_w4 | pending | pending | pending | pending | pending | pending | pending | pending |
| gemmini_d8_sp32_acc4_id8p_req8_w2 | pending | pending | pending | pending | pending | pending | pending | pending |
