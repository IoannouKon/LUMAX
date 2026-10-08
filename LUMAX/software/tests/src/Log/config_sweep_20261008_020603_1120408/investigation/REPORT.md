# Investigation of the 18 failed LUMAX sweep cases

Sweep: `config_sweep_20261008_020603_1120408`.
Observed result: 162 PASS, 18 FAIL. All failures are 1x512 times 512x512,
with activation16 and weight2/4/8 across the six hardware configurations.

Two RTL width defects explain the observed failures. The current RTL and
Python vector-generator SHA-256 hashes match the sources recorded in the sweep
manifest. A host model of the actual address truncation and signed reduction
reproduces all 18 exact-match counts and all 144 mismatch values saved in the
logs. This investigation does not replace or reinterpret the original FAILs.

## 1. The weight-column base index wraps at 65536 elements

Locations, relative to Chipyard:

- `generators/LUMAX/src/main/scala/LUMAX_Top.scala:238`: `mul1` is a 16-bit register.
- `generators/LUMAX/src/main/scala/ChunkInfoModules.scala:200`: request-side `mul1` is a 16-bit input.
- `generators/LUMAX/src/main/scala/ChunkInfoModules.scala:232`: response-side `mul1` is a 16-bit input.
- `generators/LUMAX/src/main/scala/LUMAX_Top.scala:1765`: the next weight-column base is `(j_w + 1) * cin_reg`.

Weights are stored by output column. With CIN=512, output column 128 needs a
base element index of 128*512=65536. All three 16-bit paths truncate that value
to zero, so the accelerator starts reading weights for column 0 again. The
wrong base repeats every 128 columns, regardless of b or RF.

For W2, the saved hardware output at column 128 is -895183. That is exactly
Python's expected output at column 0; the correct column-128 result is -103868.
The next seven recorded mismatches likewise equal Python columns 1 through 7.
The same correspondence holds for W4 and W8, apart from the separate reduction
overflow described below.

This explains why the square tests through K=256 pass: their largest flattened
element index is 256*256-1=65535. K=512 crosses the limit.

For W2 and W4 the result is 128/512 exact matches. The default W8 test pattern
repeats every 256 output columns, so this alias happens to yield 256/512 matches
in five configurations. Those extra matches do not mean their addresses are correct.

Required correction: derive the flattened matrix-index width from the supported
matrix dimensions and widen `mul1` consistently in the top-level register and
both chunk-info module interfaces. The present Cin=Cout=1000 limits need 20 bits
for the full matrix index. Widening only the register would leave interface truncation.

## 2. The parallel signed sum can overflow before reaching the output register

Locations, relative to Chipyard:

- `generators/LUMAX/src/main/scala/LUMAX_Top.scala:1298`: zero extension of the 24-bit product produces a 25-bit signed value.
- `generators/LUMAX/src/main/scala/LUMAX_Top.scala:1310`: `signedValues.reduce(_ + _)` keeps the signed addition width.

The parallel reduction therefore retains 25 bits even when it combines eight
lanes. Values can overflow that reduction before assignment to the 32-bit
output accumulator. The wider destination cannot recover the lost carry bits.

This additional defect is visible in the saved b=8, RF=8, W8 case. At output
column 121, which is before the address-wrap boundary:

- Python expected: 10914364.
- Hardware observed: -22640068.
- Difference: -33554432, exactly -2^25.

The same 25-bit wrap model explains the other recorded pre-boundary mismatches
and the final 242/512 match count, instead of the 256/512 expected from address
aliasing alone. The exact signed reduction groups follow the existing lane and
RF mapping in the RTL.

Required correction: widen signed operands before the parallel reduction, or
use additions that retain carry growth. For the tested configurations, padding
the operands to the 32-bit output width before reduction is sufficient. Check
the bound against the supported parallelism when generalizing the design.

## Independent effect of the two corrections

These are host-model predictions, not results from rebuilt RTL:

| Cases | Original observed/model | Address width corrected | Reduction width corrected | Both corrected |
|---|---:|---:|---:|---:|
| All six configurations, W2 | 128/512 | 512/512 | 128/512 | 512/512 |
| All six configurations, W4 | 128/512 | 512/512 | 128/512 | 512/512 |
| Five configurations other than b8/RF8, W8 | 256/512 | 512/512 | 256/512 | 512/512 |
| b8/RF8, W8 | 242/512 | 486/512 | 256/512 | 512/512 |

Both corrections are necessary. In particular, correcting addresses alone
leaves 26 wrong outputs in the b8/RF8 W8 model.

## Reproduce the evidence

From `generators/LUMAX/software/tests/src`:

```bash
python3 Log/config_sweep_20261008_020603_1120408/investigation/reproduce_failures.py
```

The script checks source hashes, reconstructs the saved seed/pattern vectors,
decodes the exact packed signed weights, models both RTL truncations, verifies
every recorded mismatch and match count, and writes `evidence.csv` beside itself.
It also checks that untruncated dot products of the packed hardware inputs
match the Python reference for every output in all 18 cases.

The original RTL and simulator binaries have not been modified in this
investigation. After implementing the width corrections, rebuild the affected
simulators and rerun the 18 failing cases plus the full 180-case regression.
Retain the original sweep as evidence rather than overwriting its results.
