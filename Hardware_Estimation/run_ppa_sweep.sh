#!/usr/bin/env bash
# LUMAX one-parameter-at-a-time PPA sweep. This wrapper calls run_ppa.py.
# Usage:
#   bash run_ppa_sweep.sh                         # foreground
#   bash run_ppa_sweep.sh start                   # detached; survives SSH exit
#   bash run_ppa_sweep.sh status
#   bash run_ppa_sweep.sh stop                    # stop this sweep from SSH
# Optional run_ppa.py tool/IP flags may follow run/start, for example:
#   bash run_ppa_sweep.sh start --macro-bundle /path/to/srams.json
#
# Only this shell source is added. On execution, all outputs live in PPA_logs/
# beside run_ppa.py: dated per-design logs + summary.csv. Shared work/cache
# stays outside this Git repository; override with LUMAX_PPA_WORK_ROOT.
# Existing verified builds are reused by run_ppa.py's content-checked cache.
# RF16 is experimental: it must pass elaboration and the physical flow first.
# PPA currently stops after placement/CTS; wire RC, timing and power are
# estimates before routing. Global/detailed routing and OpenRCX are disabled.
# Post-CTS timing repair and verbose intermediate timing reports are disabled;
# the final area/timing/power analysis is still enabled.
set -Eeuo pipefail

# -------------------------- EDIT SETTINGS HERE ---------------------------
BASE_B=4
BASE_RF=8
BASE_YS=2
BASE_IDS=4
B_VALUES=(2 4 8)
RF_VALUES=(4 8 16)
IDS_VALUES=(1 2 4 6 8)
YS_VALUES=(1 2 4)

ACTIVATIONS=fp16
MAX_R=8
MAX_K=4097
MAX_M=128
PERIOD_NS=20
UNCERTAINTY_NS=1
UTILIZATION=0.40
ACTIVITY=0.10                 # statistical activity, not workload power
DUTY=0.50
THREADS=4
TIMEOUT_SECONDS=14400        # per underlying tool/stage, not whole sweep
# Pre-route timing/power warnings stay visible, but do not stop the sweep.
# A failed/blocked baseline (nonzero process exit) still stops other designs.
STOP_IF_BASELINE_NEEDS_REVIEW=0
PYTHON_BIN="${PYTHON_BIN:-python3}"
MACRO_BUNDLE="${LUMAX_SRAM_BUNDLE:-}"
# -----------------------------------------------------------------------

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SELF="${SCRIPT_DIR}/$(basename -- "${BASH_SOURCE[0]}")"
PPA="${SCRIPT_DIR}/run_ppa.py"
LOG_ROOT="${SCRIPT_DIR}/PPA_logs"
# Kept across dated sweeps so identical configurations need no rebuild.
WORK_ROOT="${LUMAX_PPA_WORK_ROOT:-${HOME}/.cache/lumax-ppa}"

MODE="${1:-run}"
case "$MODE" in
    run|start|status|stop) if (($#)); then shift; fi ;;
    --*) MODE=run ;;
    *) printf 'Usage: bash %s [run|start|status|stop] [run_ppa.py options]\n' "$SELF" >&2; exit 2 ;;
esac

if [[ "$MODE" == status ]]; then
    if [[ ! -f "${LOG_ROOT}/latest_run.txt" ]]; then
        printf 'No PPA sweep has been started.\n'
        exit 0
    fi
    IFS= read -r LAST_RUN < "${LOG_ROOT}/latest_run.txt"
    printf 'Latest sweep: %s\n' "$LAST_RUN"
    if flock -n "${LOG_ROOT}/sweep.lock" true; then
        printf 'Runner: inactive\n'
    else
        printf 'Runner: active (lock held)\n'
    fi
    if [[ -f "${LAST_RUN}/summary.csv" ]]; then
        cat -- "${LAST_RUN}/summary.csv"
    fi
    exit 0
fi

if [[ "$MODE" == stop ]]; then
    # Stop only the recorded PPA runner for this work directory. Its SIGTERM
    # handler terminates the current tool process group and saves an interrupted
    # report; the sweep then stops because that invocation exits nonzero.
    "$PYTHON_BIN" -B - "$LOG_ROOT" "$WORK_ROOT" "$PPA" <<'PY'
import fcntl, json, os, pathlib, signal, sys, time
root, work, script = map(pathlib.Path, sys.argv[1:])
lockfile = root/'sweep.lock'
if not lockfile.exists():
    print('No PPA sweep is active.')
    sys.exit(0)
