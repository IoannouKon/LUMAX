# ZCU106 LUMAX versus edited Gemmini

All designs use the same medium Rocket core, FPGA shell and 50 MHz target.
LUMAX uses FP16, Rin=8, Cin=Cout=1216. Gemmini matches the tested 4x4 WS configuration,
32 KiB scratchpad, 4 KiB accumulator and eight IDs per read/write pool.
LUMAX uses eight shared DMA IDs. Scaling and bias follow the tested settings.

Resources refer to the accelerator hierarchy. Each design folder also contains full-system reports.
Power uses vectorless activity. Vivado confidence is reported for each design.
The internal Fmax estimate is derived from the current routed implementation.

| Design | Status | Resource stage | LUTs | Registers | BRAM tiles | DSPs |
|---|---|---|---:|---:|---:|---:|
| gemmini_w8 | PASS | post_route | 45,504 | 22,800 | 16 | 167 |
| gemmini_w4 | PASS | post_route | 45,647 | 22,843 | 16 | 152 |
| gemmini_w2 | PASS | post_route | 44,720 | 22,850 | 16 | 152 |
| lumax_b6_rf4_ys8_id8 | FAILED | post_synthesis | — | 613,489 | 24 | 16 |
| lumax_b8_rf4_ys8_id8 | CANCELLED | — | — | — | — | — |
| lumax_b16_rf4_ys4_id8 | CANCELLED | — | — | — | — | — |
| lumax_b16_rf4_ys8_id8 | CANCELLED | — | — | — | — | — |

| Design | Dynamic power W | Power confidence | Internal Fmax MHz | Setup slack ns | Hold slack ns |
|---|---:|---|---:|---:|---:|
| gemmini_w8 | 0.265 | Low | 64.008 | 0.145 | 0.010 |
| gemmini_w4 | 0.268 | Low | 66.774 | 0.117 | 0.010 |
| gemmini_w2 | 0.251 | Low | 67.231 | 0.229 | 0.010 |
| lumax_b6_rf4_ys8_id8 | — | — | — | — | — |
| lumax_b8_rf4_ys8_id8 | — | — | — | — | — |
| lumax_b16_rf4_ys4_id8 | — | — | — | — | — |
| lumax_b16_rf4_ys8_id8 | — | — | — | — | — |

Ratios below are LUMAX / edited Gemmini. Resource and power ratios below 1 use less.
Frequency ratios above 1 indicate a higher internal frequency estimate.

| LUMAX | Gemmini | LUT ratio | Register ratio | BRAM ratio | DSP ratio | Dynamic power ratio | Fmax ratio |
|---|---|---:|---:|---:|---:|---:|---:|
| lumax_b6_rf4_ys8_id8 | gemmini_w8 | — | 26.907× | 1.500× | 0.096× | — | — |
| lumax_b6_rf4_ys8_id8 | gemmini_w4 | — | 26.857× | 1.500× | 0.105× | — | — |
| lumax_b6_rf4_ys8_id8 | gemmini_w2 | — | 26.849× | 1.500× | 0.105× | — | — |
| lumax_b8_rf4_ys8_id8 | gemmini_w8 | — | — | — | — | — | — |
| lumax_b8_rf4_ys8_id8 | gemmini_w4 | — | — | — | — | — | — |
| lumax_b8_rf4_ys8_id8 | gemmini_w2 | — | — | — | — | — | — |
| lumax_b16_rf4_ys4_id8 | gemmini_w8 | — | — | — | — | — | — |
| lumax_b16_rf4_ys4_id8 | gemmini_w4 | — | — | — | — | — | — |
| lumax_b16_rf4_ys4_id8 | gemmini_w2 | — | — | — | — | — | — |
| lumax_b16_rf4_ys8_id8 | gemmini_w8 | — | — | — | — | — | — |
| lumax_b16_rf4_ys8_id8 | gemmini_w4 | — | — | — | — | — | — |
| lumax_b16_rf4_ys8_id8 | gemmini_w2 | — | — | — | — | — | — |
