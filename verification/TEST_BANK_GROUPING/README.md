# Optional runtime B/YS bank grouping

Eight physical compute lanes, RF4, maximum YS4, shared DMA IDs8. Fixed baseline B2/YS4; flexible modes B8/YS1, B4/YS2 and B2/YS4. Matrix capacities R9/K129/N65. FP16 activations and signed W8/W4/W2. The build-time flag `FLEXIBLE_BANK_GROUPING` remains **false by default** because flexibility increases hardware cost.

## Actual Verilator results

**72/72 numerical/guard checks passed:** 15 fixed + 45 flexible finite-input matmuls, plus 3 fixed + 9 flexible FP16 special-value matmuls. Flexible grouping changes between matmuls in the same RISC-V executable. Actual hardware output words are compared bit-for-bit with independent host Python references; expected outputs are never placed in the ELF. Special checks cover NaN/Infinity, zero-weight products, signed/subnormal/finite values and row tails.

| Weights | Fixed active cycles | Python-selected split active cycles | Speedup |
|---|---:|---:|---:|
| W8 | 21,629 | 18,965 | 1.140x |
| W4 | 6,459 | 6,075 | 1.063x |
| W2 | 5,821 | 5,393 | 1.079x |

Shapes X[R,K] * W[K,N]: (1,32,16), (4,32,16), (5,33,9), (8,64,32), (3,129,17). Totals above sum these five shapes once per precision. All three supported splits were measured independently; Python controller-events-v7 predictions were saved before measurements. Its selected split was the measured fastest in 13/15 cases; maximum selection regret was 1.665%. This is a small prototype study, not a claim for every matrix or larger lane budget. CPU lookup/preload overhead is excluded from accelerator active cycles. Different executable layouts/cache state can cause small same-split cycle differences.

`comparison.csv` contains fixed, same-split flexible, model-selected and measured-best cycles. `model_schedule.json` contains the original predictions. `model_schedule.h` exports a small offline-generated C lookup for these shapes; Python does not run on the RISC-V CPU. The lookup itself was not the code timed in the test ELFs: the test enumerated all candidate splits, and the report selects the already-predicted entries.

Each flag/special folder contains full Verilator logs (commands, simulator/ELF/C hashes and every actual/reference output word), cycles.csv and records.json. The main flag folders include exact build Config.scala files. Original elapsed worker state is retained in status.json.

## Hardware trade-off

[Full paired Vivado logs/reports](../../Hardware_Estimation/LUMAX/b2_rf4_ys4_id8_bank_grouping/README.md): both routed and met 50 MHz. Accelerator LUTs increased 13,543 to 22,692 (+67.56%), registers 3,592 to 3,867 (+7.66%); BRAM4 and DSP0 were unchanged. Both vectorless dynamic estimates rounded to 33 mW. Estimated internal Fmax fell 87.719 to 80.645 MHz (-8.06%); these are internal path estimates, not verified higher system clocks. Power confidence is low; equal rounded values do not establish equal workload energy.

Generated RTL confirms eight product generators, eight activation banks and eight product-table banks in both builds. Weight SRAM bank count is four off versus sixteen on: two buffers must support maximum B8. The configurable implementation uses one DMA engine and a shared reduction tree. Output storage supports maximum YS4. It does not duplicate a complete accelerator.

## Sources and reproduction

`tested_source/` freezes exact RTL, production C/build helpers, model and ZCU106 platform source. `tested_runner.py` and `tested_special_checks.py` preserve the originally executed scripts unchanged, including historical absolute paths. `original_run_manifest.json` preserves original path-based provenance. `artifact_manifest.json` checksums this archive.

`run_test_bank_grouping.py` adapts paths to use the bundled tested source and the existing Chipyard 1.13.0 build/Verilator/Vivado helpers. It runs the main 60 cases and two implementations. The separately archived 12 special-value checks were an additional verification run and are not automatically replayed by that entry point. It requires the existing Chipyard toolchains, Vivado 2023.2 and frozen base assemblies/options under `/tmp/lumax-stable-baseline-20261114`; these large environment-specific dependencies are not committed. Override `LUMAX_CHIPYARD` and `LUMAX_FROZEN_BASELINE` when needed. Runs use two CPU cores, two make jobs and two Vivado threads; results are detached and SSH-safe.

```sh
cd /home/kioannou/LUMAX/verification/TEST_BANK_GROUPING
/home/kioannou/chipyard_1.13.0/.conda-env/bin/python run_test_bank_grouping.py start
# For a newly started run:
/home/kioannou/chipyard_1.13.0/.conda-env/bin/python run_test_bank_grouping.py status
```

While idle, software calls `lumax_select_bank_grouping(selected_ys)` before `preload_hw`. The existing row-configuration command carries the grouping; B becomes eight/selected_ys. Actual tail-row count does not change that grouping. Initial support requires power-of-two B/max-YS, dense FP16 and SRAM outputs; zero skipping, early weight-buffer release and burst reads must be disabled. No additional selection pipeline stages were added.