with lockfile.open('a') as lock:
    def inactive():
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False
    if inactive():
        print('No PPA sweep is active.')
        sys.exit(0)
    state = json.loads((work/'latest.json').read_text())
    pid = int(state['pid'])
    if pid <= 1:
        sys.exit('Invalid recorded runner PID; nothing was signalled.')
    proc = pathlib.Path('/proc')/str(pid)/'cmdline'
    if not proc.exists():
        sys.exit('Runner is not visible here. Run this stop command in the SSH terminal on cheetara.')
    argv = proc.read_bytes().decode().rstrip('\0').split('\0')
    if str(script) not in argv or 'run' not in argv or '--output' not in argv:
        sys.exit('Recorded PID does not match this PPA runner; nothing was signalled.')
    if pathlib.Path(argv[argv.index('--output')+1]).resolve() != work.resolve():
        sys.exit('Recorded PID uses a different work directory; nothing was signalled.')
    os.kill(pid, signal.SIGTERM)
    print(f'Sent SIGTERM to PPA runner {pid}; waiting for tools and sweep to exit.', flush=True)
    for _ in range(60):
        time.sleep(.5)
        if inactive():
            print('PPA sweep stopped. Existing logs and caches were kept.')
            sys.exit(0)
    sys.exit('Stop requested, but the sweep lock is still held. Check status before restarting.')
PY
    exit $?
fi

[[ -f "$PPA" ]] || { printf 'Missing %s\n' "$PPA" >&2; exit 1; }
command -v "$PYTHON_BIN" >/dev/null || { printf 'Python not found: %s\n' "$PYTHON_BIN" >&2; exit 1; }
command -v flock >/dev/null || { printf 'The flock command is required.\n' >&2; exit 1; }
mkdir -p -- "$LOG_ROOT"

if [[ "$MODE" == start ]]; then
    # The worker takes the lock and performs preflight; no double-start can
    # launch two actual sweeps. A second worker exits without starting tools.
    nohup bash "$SELF" run "$@" >> "${LOG_ROOT}/runner.log" 2>&1 < /dev/null &
    printf 'Started worker PID %s. Progress: %s\n' "$!" "${LOG_ROOT}/runner.log"
    exit 0
fi

exec 9> "${LOG_ROOT}/sweep.lock"
flock -n 9 || { printf 'Another PPA sweep is already active.\n' >&2; exit 1; }
RUN_DIR="${LOG_ROOT}/run_$(date +%Y%m%d_%H%M%S_%N)"
mkdir -p -- "$RUN_DIR" "$WORK_ROOT"
printf '%s\n' "$RUN_DIR" > "${LOG_ROOT}/latest_run.txt"

COMMON=(
    --activations "$ACTIVATIONS" --max-r "$MAX_R" --max-k "$MAX_K" --max-m "$MAX_M"
    --period-ns "$PERIOD_NS" --uncertainty-ns "$UNCERTAINTY_NS"
    --utilization "$UTILIZATION" --activity "$ACTIVITY" --duty "$DUTY"
    --threads "$THREADS" --timeout "$TIMEOUT_SECONDS"
)
if [[ -n "$MACRO_BUNDLE" ]]; then COMMON+=(--macro-bundle "$MACRO_BUNDLE"); fi
EXTRA=("$@")

declare -a CASES=()
declare -A SEEN=()
add_case() {
    local group="$1" b="$2" rf="$3" ys="$4" ids="$5" key
    key="b${b}_rf${rf}_ys${ys}_id${ids}"
    if [[ -z "${SEEN[$key]:-}" ]]; then
        SEEN[$key]=1
        CASES+=("${key}:${group}:${b}:${rf}:${ys}:${ids}")
    fi
}
add_case baseline "$BASE_B" "$BASE_RF" "$BASE_YS" "$BASE_IDS"
BASE_LABEL="b${BASE_B}_rf${BASE_RF}_ys${BASE_YS}_id${BASE_IDS}"
for value in "${B_VALUES[@]}"; do add_case b "$value" "$BASE_RF" "$BASE_YS" "$BASE_IDS"; done
for value in "${RF_VALUES[@]}"; do add_case rf "$BASE_B" "$value" "$BASE_YS" "$BASE_IDS"; done
for value in "${IDS_VALUES[@]}"; do add_case ids "$BASE_B" "$BASE_RF" "$BASE_YS" "$value"; done
for value in "${YS_VALUES[@]}"; do add_case ys "$BASE_B" "$BASE_RF" "$value" "$BASE_IDS"; done

