# Configuration b=4, RF=8

**30 PASS, 0 FAIL.**

[All results](../total_results.md) · [CSV](results.csv) · [Hardware configuration](design/Config.scala)

| Input X | Weights W | A bits | W bits | Result | Exact matches | HW cycles | Seconds | Log |
|---|---|---:|---:|---|---|---:|---:|---|
| 1×4 | 4×4 | 16 | 2 | PASS | 4/4 | 96 | 17.50 | [log](tests/X1x4_W4x4_A16_W2.log) |
| 1×4 | 4×4 | 16 | 4 | PASS | 4/4 | 96 | 17.15 | [log](tests/X1x4_W4x4_A16_W4.log) |
| 1×4 | 4×4 | 16 | 8 | PASS | 4/4 | 160 | 17.54 | [log](tests/X1x4_W4x4_A16_W8.log) |
| 1×8 | 8×8 | 16 | 2 | PASS | 8/8 | 153 | 17.44 | [log](tests/X1x8_W8x8_A16_W2.log) |
| 1×8 | 8×8 | 16 | 4 | PASS | 8/8 | 154 | 17.46 | [log](tests/X1x8_W8x8_A16_W4.log) |
| 1×8 | 8×8 | 16 | 8 | PASS | 8/8 | 266 | 17.97 | [log](tests/X1x8_W8x8_A16_W8.log) |
| 1×16 | 16×16 | 16 | 2 | PASS | 16/16 | 273 | 18.63 | [log](tests/X1x16_W16x16_A16_W2.log) |
| 1×16 | 16×16 | 16 | 4 | PASS | 16/16 | 310 | 17.92 | [log](tests/X1x16_W16x16_A16_W4.log) |
| 1×16 | 16×16 | 16 | 8 | PASS | 16/16 | 572 | 18.26 | [log](tests/X1x16_W16x16_A16_W8.log) |
| 1×32 | 32×32 | 16 | 2 | PASS | 32/32 | 661 | 19.48 | [log](tests/X1x32_W32x32_A16_W2.log) |
| 1×32 | 32×32 | 16 | 4 | PASS | 32/32 | 707 | 19.04 | [log](tests/X1x32_W32x32_A16_W4.log) |
| 1×32 | 32×32 | 16 | 8 | PASS | 32/32 | 1354 | 21.64 | [log](tests/X1x32_W32x32_A16_W8.log) |
| 1×64 | 64×16 | 16 | 2 | PASS | 16/16 | 554 | 18.57 | [log](tests/X1x64_W64x16_A16_W2.log) |
| 1×64 | 64×16 | 16 | 4 | PASS | 16/16 | 633 | 18.93 | [log](tests/X1x64_W64x16_A16_W4.log) |
| 1×64 | 64×16 | 16 | 8 | PASS | 16/16 | 1833 | 20.61 | [log](tests/X1x64_W64x16_A16_W8.log) |
| 1×64 | 64×64 | 16 | 2 | PASS | 64/64 | 1755 | 21.78 | [log](tests/X1x64_W64x64_A16_W2.log) |
| 1×64 | 64×64 | 16 | 4 | PASS | 64/64 | 1857 | 22.61 | [log](tests/X1x64_W64x64_A16_W4.log) |
| 1×64 | 64×64 | 16 | 8 | PASS | 64/64 | 3452 | 24.82 | [log](tests/X1x64_W64x64_A16_W8.log) |
| 1×128 | 128×32 | 16 | 2 | PASS | 32/32 | 1578 | 20.79 | [log](tests/X1x128_W128x32_A16_W2.log) |
| 1×128 | 128×32 | 16 | 4 | PASS | 32/32 | 1737 | 22.09 | [log](tests/X1x128_W128x32_A16_W4.log) |
| 1×128 | 128×32 | 16 | 8 | PASS | 32/32 | 4477 | 24.40 | [log](tests/X1x128_W128x32_A16_W8.log) |
| 1×128 | 128×128 | 16 | 2 | PASS | 128/128 | 5391 | 27.24 | [log](tests/X1x128_W128x128_A16_W2.log) |
| 1×128 | 128×128 | 16 | 4 | PASS | 128/128 | 5524 | 32.49 | [log](tests/X1x128_W128x128_A16_W4.log) |
| 1×128 | 128×128 | 16 | 8 | PASS | 128/128 | 10993 | 41.04 | [log](tests/X1x128_W128x128_A16_W8.log) |
| 1×256 | 256×256 | 16 | 2 | PASS | 256/256 | 19005 | 47.48 | [log](tests/X1x256_W256x256_A16_W2.log) |
| 1×256 | 256×256 | 16 | 4 | PASS | 256/256 | 19190 | 62.98 | [log](tests/X1x256_W256x256_A16_W4.log) |
| 1×256 | 256×256 | 16 | 8 | PASS | 256/256 | 39139 | 99.80 | [log](tests/X1x256_W256x256_A16_W8.log) |
| 1×512 | 512×512 | 16 | 2 | PASS | 512/512 | 70827 | 117.07 | [log](tests/X1x512_W512x512_A16_W2.log) |
| 1×512 | 512×512 | 16 | 4 | PASS | 512/512 | 71455 | 178.21 | [log](tests/X1x512_W512x512_A16_W4.log) |
| 1×512 | 512×512 | 16 | 8 | PASS | 512/512 | 161607 | 334.02 | [log](tests/X1x512_W512x512_A16_W8.log) |
