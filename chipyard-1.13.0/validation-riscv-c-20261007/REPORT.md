# RISC-V pure-C reference versus LUMAX

All three isolated normal-Verilator tests PASS: input16, weight2/4/8,
X=1×4 and W=4×4, with 4/4 exact output matches in each test.
Hardware: b=2, RF=8, YS=1; the previously verified normal simulator.

The C test was compiled without LUMAX_PREGENERATED_TEST. The simulated
RISC-V CPU generated the input matrices, transposed the packed weights,
and ran matmul_sw_uniform() in pure C. Logs contain SW Mat-Mul Start,
the CPU multiplication cycle count, exact-match counts, and correctness PASS.
The accelerator consumed the same matrices and returned its own outputs.
No waveform or DRAMSim was used.

Validation used separate temporary sources, object files and test ELFs;
shared files belonging to the active sweep were not changed. Only the private
quiet-output filter was extended to include CPU multiplication messages.
The source and ELF hashes are in summary.json. Full artifacts are in
/tmp/lumax-riscv-c-reference-26ayyewj.

The updated sweep forces FAST_VECTORS=0 and REQUIRE_RISCV_REFERENCE=1.
It retains all 180 input16/weight2,4,8 cases across the six b/RF settings.
These three passing tests validate the CPU-versus-accelerator path; they
are not results for the complete configuration sweep.