# Inline Python only formats reports/logs. Hardware flow remains run_ppa.py.
# No additional helper scripts are written or maintained.
update_summary() {
    "$PYTHON_BIN" -B - "$RUN_DIR" "$WORK_ROOT" "$BASE_LABEL" "$@" <<'PY'
import csv, json, pathlib, sys, time
run, work = map(pathlib.Path, sys.argv[1:3])
baseline, action = sys.argv[3:5]
table = run / 'summary.csv'
fields = ['configuration','group','b','rf','ys','ids','status','exit_code',
          'flow_mode','routing_performed','parasitics_basis',
          'logic_um2','registers_um2','sram_um2','placed_cell_and_macro_um2',
          'core_um2','sram_instances','logical_sram_bits','physical_sram_bits',
          'logic_cells','register_cells','physical_only_cells',
          'estimated_fmax_mhz','power_total_w','power_internal_w','power_switching_w',
          'power_leakage_w','power_basis','target_setup_met',
          'hold_met','routing_drc_clean','elapsed_seconds',
          'core_area_change_percent','fmax_change_percent','power_change_percent','report']
rows = list(csv.DictReader(table.open())) if table.exists() else []
if action == 'init':
    for case in sys.argv[5:]:
        label, group, b, rf, ys, ids = case.split(':')
        rows.append(dict(configuration=label, group=group, b=b, rf=rf, ys=ys,
                         ids=ids, status='NOT_RUN', flow_mode='placement_cts_quick',
                         routing_performed=False, parasitics_basis='estimate_parasitics -placement'))
        (run/(label+'.log')).write_text(
            f'Configuration: {label}\nGroup: {group}\nStatus: NOT_RUN\n')
elif action in ('blocked', 'skip'):
    for row in rows:
        if row['status'] == 'NOT_RUN':
            row['status'] = 'BLOCKED' if action == 'blocked' else 'SKIPPED'
            with (run/(row['configuration']+'.log')).open('a') as log:
                log.write('\n'+sys.argv[5]+'\n')
                if action == 'blocked':
                    log.write((run/'preflight.log').read_text())
else:
    label, code, since = sys.argv[5], int(sys.argv[6]), float(sys.argv[7])
    row = next(r for r in rows if r['configuration'] == label)
    row.update(exit_code=code, status='FAILED')
    report_dir = None
    latest = work/'latest.json'
    if latest.exists():
        candidate = pathlib.Path(json.loads(latest.read_text())['folder'])
        report_file = candidate/'report.json'
        if report_file.exists() and report_file.stat().st_mtime >= since:
            result = json.loads(report_file.read_text())
            config = result.get('configuration', {})
            if all(config.get(k) == int(row[k]) for k in ('b','rf','ys','ids')):
                report_dir = candidate
                row.update(status=result['status'], report=str(candidate/'report.md'),
                           elapsed_seconds=result.get('elapsed_seconds',''),
                           flow_mode=result.get('flow_mode',''),
                           routing_performed=result.get('routing_performed',''))
                for field in ('logic_um2','registers_um2','sram_um2','placed_cell_and_macro_um2',
                              'core_um2','sram_instances','logical_sram_bits','physical_sram_bits',
                              'logic_cells','register_cells','physical_only_cells'):
                    row[field] = (result.get('area') or {}).get(field, '')
                timing, power = result.get('timing') or {}, result.get('power') or {}
                for field in ('estimated_fmax_mhz','target_setup_met','hold_met','routing_drc_clean'):
                    row[field] = timing.get(field, '')
                row['parasitics_basis'] = timing.get('parasitics_basis', row.get('parasitics_basis',''))
                row['power_total_w'] = power.get('total_w', '')
                for component in ('internal','switching','leakage'):
                    row['power_'+component+'_w'] = power.get(component+'_w', '')
                row['power_basis'] = power.get('basis', '')
    with (run/(label+'.log')).open('a') as log:
        log.write(f'\nFINAL STATUS: {row["status"]}; process exit={code}\n')
        if report_dir:
            design_file = report_dir/'design.json'
            design = pathlib.Path(json.loads(design_file.read_text())['path']) if design_file.exists() else None
            files = list(report_dir.rglob('*'))
            if design:
                files += list(design.rglob('*'))
                log.write(f'\nAll netlists, databases, SPEF and intermediate artifacts: {design}\n')
                provenance = design/'provenance.json'
                if provenance.exists():
                    jar_hash = json.loads(provenance.read_text()).get('jar')
                    for marker in (work/'cache/assembly').glob('*/assembly.done.json'):
                        manifest = json.loads(marker.read_text())
                        if jar_hash in manifest.get('outputs', {}).values():
                            files.append(marker.parent/'assembly.log')
            else:
                # An assembly failure can occur before a design folder exists.
                files += [p for p in (work/'cache/assembly').glob('*/assembly.log')
                          if p.stat().st_mtime >= since]
            seen = set()
            for path in sorted(files):
                if path in seen or not path.is_file() or path.is_symlink(): continue
                if path.suffix not in ('.log','.rpt') and path.name not in (
                        'report.md','report.json','memory_requirements.json','placed_cells.tsv',
                        'timing.tsv','core_area.txt', 'synthesis_cells.json') and not path.name.endswith('.synth_stat.txt'):
                    continue
                seen.add(path)
                log.write(f'\n----- {path} -----\n')
                with path.open(errors='replace') as source:
                    for block in iter(lambda: source.read(1024*1024), ''): log.write(block)
    print(row['status'])
base = next(r for r in rows if r['configuration'] == baseline)
for row in rows:
    for column, metric in [('core_area_change_percent','core_um2'),
                           ('fmax_change_percent','estimated_fmax_mhz'),
                           ('power_change_percent','power_total_w')]:
        try:
            denominator = float(base[metric])
            row[column] = round(100*(float(row[metric])/denominator-1), 3) if denominator else ''
        except (ValueError, TypeError, KeyError): row[column] = ''
tmp = table.with_suffix('.csv.tmp')
with tmp.open('w', newline='') as output:
    writer = csv.DictWriter(output, fields)
    writer.writeheader(); writer.writerows(rows)
tmp.replace(table)
PY
}

