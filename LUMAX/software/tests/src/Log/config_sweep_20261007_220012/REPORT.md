# LUMAX configuration sweep

Normal Verilator; exact integer output comparisons; no waveforms.

| b | RF | n | PASS | FAIL | TIMEOUT | BUILD_FAIL | ERROR |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 2 | 4 | 256 | 21 | 3 | 0 | 0 | 0 |
| 2 | 8 | 512 | 0 | 0 | 0 | 0 | 0 |
| 4 | 4 | 256 | 0 | 0 | 0 | 0 | 0 |
| 4 | 8 | 512 | 0 | 0 | 0 | 0 | 0 |
| 8 | 4 | 256 | 0 | 0 | 0 | 0 | 0 |
| 8 | 8 | 512 | 0 | 0 | 0 | 0 | 0 |

Completed 24/180 tests. Counts: {'PASS': 21, 'FAIL': 3}.

Each geometry tests 1×K times K×K for K=4,8,16,32,64,128,256,512,
plus 1×64 times 64×16 and 1×128 times 128×32.
Every shape uses input16 and weight2/weight4/weight8; output32.

See summary.csv for individual results and log paths.
FAIL means an output mismatch or simulator failure. TIMEOUT is inconclusive.
BUILD_FAIL means the simulator could not build; ERROR means another runner error.
The default memory model is used. Python prepares the inputs, packed weights, and exact integer reference on the host.
