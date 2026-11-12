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
| lumax_b16_rf8_ys1_id8 | TIMING_FAILED | post_route | 40,594 | 5,553 | 60 | 0 |
| lumax_b8_rf8_ys2_id8 | TIMING_FAILED | post_route | 30,582 | 5,218 | 48 | 0 |

| Design | Dynamic power W | Power confidence | Internal Fmax MHz | Setup slack ns | Hold slack ns |
|---|---:|---|---:|---:|---:|
| gemmini_w8 | 0.265 | Low | 64.008 | 0.145 | 0.010 |
| gemmini_w4 | 0.268 | Low | 66.774 | 0.117 | 0.010 |
| gemmini_w2 | 0.251 | Low | 67.231 | 0.229 | 0.010 |
| lumax_b16_rf8_ys1_id8 | 0.077 | Low | 34.357 | -9.108 | 0.009 |
| lumax_b8_rf8_ys2_id8 | 0.058 | Low | 33.901 | -9.500 | 0.009 |

Ratios below are LUMAX / edited Gemmini. Resource and power ratios below 1 use less.
Frequency ratios above 1 indicate a higher internal frequency estimate.

| LUMAX | Gemmini | LUT ratio | Register ratio | BRAM ratio | DSP ratio | Dynamic power ratio | Fmax ratio |
|---|---|---:|---:|---:|---:|---:|---:|
| lumax_b16_rf8_ys1_id8 | gemmini_w8 | 0.892× | 0.244× | 3.750× | 0.000× | 0.291× | 0.537× |
| lumax_b16_rf8_ys1_id8 | gemmini_w4 | 0.889× | 0.243× | 3.750× | 0.000× | 0.287× | 0.515× |
| lumax_b16_rf8_ys1_id8 | gemmini_w2 | 0.908× | 0.243× | 3.750× | 0.000× | 0.307× | 0.511× |
| lumax_b8_rf8_ys2_id8 | gemmini_w8 | 0.672× | 0.229× | 3.000× | 0.000× | 0.219× | 0.530× |
| lumax_b8_rf8_ys2_id8 | gemmini_w4 | 0.670× | 0.228× | 3.000× | 0.000× | 0.216× | 0.508× |
| lumax_b8_rf8_ys2_id8 | gemmini_w2 | 0.684× | 0.228× | 3.000× | 0.000× | 0.231× | 0.504× |
