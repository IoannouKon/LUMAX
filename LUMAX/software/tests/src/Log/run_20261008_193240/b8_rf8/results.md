# Configuration b=8, RF=8

**30 PASS, 0 FAIL.**

[All results](../total_results.md) · [CSV](results.csv) · [Hardware configuration](design/Config.scala)

| Input X | Weights W | A bits | W bits | Result | Exact matches | HW cycles | Seconds | Log |
|---|---|---:|---:|---|---|---:|---:|---|
| 1×4 | 4×4 | 16 | 2 | PASS | 4/4 | 96 | 17.47 | [log](tests/X1x4_W4x4_A16_W2.log) |
| 1×4 | 4×4 | 16 | 4 | PASS | 4/4 | 96 | 17.43 | [log](tests/X1x4_W4x4_A16_W4.log) |
| 1×4 | 4×4 | 16 | 8 | PASS | 4/4 | 160 | 18.03 | [log](tests/X1x4_W4x4_A16_W8.log) |
| 1×8 | 8×8 | 16 | 2 | PASS | 8/8 | 152 | 17.71 | [log](tests/X1x8_W8x8_A16_W2.log) |
| 1×8 | 8×8 | 16 | 4 | PASS | 8/8 | 152 | 17.48 | [log](tests/X1x8_W8x8_A16_W4.log) |
| 1×8 | 8×8 | 16 | 8 | PASS | 8/8 | 198 | 17.67 | [log](tests/X1x8_W8x8_A16_W8.log) |
| 1×16 | 16×16 | 16 | 2 | PASS | 16/16 | 256 | 18.38 | [log](tests/X1x16_W16x16_A16_W2.log) |
| 1×16 | 16×16 | 16 | 4 | PASS | 16/16 | 283 | 18.86 | [log](tests/X1x16_W16x16_A16_W4.log) |
| 1×16 | 16×16 | 16 | 8 | PASS | 16/16 | 435 | 18.60 | [log](tests/X1x16_W16x16_A16_W8.log) |
| 1×32 | 32×32 | 16 | 2 | PASS | 32/32 | 541 | 18.86 | [log](tests/X1x32_W32x32_A16_W2.log) |
| 1×32 | 32×32 | 16 | 4 | PASS | 32/32 | 588 | 19.80 | [log](tests/X1x32_W32x32_A16_W4.log) |
| 1×32 | 32×32 | 16 | 8 | PASS | 32/32 | 1087 | 22.05 | [log](tests/X1x32_W32x32_A16_W8.log) |
| 1×64 | 64×16 | 16 | 2 | PASS | 16/16 | 428 | 18.96 | [log](tests/X1x64_W64x16_A16_W2.log) |
| 1×64 | 64×16 | 16 | 4 | PASS | 16/16 | 535 | 18.96 | [log](tests/X1x64_W64x16_A16_W4.log) |
| 1×64 | 64×16 | 16 | 8 | PASS | 16/16 | 1292 | 20.89 | [log](tests/X1x64_W64x16_A16_W8.log) |
| 1×64 | 64×64 | 16 | 2 | PASS | 64/64 | 1295 | 21.50 | [log](tests/X1x64_W64x64_A16_W2.log) |
| 1×64 | 64×64 | 16 | 4 | PASS | 64/64 | 1581 | 22.65 | [log](tests/X1x64_W64x64_A16_W4.log) |
| 1×64 | 64×64 | 16 | 8 | PASS | 64/64 | 2793 | 24.98 | [log](tests/X1x64_W64x64_A16_W8.log) |
| 1×128 | 128×32 | 16 | 2 | PASS | 32/32 | 1094 | 21.15 | [log](tests/X1x128_W128x32_A16_W2.log) |
| 1×128 | 128×32 | 16 | 4 | PASS | 32/32 | 1582 | 22.30 | [log](tests/X1x128_W128x32_A16_W4.log) |
| 1×128 | 128×32 | 16 | 8 | PASS | 32/32 | 3263 | 24.48 | [log](tests/X1x128_W128x32_A16_W8.log) |
| 1×128 | 128×128 | 16 | 2 | PASS | 128/128 | 3362 | 26.88 | [log](tests/X1x128_W128x128_A16_W2.log) |
| 1×128 | 128×128 | 16 | 4 | PASS | 128/128 | 4595 | 32.05 | [log](tests/X1x128_W128x128_A16_W4.log) |
| 1×128 | 128×128 | 16 | 8 | PASS | 128/128 | 9387 | 40.88 | [log](tests/X1x128_W128x128_A16_W8.log) |
| 1×256 | 256×256 | 16 | 2 | PASS | 256/256 | 10749 | 44.47 | [log](tests/X1x256_W256x256_A16_W2.log) |
| 1×256 | 256×256 | 16 | 4 | PASS | 256/256 | 16195 | 62.54 | [log](tests/X1x256_W256x256_A16_W4.log) |
| 1×256 | 256×256 | 16 | 8 | PASS | 256/256 | 35106 | 98.96 | [log](tests/X1x256_W256x256_A16_W8.log) |
| 1×512 | 512×512 | 16 | 2 | PASS | 512/512 | 37976 | 106.59 | [log](tests/X1x512_W512x512_A16_W2.log) |
| 1×512 | 512×512 | 16 | 4 | PASS | 512/512 | 64967 | 178.30 | [log](tests/X1x512_W512x512_A16_W4.log) |
| 1×512 | 512×512 | 16 | 8 | PASS | 512/512 | 151628 | 333.91 | [log](tests/X1x512_W512x512_A16_W8.log) |
