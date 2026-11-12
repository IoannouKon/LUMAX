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
| lumax_b4_rf8_ys1_id8 | PASS | post_route | 80,228 | 78,352 | 10 | 26 |
| lumax_b8_rf8_ys1_id8 | TIMING_FAILED | post_route | 95,844 | 79,224 | 20 | 38 |
| lumax_b16_rf8_ys1_id8 | FAILED | post_synthesis | 217,584 | 80,836 | 40 | 60 |
| lumax_b8_rf8_ys2_id8 | FAILED | post_synthesis | 262,762 | 155,926 | 24 | 38 |
| lumax_b4_rf8_ys4_id8 | FAILED | post_synthesis | 344,471 | 306,652 | 16 | 26 |

| Design | Dynamic power W | Power confidence | Internal Fmax MHz | Setup slack ns | Hold slack ns |
|---|---:|---|---:|---:|---:|
| gemmini_w8 | 0.265 | Low | 64.008 | 0.145 | 0.010 |
| gemmini_w4 | 0.268 | Low | 66.774 | 0.117 | 0.010 |
| gemmini_w2 | 0.251 | Low | 67.231 | 0.229 | 0.010 |
| lumax_b4_rf8_ys1_id8 | 0.235 | Low | 51.916 | 0.051 | 0.009 |
| lumax_b8_rf8_ys1_id8 | 0.275 | Low | 47.792 | -0.926 | 0.004 |
| lumax_b16_rf8_ys1_id8 | — | — | — | — | — |
| lumax_b8_rf8_ys2_id8 | — | — | — | — | — |
| lumax_b4_rf8_ys4_id8 | — | — | — | — | — |

Ratios below are LUMAX / edited Gemmini. Resource and power ratios below 1 use less.
Frequency ratios above 1 indicate a higher internal frequency estimate.

| LUMAX | Gemmini | LUT ratio | Register ratio | BRAM ratio | DSP ratio | Dynamic power ratio | Fmax ratio |
|---|---|---:|---:|---:|---:|---:|---:|
| lumax_b4_rf8_ys1_id8 | gemmini_w8 | 1.763× | 3.436× | 0.625× | 0.156× | 0.887× | 0.811× |
| lumax_b4_rf8_ys1_id8 | gemmini_w4 | 1.758× | 3.430× | 0.625× | 0.171× | 0.877× | 0.777× |
| lumax_b4_rf8_ys1_id8 | gemmini_w2 | 1.794× | 3.429× | 0.625× | 0.171× | 0.936× | 0.772× |
| lumax_b8_rf8_ys1_id8 | gemmini_w8 | 2.106× | 3.475× | 1.250× | 0.228× | 1.038× | 0.747× |
| lumax_b8_rf8_ys1_id8 | gemmini_w4 | 2.100× | 3.468× | 1.250× | 0.250× | 1.026× | 0.716× |
| lumax_b8_rf8_ys1_id8 | gemmini_w2 | 2.143× | 3.467× | 1.250× | 0.250× | 1.096× | 0.711× |
| lumax_b16_rf8_ys1_id8 | gemmini_w8 | — | — | — | — | — | — |
| lumax_b16_rf8_ys1_id8 | gemmini_w4 | — | — | — | — | — | — |
| lumax_b16_rf8_ys1_id8 | gemmini_w2 | — | — | — | — | — | — |
| lumax_b8_rf8_ys2_id8 | gemmini_w8 | — | — | — | — | — | — |
| lumax_b8_rf8_ys2_id8 | gemmini_w4 | — | — | — | — | — | — |
| lumax_b8_rf8_ys2_id8 | gemmini_w2 | — | — | — | — | — | — |
| lumax_b4_rf8_ys4_id8 | gemmini_w8 | — | — | — | — | — | — |
| lumax_b4_rf8_ys4_id8 | gemmini_w4 | — | — | — | — | — | — |
| lumax_b4_rf8_ys4_id8 | gemmini_w2 | — | — | — | — | — | — |
