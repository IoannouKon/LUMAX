# Configuration b=4, RF=4

**30 PASS, 0 FAIL.**

[All results](../total_results.md) · [CSV](results.csv) · [Hardware configuration](design/Config.scala)

| Input X | Weights W | A bits | W bits | Result | Exact matches | HW cycles | Seconds | Log |
|---|---|---:|---:|---|---|---:|---:|---|
| 1×4 | 4×4 | 16 | 2 | PASS | 4/4 | 96 | 17.18 | [log](tests/X1x4_W4x4_A16_W2.log) |
| 1×4 | 4×4 | 16 | 4 | PASS | 4/4 | 96 | 17.81 | [log](tests/X1x4_W4x4_A16_W4.log) |
| 1×4 | 4×4 | 16 | 8 | PASS | 4/4 | 160 | 17.57 | [log](tests/X1x4_W4x4_A16_W8.log) |
| 1×8 | 8×8 | 16 | 2 | PASS | 8/8 | 153 | 17.51 | [log](tests/X1x8_W8x8_A16_W2.log) |
| 1×8 | 8×8 | 16 | 4 | PASS | 8/8 | 154 | 17.44 | [log](tests/X1x8_W8x8_A16_W4.log) |
| 1×8 | 8×8 | 16 | 8 | PASS | 8/8 | 266 | 17.60 | [log](tests/X1x8_W8x8_A16_W8.log) |
| 1×16 | 16×16 | 16 | 2 | PASS | 16/16 | 273 | 17.69 | [log](tests/X1x16_W16x16_A16_W2.log) |
| 1×16 | 16×16 | 16 | 4 | PASS | 16/16 | 310 | 18.05 | [log](tests/X1x16_W16x16_A16_W4.log) |
| 1×16 | 16×16 | 16 | 8 | PASS | 16/16 | 572 | 18.12 | [log](tests/X1x16_W16x16_A16_W8.log) |
| 1×32 | 32×32 | 16 | 2 | PASS | 32/32 | 661 | 19.00 | [log](tests/X1x32_W32x32_A16_W2.log) |
| 1×32 | 32×32 | 16 | 4 | PASS | 32/32 | 707 | 18.90 | [log](tests/X1x32_W32x32_A16_W4.log) |
| 1×32 | 32×32 | 16 | 8 | PASS | 32/32 | 1518 | 21.68 | [log](tests/X1x32_W32x32_A16_W8.log) |
| 1×64 | 64×16 | 16 | 2 | PASS | 16/16 | 554 | 18.63 | [log](tests/X1x64_W64x16_A16_W2.log) |
| 1×64 | 64×16 | 16 | 4 | PASS | 16/16 | 633 | 19.11 | [log](tests/X1x64_W64x16_A16_W4.log) |
| 1×64 | 64×16 | 16 | 8 | PASS | 16/16 | 2016 | 20.60 | [log](tests/X1x64_W64x16_A16_W8.log) |
| 1×64 | 64×64 | 16 | 2 | PASS | 64/64 | 1755 | 21.04 | [log](tests/X1x64_W64x64_A16_W2.log) |
| 1×64 | 64×64 | 16 | 4 | PASS | 64/64 | 1857 | 22.99 | [log](tests/X1x64_W64x64_A16_W4.log) |
| 1×64 | 64×64 | 16 | 8 | PASS | 64/64 | 3769 | 24.85 | [log](tests/X1x64_W64x64_A16_W8.log) |
| 1×128 | 128×32 | 16 | 2 | PASS | 32/32 | 1578 | 20.79 | [log](tests/X1x128_W128x32_A16_W2.log) |
| 1×128 | 128×32 | 16 | 4 | PASS | 32/32 | 1737 | 22.04 | [log](tests/X1x128_W128x32_A16_W4.log) |
| 1×128 | 128×32 | 16 | 8 | PASS | 32/32 | 4775 | 24.73 | [log](tests/X1x128_W128x32_A16_W8.log) |
| 1×128 | 128×128 | 16 | 2 | PASS | 128/128 | 5391 | 26.53 | [log](tests/X1x128_W128x128_A16_W2.log) |
| 1×128 | 128×128 | 16 | 4 | PASS | 128/128 | 5524 | 32.39 | [log](tests/X1x128_W128x128_A16_W4.log) |
| 1×128 | 128×128 | 16 | 8 | PASS | 128/128 | 12072 | 41.08 | [log](tests/X1x128_W128x128_A16_W8.log) |
| 1×256 | 256×256 | 16 | 2 | PASS | 256/256 | 19005 | 47.00 | [log](tests/X1x256_W256x256_A16_W2.log) |
| 1×256 | 256×256 | 16 | 4 | PASS | 256/256 | 19190 | 61.91 | [log](tests/X1x256_W256x256_A16_W4.log) |
| 1×256 | 256×256 | 16 | 8 | PASS | 256/256 | 43484 | 100.16 | [log](tests/X1x256_W256x256_A16_W8.log) |
| 1×512 | 512×512 | 16 | 2 | PASS | 512/512 | 70827 | 116.33 | [log](tests/X1x512_W512x512_A16_W2.log) |
| 1×512 | 512×512 | 16 | 4 | PASS | 512/512 | 74913 | 178.57 | [log](tests/X1x512_W512x512_A16_W4.log) |
| 1×512 | 512×512 | 16 | 8 | PASS | 512/512 | 178890 | 340.99 | [log](tests/X1x512_W512x512_A16_W8.log) |
