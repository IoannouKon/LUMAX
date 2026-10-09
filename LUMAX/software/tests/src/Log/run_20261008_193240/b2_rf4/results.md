# Configuration b=2, RF=4

**30 PASS, 0 FAIL.**

[All results](../total_results.md) · [CSV](results.csv) · [Hardware configuration](design/Config.scala)

| Input X | Weights W | A bits | W bits | Result | Exact matches | HW cycles | Seconds | Log |
|---|---|---:|---:|---|---|---:|---:|---|
| 1×4 | 4×4 | 16 | 2 | PASS | 4/4 | 97 | 17.41 | [log](tests/X1x4_W4x4_A16_W2.log) |
| 1×4 | 4×4 | 16 | 4 | PASS | 4/4 | 98 | 17.51 | [log](tests/X1x4_W4x4_A16_W4.log) |
| 1×4 | 4×4 | 16 | 8 | PASS | 4/4 | 227 | 17.31 | [log](tests/X1x4_W4x4_A16_W8.log) |
| 1×8 | 8×8 | 16 | 2 | PASS | 8/8 | 162 | 17.37 | [log](tests/X1x8_W8x8_A16_W2.log) |
| 1×8 | 8×8 | 16 | 4 | PASS | 8/8 | 174 | 17.48 | [log](tests/X1x8_W8x8_A16_W4.log) |
| 1×8 | 8×8 | 16 | 8 | PASS | 8/8 | 405 | 17.24 | [log](tests/X1x8_W8x8_A16_W8.log) |
| 1×16 | 16×16 | 16 | 2 | PASS | 16/16 | 341 | 18.23 | [log](tests/X1x16_W16x16_A16_W2.log) |
| 1×16 | 16×16 | 16 | 4 | PASS | 16/16 | 386 | 18.19 | [log](tests/X1x16_W16x16_A16_W4.log) |
| 1×16 | 16×16 | 16 | 8 | PASS | 16/16 | 984 | 18.27 | [log](tests/X1x16_W16x16_A16_W8.log) |
| 1×32 | 32×32 | 16 | 2 | PASS | 32/32 | 913 | 18.83 | [log](tests/X1x32_W32x32_A16_W2.log) |
| 1×32 | 32×32 | 16 | 4 | PASS | 32/32 | 961 | 19.78 | [log](tests/X1x32_W32x32_A16_W4.log) |
| 1×32 | 32×32 | 16 | 8 | PASS | 32/32 | 2599 | 22.28 | [log](tests/X1x32_W32x32_A16_W8.log) |
| 1×64 | 64×16 | 16 | 2 | PASS | 16/16 | 842 | 18.39 | [log](tests/X1x64_W64x16_A16_W2.log) |
| 1×64 | 64×16 | 16 | 4 | PASS | 16/16 | 934 | 19.21 | [log](tests/X1x64_W64x16_A16_W4.log) |
| 1×64 | 64×16 | 16 | 8 | PASS | 16/16 | 3634 | 21.29 | [log](tests/X1x64_W64x16_A16_W8.log) |
| 1×64 | 64×64 | 16 | 2 | PASS | 64/64 | 2811 | 21.80 | [log](tests/X1x64_W64x64_A16_W2.log) |
| 1×64 | 64×64 | 16 | 4 | PASS | 64/64 | 2851 | 22.81 | [log](tests/X1x64_W64x64_A16_W4.log) |
| 1×64 | 64×64 | 16 | 8 | PASS | 64/64 | 7122 | 25.41 | [log](tests/X1x64_W64x64_A16_W8.log) |
| 1×128 | 128×32 | 16 | 2 | PASS | 32/32 | 2666 | 20.95 | [log](tests/X1x128_W128x32_A16_W2.log) |
| 1×128 | 128×32 | 16 | 4 | PASS | 32/32 | 2823 | 22.51 | [log](tests/X1x128_W128x32_A16_W4.log) |
| 1×128 | 128×32 | 16 | 8 | PASS | 32/32 | 9179 | 25.99 | [log](tests/X1x128_W128x32_A16_W8.log) |
| 1×128 | 128×128 | 16 | 2 | PASS | 128/128 | 9551 | 28.14 | [log](tests/X1x128_W128x128_A16_W2.log) |
| 1×128 | 128×128 | 16 | 4 | PASS | 128/128 | 9743 | 33.04 | [log](tests/X1x128_W128x128_A16_W4.log) |
| 1×128 | 128×128 | 16 | 8 | PASS | 128/128 | 23366 | 44.80 | [log](tests/X1x128_W128x128_A16_W8.log) |
| 1×256 | 256×256 | 16 | 2 | PASS | 256/256 | 35517 | 53.22 | [log](tests/X1x256_W256x256_A16_W2.log) |
| 1×256 | 256×256 | 16 | 4 | PASS | 256/256 | 37194 | 68.40 | [log](tests/X1x256_W256x256_A16_W4.log) |
| 1×256 | 256×256 | 16 | 8 | PASS | 256/256 | 84276 | 113.51 | [log](tests/X1x256_W256x256_A16_W8.log) |
| 1×512 | 512×512 | 16 | 2 | PASS | 512/512 | 136627 | 139.27 | [log](tests/X1x512_W512x512_A16_W2.log) |
| 1×512 | 512×512 | 16 | 4 | PASS | 512/512 | 145120 | 202.48 | [log](tests/X1x512_W512x512_A16_W4.log) |
| 1×512 | 512×512 | 16 | 8 | PASS | 512/512 | 331685 | 391.47 | [log](tests/X1x512_W512x512_A16_W8.log) |
