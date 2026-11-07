# Configuration b=8, RF=4

**30 PASS, 0 FAIL.**

[All results](../total_results.md) · [CSV](results.csv) · [Hardware configuration](design/Config.scala)

| Input X | Weights W | A bits | W bits | Result | Exact matches | HW cycles | Seconds | Log |
|---|---|---:|---:|---|---|---:|---:|---|
| 1×4 | 4×4 | 16 | 2 | PASS | 4/4 | 96 | 17.62 | [log](tests/X1x4_W4x4_A16_W2.log) |
| 1×4 | 4×4 | 16 | 4 | PASS | 4/4 | 96 | 17.91 | [log](tests/X1x4_W4x4_A16_W4.log) |
| 1×4 | 4×4 | 16 | 8 | PASS | 4/4 | 160 | 17.76 | [log](tests/X1x4_W4x4_A16_W8.log) |
| 1×8 | 8×8 | 16 | 2 | PASS | 8/8 | 152 | 17.70 | [log](tests/X1x8_W8x8_A16_W2.log) |
| 1×8 | 8×8 | 16 | 4 | PASS | 8/8 | 152 | 18.00 | [log](tests/X1x8_W8x8_A16_W4.log) |
| 1×8 | 8×8 | 16 | 8 | PASS | 8/8 | 198 | 17.73 | [log](tests/X1x8_W8x8_A16_W8.log) |
| 1×16 | 16×16 | 16 | 2 | PASS | 16/16 | 256 | 18.50 | [log](tests/X1x16_W16x16_A16_W2.log) |
| 1×16 | 16×16 | 16 | 4 | PASS | 16/16 | 283 | 18.83 | [log](tests/X1x16_W16x16_A16_W4.log) |
| 1×16 | 16×16 | 16 | 8 | PASS | 16/16 | 436 | 18.65 | [log](tests/X1x16_W16x16_A16_W8.log) |
| 1×32 | 32×32 | 16 | 2 | PASS | 32/32 | 541 | 19.31 | [log](tests/X1x32_W32x32_A16_W2.log) |
| 1×32 | 32×32 | 16 | 4 | PASS | 32/32 | 587 | 19.65 | [log](tests/X1x32_W32x32_A16_W4.log) |
| 1×32 | 32×32 | 16 | 8 | PASS | 32/32 | 1086 | 21.76 | [log](tests/X1x32_W32x32_A16_W8.log) |
| 1×64 | 64×16 | 16 | 2 | PASS | 16/16 | 428 | 18.89 | [log](tests/X1x64_W64x16_A16_W2.log) |
| 1×64 | 64×16 | 16 | 4 | PASS | 16/16 | 535 | 18.95 | [log](tests/X1x64_W64x16_A16_W4.log) |
| 1×64 | 64×16 | 16 | 8 | PASS | 16/16 | 1297 | 20.40 | [log](tests/X1x64_W64x16_A16_W8.log) |
| 1×64 | 64×64 | 16 | 2 | PASS | 64/64 | 1293 | 21.05 | [log](tests/X1x64_W64x64_A16_W2.log) |
| 1×64 | 64×64 | 16 | 4 | PASS | 64/64 | 1581 | 22.47 | [log](tests/X1x64_W64x64_A16_W4.log) |
| 1×64 | 64×64 | 16 | 8 | PASS | 64/64 | 2919 | 24.56 | [log](tests/X1x64_W64x64_A16_W8.log) |
| 1×128 | 128×32 | 16 | 2 | PASS | 32/32 | 1094 | 21.19 | [log](tests/X1x128_W128x32_A16_W2.log) |
| 1×128 | 128×32 | 16 | 4 | PASS | 32/32 | 1582 | 21.69 | [log](tests/X1x128_W128x32_A16_W4.log) |
| 1×128 | 128×32 | 16 | 8 | PASS | 32/32 | 3408 | 24.17 | [log](tests/X1x128_W128x32_A16_W8.log) |
| 1×128 | 128×128 | 16 | 2 | PASS | 128/128 | 3361 | 26.74 | [log](tests/X1x128_W128x128_A16_W2.log) |
| 1×128 | 128×128 | 16 | 4 | PASS | 128/128 | 4594 | 31.60 | [log](tests/X1x128_W128x128_A16_W4.log) |
| 1×128 | 128×128 | 16 | 8 | PASS | 128/128 | 9919 | 41.24 | [log](tests/X1x128_W128x128_A16_W8.log) |
| 1×256 | 256×256 | 16 | 2 | PASS | 256/256 | 10748 | 44.56 | [log](tests/X1x256_W256x256_A16_W2.log) |
| 1×256 | 256×256 | 16 | 4 | PASS | 256/256 | 16195 | 61.12 | [log](tests/X1x256_W256x256_A16_W4.log) |
| 1×256 | 256×256 | 16 | 8 | PASS | 256/256 | 37110 | 98.36 | [log](tests/X1x256_W256x256_A16_W8.log) |
| 1×512 | 512×512 | 16 | 2 | PASS | 512/512 | 38013 | 106.76 | [log](tests/X1x512_W512x512_A16_W2.log) |
| 1×512 | 512×512 | 16 | 4 | PASS | 512/512 | 64763 | 176.22 | [log](tests/X1x512_W512x512_A16_W4.log) |
| 1×512 | 512×512 | 16 | 8 | PASS | 512/512 | 157465 | 336.65 | [log](tests/X1x512_W512x512_A16_W8.log) |
