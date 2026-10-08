# LUMAX configuration sweep

Normal Verilator; exact integer output comparisons; no waveforms.

| b | RF | n | PASS | FAIL | TIMEOUT | BUILD_FAIL | ERROR |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 2 | 4 | 256 | 27 | 3 | 0 | 0 | 0 |
| 2 | 8 | 512 | 27 | 3 | 0 | 0 | 0 |
| 4 | 4 | 256 | 27 | 3 | 0 | 0 | 0 |
| 4 | 8 | 512 | 27 | 3 | 0 | 0 | 0 |
| 8 | 4 | 256 | 27 | 3 | 0 | 0 | 0 |
| 8 | 8 | 512 | 27 | 3 | 0 | 0 | 0 |

Completed 180/180 tests. Counts: {'PASS': 162, 'FAIL': 18}.

Each geometry tests 1×K times K×K for K=4,8,16,32,64,128,256,512,
plus 1×64 times 64×16 and 1×128 times 128×32.
Every shape uses input16 and weight2/weight4/weight8; output32.

See summary.csv for individual results and log paths.
Hardware cycle counters are saved in each test log and the hw_* columns in summary.csv.
hw_cycles = Load X + Generate BRAMs + combined Load W/Select + Store O hardware counters.
Load W and Select counters describe overlapping activity; do not add them again to hw_cycles.
Hardware counters exclude CPU reference calculation and host simulation time; seconds is wall-clock runner time.
FAIL means an output mismatch or simulator failure. TIMEOUT is inconclusive.
BUILD_FAIL means the simulator could not build; ERROR means another runner error.
The default memory model is used. Python prepares the inputs, packed weights, and exact integer reference on the host.
