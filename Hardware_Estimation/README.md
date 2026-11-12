# Vivado hardware estimation — ZCU106

This folder replaces the earlier Yosys/OpenROAD PPA archive with the actual
Vivado FPGA flow and its saved results. The platform is Chipyard 1.13.0,
Vivado 2023.2, ZCU106 (`xczu7ev-ffvc1156-2-e`). No physical board is required.
The comparison runs target 50 MHz, FP16 activations and capacities
Rin=8, Cin=Cout=1216. LUMAX supports W8/W4/W2 in one hardware design;
edited Gemmini uses one separate build per weight precision.

## Folder contents

```text
Hardware_Estimation/
├── README.md
├── HW_resources_with_vivado_zcu106.py  # One configurable hardware build
├── run_vivado_comparison.py           # Detached multi-design sweep
├── lumax_zcu106_reports.tcl           # Synthesis, placement, routing, reports
├── gemmini_options.json              # Saved tested Gemmini settings/hashes
├── archive_manifest.json             # Copied report/source SHA256 provenance
├── platform/
│   ├── fpga_Makefile.fragment
│   └── src/main/
│       ├── scala/zcu106/              # Board harness, configuration and DDR
│       └── resources/zcu106/board.tcl
└── logs/
    ├── initial_run_20261111_082148_249124/
    ├── rf8_run_20261111_191949_049813/
    └── optimized_run_20261112_081942_242463/
```

Each archived run has `comparison.md`, CSV summaries, `state.json`, frozen
source/configuration provenance, and individual design folders. Each attempted
design has its available `vivado.log` and `reports/`. Cancelled designs may have
only a status entry. Reused Gemmini results are the same original measurements,
not additional independent runs. Binary checkpoints, generated build trees and
compiled tool assemblies remain outside Git.

## Saved measurements

| Design | Version | Result |
|---|---|---|
| Edited Gemmini W8/W4/W2 | 4x4 WS, 8 read + 8 write IDs | All routed and met 50 MHz |
| LUMAX B6/RF4/YS8/ID8 | Before resource optimizations | Synthesis saved; FPGA capacity exceeded |
| LUMAX B4/RF8/YS1/ID8 | Before resource optimizations | Routed and met 50 MHz |
| LUMAX B8/RF8/YS1/ID8 | Before resource optimizations | Routed; timing failed |
| LUMAX B16/RF8/YS1/ID8 | Before resource optimizations | Placement failed; synthesis saved |
| LUMAX B8/RF8/YS2/ID8 | Before resource optimizations | Placement failed; synthesis saved |
| LUMAX B4/RF8/YS4/ID8 | Before resource optimizations | LUT capacity exceeded; synthesis saved |
| LUMAX B16/RF8/YS1/ID8 | First six resource flags enabled | Routed; timing failed |
| LUMAX B8/RF8/YS2/ID8 | First six resource flags enabled | Routed; timing failed |

The initial B8/RF4/YS8, B16/RF4/YS4 and B16/RF4/YS8 attempts were cancelled
at the user's request after B6 exceeded capacity; they have no completed
implementation measurements. The later compact-validity-width correction is
being measured separately and is **not included in these archived results**.
New script runs snapshot the current working Chipyard sources; they do not
implicitly reproduce a historical source version. Consult each run's frozen
`sources/` and manifest when matching versions.

Reports distinguish accelerator-only utilization from full-system utilization
(Rocket, memory subsystem and FPGA shell). Completed implementations report LUTs,
registers, BRAM, DSPs, timing paths/slacks, routing status and power. Power is a
**vectorless estimate with low reported confidence** unless workload SAIF is
supplied. The internal Fmax estimate describes register-to-register timing in
that routed implementation, not a separately verified whole-system clock.
Synthesis-only failed runs do not provide valid routed timing or power results.
Compare resource numbers at the same implementation stage.

## Run one LUMAX design

On Cheetara, Chipyard's ZCU106 platform support is already installed. The archive
uses its existing Chipyard environment, FPGA shell and tool dependencies.
The Python runner reads the live working LUMAX source in that checkout.

```bash
cd /home/kioannou/LUMAX/Hardware_Estimation
export LUMAX_CHIPYARD=/home/kioannou/chipyard_1.13.0

python3 HW_resources_with_vivado_zcu106.py \
  --b 8 --rf 8 --ys 2 --ids 8 --activation fp16 \
  --rin 8 --cin 1216 --cout 1216 --clock-mhz 50 --jobs 4 \
  --output-dir /tmp/lumax-vivado-manual
```

