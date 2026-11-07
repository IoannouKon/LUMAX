# Run 2026-10-08 19:32:40

**180 PASS, 0 FAIL.**

[All individual results as CSV](total_results.csv)

| Configuration | b | RF | n | Tests | PASS | FAIL | Individual results |
|---|---:|---:|---:|---:|---:|---:|---|
| b2_rf4 | 2 | 4 | 256 | 30 | 30 | 0 | [Results](b2_rf4/results.md) · [CSV](b2_rf4/results.csv) |
| b2_rf8 | 2 | 8 | 512 | 30 | 30 | 0 | [Results](b2_rf8/results.md) · [CSV](b2_rf8/results.csv) |
| b4_rf4 | 4 | 4 | 256 | 30 | 30 | 0 | [Results](b4_rf4/results.md) · [CSV](b4_rf4/results.csv) |
| b4_rf8 | 4 | 8 | 512 | 30 | 30 | 0 | [Results](b4_rf8/results.md) · [CSV](b4_rf8/results.csv) |
| b8_rf4 | 8 | 4 | 256 | 30 | 30 | 0 | [Results](b8_rf4/results.md) · [CSV](b8_rf4/results.csv) |
| b8_rf8 | 8 | 8 | 512 | 30 | 30 | 0 | [Results](b8_rf8/results.md) · [CSV](b8_rf8/results.csv) |
| **Total** | | | | **180** | **180** | **0** | |

Actual Verilator executions with exact signed integer comparisons against a host Python reference. Input16, weight2/4/8, output32; YS=1, seed=1, pattern=0.

Square sizes K=4,8,16,32,64,128,256,512, plus 1×64 times 64×16 and 1×128 times 128×32.

The 18 previously failing cases ran first; the other 162 started after those passed. Six verified designs built earlier with make were reused. The hardware has the 20-bit matrix index and 32-bit parallel reduction fixes.

Each configuration contains its individual results, tests/ logs and design/ build evidence. Run metadata and tested source snapshots are in artifacts/. Original paths inside raw logs preserve execution-time provenance.