update_summary init "${CASES[@]}"
printf 'PPA sweep: %s unique designs. Output: %s\n' "${#CASES[@]}" "$RUN_DIR"
printf 'Checking tools and SRAM IP before any build...\n'
if "$PYTHON_BIN" -u -B "$PPA" check "${EXTRA[@]}" "${COMMON[@]}" \
    --b "$BASE_B" --rf "$BASE_RF" --ys "$BASE_YS" --ids "$BASE_IDS" \
    > "${RUN_DIR}/preflight.log" 2>&1; then
    printf 'Preflight passed. Running the baseline first.\n'
else
    update_summary blocked 'No hardware runs: prerequisites failed; see preflight.log.'
    printf 'BLOCKED: %s\n' "${RUN_DIR}/preflight.log" >&2
    exit 1
fi

EXIT_STATUS=0
for entry in "${CASES[@]}"; do
    IFS=: read -r label group b rf ys ids <<< "$entry"
    logfile="${RUN_DIR}/${label}.log"
    started="$(date +%s)"
    printf 'Running %s (%s)\n' "$label" "$group"
    {
        printf 'Configuration: %s\nGroup: %s\nStart: %s\n' "$label" "$group" "$(date -Is)"
        printf 'FP16/INT16: %s; capacities R=%s K=%s M=%s; target=%sns; utilization=%s\n' \
            "$ACTIVATIONS" "$MAX_R" "$MAX_K" "$MAX_M" "$PERIOD_NS" "$UTILIZATION"
        printf 'Power: statistical activity=%s duty=%s unless VCD explicitly supplied\n' "$ACTIVITY" "$DUTY"
        printf 'Flow: placement + CTS, final PPA analysis only; estimated wire RC. Post-CTS timing repair, intermediate timing reports, routing and OpenRCX disabled.\n'
        printf 'Command: '
        printf '%q ' "$PYTHON_BIN" -u -B "$PPA" run "${EXTRA[@]}" "${COMMON[@]}" \
            --output "$WORK_ROOT" --b "$b" --rf "$rf" --ys "$ys" --ids "$ids"
        printf '\n'
    } > "$logfile"
    code=0
    "$PYTHON_BIN" -u -B "$PPA" run "${EXTRA[@]}" "${COMMON[@]}" \
        --output "$WORK_ROOT" --b "$b" --rf "$rf" --ys "$ys" --ids "$ids" \
        >> "$logfile" 2>&1 || code=$?
    status="$(update_summary result "$label" "$code" "$started")"
    printf '%s: %s; log: %s\n' "$label" "$status" "$logfile"
    if [[ "$status" != COMPLETE ]]; then EXIT_STATUS=1; fi
    if [[ "$group" == baseline ]] && \
       { ((code != 0)) || { ((STOP_IF_BASELINE_NEEDS_REVIEW)) && [[ "$status" != COMPLETE ]]; }; }; then
        update_summary skip "Baseline status is ${status}; sweep stopped before other designs."
        printf 'Baseline needs attention; other designs were skipped. See %s\n' "$logfile" >&2
        exit 1
    fi
done
printf 'Finished. Results: %s\n' "${RUN_DIR}/summary.csv"
exit "$EXIT_STATUS"