The runner performs these steps automatically:

1. Pass the configuration as `LUMAX_ZCU106_*` environment variables.
2. Run Chipyard's `make SUB_PROJECT=LUMAXzcu106 ... fpga-rtl` target. This
   elaborates Chisel and generates the RTL, source manifest, constraints and IP
   Tcl. Chipyard does **not** run its full FPGA implementation/bitstream target.
3. Invoke Vivado in batch mode using the archived `lumax_zcu106_reports.tcl`.
4. Run `synth_design`, `opt_design`, `place_design`, `phys_opt_design`, and
   `route_design`, then write utilization, timing, power and route-status reports.
5. Cache a routed checkpoint keyed by RTL, constraints, Tcl and tool version.
   Add `--reimplement` to force a new implementation; `--rtl-only` stops after RTL.

The exact expanded commands are retained in each saved run's driver/assembly
logs. A direct RTL-generation example, with all settings explicit, is:

```bash
cd /home/kioannou/chipyard_1.13.0/fpga
LUMAX_ZCU106_B=8 LUMAX_ZCU106_RF=8 LUMAX_ZCU106_YS=2 \
LUMAX_ZCU106_IDS=8 LUMAX_ZCU106_RIN=8 LUMAX_ZCU106_CIN=1216 \
LUMAX_ZCU106_COUT=1216 LUMAX_ZCU106_FP16=true LUMAX_ZCU106_CLOCK_MHZ=50 \
LUMAX_ZCU106_CHIPYARD=/home/kioannou/chipyard_1.13.0 \
make SUB_PROJECT=LUMAXzcu106 CONFIG=LUMAXzcu106 \
  build_dir=/tmp/lumax-zcu106-rtl fpga-rtl
```

The implementation command generated by the Python runner has this form
(the paths below are placeholders supplied automatically by the runner):

```text
/tools/Xilinx/Vivado/2023.2/bin/vivado -mode batch -nojournal -nolog \
  -source lumax_zcu106_reports.tcl -tclargs \
  ZCU106FPGATestHarness RTL_MANIFEST COMMON_TCL_DIRECTORY BOARD_TCL \
  ROUTED_CHECKPOINT OPTIONAL_SAIF OPTIONAL_SAIF_SCOPE
```

Use the Python runner for implementation: it sets the required environment,
absolute paths, tool version, cache key and working directory consistently.

## Run a detached LUMAX/Gemmini sweep

Edit `LUMAX_CONFIGURATIONS`, clock and capacities at the top of the sweep script.
Defaults are B4/RF8/YS1, B8/RF8/YS1, B16/RF8/YS1, B8/RF8/YS2 and B4/RF8/YS4,
all ID8, plus the three edited-Gemmini builds.

```bash
cd /home/kioannou/LUMAX/Hardware_Estimation
export LUMAX_CHIPYARD=/home/kioannou/chipyard_1.13.0
python3 run_vivado_comparison.py start
# SSH can then be closed. After reconnecting:
python3 run_vivado_comparison.py status
# If needed:
python3 run_vivado_comparison.py stop
python3 run_vivado_comparison.py resume
```

New outputs go into `runs/run_DATE/`; archived `logs/` are preserved. The sweep
builds a private FPGA assembly, snapshots LUMAX and the checked-in tested Gemmini
sources, and invokes the single-design runner for each configuration. Gemmini
assets come from `../Baseline_Comparison/Gemmini/fpint/src/main/scala/`, checked
against `gemmini_options.json`; its configuration generator is the committed
Gemmini `verification/TEST_LLMS/run_test_llms.py`. Temporary Gemmini test state
is not required. Cached Scala compiler/plugin JARs must be available at the
paths recorded in `gemmini_options.json` on this server.

The underlying build and implementation flow is the same as the scripts used
for the saved runs. Archive adaptations only change Chipyard/Tcl/output and
Gemmini asset paths; hardware calculations and Vivado implementation steps are
unchanged.

## ZCU106 support in another Chipyard 1.13.0 checkout

The `platform/` directory contains the exact board-support files used here.
Copy its `src/main/scala/zcu106/` and `src/main/resources/zcu106/` directories
into the corresponding `fpga/src/main/` locations and add the block in
`platform/fpga_Makefile.fragment` to `fpga/Makefile` if absent. The existing
Chipyard FPGA-shells and VCU118 SD boot-ROM sources are also required.
Set `LUMAX_CHIPYARD` to that prepared checkout. These setup steps are already
done in `/home/kioannou/chipyard_1.13.0`; this archive operation did not alter it.
