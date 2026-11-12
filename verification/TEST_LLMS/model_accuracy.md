# TEST_LLMS verification and measured cycle comparison

Updated 2026-11-12T10:57:06.978293+02:00.

Actual make-built Verilator + RISC-V binaries; independent exact host Python references. No cycle-model predictions are presented as measurements. Gemmini baseline is the existing edited-Gemmini TEST_LLMS Verilator run.

| Design | W8 active / speedup | W4 active / speedup | W2 active / speedup | PASS |
|---|---:|---:|---:|---:|
| B6/RF4/YS8/ID8 | 869,068 / 1.097x | 388,943 / 2.439x | 356,812 / 2.659x | 36/36 |
| B8/RF4/YS8/ID8 | 749,335 / 1.272x | 333,846 / 2.842x | 270,485 / 3.508x | 36/36 |
| B16/RF4/YS4/ID8 | 788,928 / 1.208x | 352,927 / 2.688x | 283,462 / 3.347x | 36/36 |
| B16/RF4/YS8/ID8 | 552,694 / 1.724x | 255,718 / 3.710x | 206,526 / 4.594x | 36/36 |

Speedup = summed Gemmini controller-active cycles / summed LUMAX active cycles over the same 12 shapes per precision. Ratios are withheld until a precision group fully passes. LUMAX active=Load X + generation + combined LoadW/Select + Store O; overlapping diagnostics are not added twice. Gemmini uses separate 8 read + 8 write IDs; LUMAX uses 8 shared IDs. Counter coverage and hardware resources differ.

Production edits: one HW file (src/main/scala/LUMAX_Top.scala); zero production C/software files; one new verification runner (verification/TEST_LLMS/run_lumax_weight_loader.py). Live Config.scala, DMA.scala, LUT arithmetic, Gemmini sources and production Model.py are unchanged. Build configuration is generated in private cache snapshots.

Logic example: after all requests for column A are accepted, B requests may start in the other free existing buffer while A responses are outstanding. A separate ordered response cursor writes each response to its own reserved buffer. The final response commits SRAM data and ready together; selection starts next cycle. A buffer is released only when its final selection read is captured.

Small directed gates include unaligned packed columns, partial K/output tails, multiple K windows, multiple row groups and sequential matmuls in one ELF. Full-run numerical references execute only in host Python; expected output arrays are never placed in the RISC-V ELF.

Archive layout matches TEST_SAFE: one runner, one Verilator log, one cycle CSV,
and this report. The CSV/log include 204/204 PASS: 144 LLM matmuls across four
FP16 configurations plus 60 INT16/FP16 bootstrap and configuration gates.
Recorded worker elapsed time: 158872.6 seconds (44.13 hours).

The file is named model_accuracy.md to follow TEST_SAFE's layout. No cycle-model
accuracy percentage was calculated for this archived run. All speedups above
compare actual Verilator measurements with saved edited-Gemmini measurements;
they are not Python cycle predictions. Numerical references are independent
host-Python dot products and matched the actual LUMAX FP32/INT32 output words.

Run in a configured Chipyard 1.13.0 checkout:
`python3 /home/kioannou/LUMAX/verification/TEST_LLMS/run_test_llms.py start`.
Use `status`, `resume` or `stop` with the same script. Hardware configurations
are at the top. Future runs create runs/run_DATE folders containing
verilator.log, cycles.csv and model_accuracy.md, retaining the checked-in run.
The runner uses the already tracked OLD_VERIFICATION helpers and the current
working Chipyard's hardware/production C helpers; it never launches Gemmini.
The checked-in cycles.csv supplies the original 36 Gemmini reference cycles,
seeds and hashes, so the uncommitted Gemmini runner is not required.
