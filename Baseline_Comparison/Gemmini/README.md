# Gemmini baseline comparison — Chipyard 1.13.0

This folder archives the original Gemmini and the optional FP16 × integer
version for a later comparison with LUMAX. **The platform is still Chipyard
1.13.0.** This commit archives sources and completed tests; it does not replace
the active Chipyard checkout or change the running LUMAX PPA sweep.

## Contents

- `original/src/`: Gemmini source snapshot from the working Chipyard checkout.
- `fpint/src/`: the modified Gemmini, including both classic and mixed routes.
- `fpint/chipyard/FPIntRocketConfig.scala`: the Rocket integration configuration.
- `software/test_fpint.c`: one test source covering W8, W4 and W2.
- `software/gemmini_fpint.h`, `software/params/` and `software/rocc-software/`:
  the required C interface, matching generated parameters and RoCC header.
- `logs/fp16_w8.log`, `fp16_w4.log`, `fp16_w2.log`: completed Verilator outputs.

The original snapshot is based on Gemmini commit
`25809f78323a729ef76fb68f3cedd8a24da2942b`, with the user's existing local
`Configs.scala` settings preserved in both snapshots. `LICENSE` retains the
original Gemmini source license. These snapshots use Chipyard's dependencies;
they are not independent standalone Scala projects.

## What changed

The Scala Boolean `mixedFpInt` defaults to **false**, retaining the existing
arithmetic. Enable the compatible configuration with:

```scala
new gemmini.GemminiFPIntConfig(enabled = true, weightBits = 4)
```

`weightBits` is statically selected as **8, 4 or 2** when building the RTL.
The enabled route uses FP16 activations, signed integer weights,
weight-stationary execution and FP32 partial sums/output. There is no runtime
precision switch.

Weights are stored in DRAM packed, low field first, with each K row byte aligned:
`ceil(N × weightBits / 8)` bytes per row. The existing DMA fetches them through
LOAD2 and unpacks them into the existing 16-bit scratchpad slots. Minimum bus
beat/alignment rounding still applies; SRAM capacity is unchanged.

The new product multiplier is **11 × weightBits** (FP16 mantissa × integer
magnitude), followed by FP32 addition. Its reduced width was checked with Yosys.
It does not use a general FP16 weight multiplier. No overall area, power or
clock-frequency improvement has been established.

Main changes are in `GemminiConfigs.scala`/`ConfigsFP.scala`, the new
`FPIntMac.scala`, the PE/mesh parameter propagation, and the DMA/load/WS-loop
files. The new C interface supports plain row-major matmul. Group scales,
zero-points, bias, transposed weights and convolution loops are outside this
mixed interface.

## Completed hardware tests

Actual Rocket + Gemmini Verilator runs: **4×4 WS array, 16 DMA IDs**.
Each precision ran eight matmuls sequentially in one RISC-V ELF. All **24/24
matmuls passed**, with **1,524/1,524 output elements exactly matching** independent
Python references, and intact output guards. The independent reference arrays
were not included in the RISC-V programs.

Inputs were random dyadic FP16 values and full-range signed integer weights.
These particular sums are exactly representable in FP32, so an exact comparison
is appropriate. This does not promise exact equality to a higher-precision
reference for every possible floating-point workload. The classic route has
separate unit regression coverage; no full classic application suite was rerun.

| R × K × N | W8 cycles | W4 cycles | W2 cycles | Result |
|---|---:|---:|---:|---|
| 1 × 4 × 4 | 474 | 403 | 398 | PASS |
| 2 × 7 × 5 | 353 | 306 | 304 | PASS |
| 1 × 32 × 32 | 795 | 754 | 755 | PASS |
| 3 × 33 × 17 | 734 | 659 | 659 | PASS |
| 2 × 128 × 128 | 6597 | 8559 | 8567 | PASS |
| 5 × 9 × 17 | 545 | 494 | 486 | PASS |
| 2 × 257 × 33 | 3564 | 3453 | 3379 | PASS |
| 1 × 4 × 4 | 271 | 231 | 229 | PASS |

Cycles are measured with `rdcycle` around the matmul wrapper, including
configuration, DMA/compute/store and the final fence. Packing and output printing
are excluded. The K=257 test limits K chunks to 16 array tiles, checking hardware
accumulator retention across five chunks. The final small matmul checks reuse.

The logs preserve every HW output as FP32 hexadecimal bits. The test transfers
raw output bits through HTIF; the original host runner rendered them as readable
hex in these archived logs. Only the requested sources, test, final logs and this
README are retained here, with the source license.

## Build the archived C test

Use the matching mixed hardware build. For example, for W4:

```bash
cd /home/kioannou/LUMAX/Baseline_Comparison/Gemmini
/home/kioannou/chipyard_1.13.0/.conda-env/riscv-tools/bin/riscv64-unknown-elf-gcc \
  -O2 -fno-common -fno-builtin-printf -DBAREMETAL=1 -specs=htif_nano.specs \
  -Isoftware -Isoftware/params/w4 -c software/test_fpint.c -o /tmp/gemmini_fpint.o
/home/kioannou/chipyard_1.13.0/.conda-env/riscv-tools/bin/riscv64-unknown-elf-gcc \
  -static -specs=htif_nano.specs /tmp/gemmini_fpint.o -lm -o /tmp/gemmini_fpint.riscv
```

Choose `params/w8` or `params/w2` for the corresponding hardware. The integration
configuration belongs in the Chipyard project when building a new simulator;
`-Dfpint.weightBits=4` selects W4 on the Java elaboration command. The existing
verified simulators remain outside this archive under
`/home/kioannou/gemmini_fpint/verification/fpint/build/soc_w{8,4,2}/`.
