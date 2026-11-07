#!/usr/bin/env python3
"""One untiled 1x1024 @ 1024x1024 operation at each of W8/W4/W2.

Uses the existing verified RISC-V/Verilator harness in run_llama_shapes.py.
Commands: start (detached), status, stop, check. Default design: b8_rf8.
"""
import argparse
import collections
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shlex
import shutil
import signal
import struct
import subprocess
import sys

sys.dont_write_bytecode = True
import run_llama_shapes as hw

SELF = Path(__file__).resolve()
LOGDIR = SELF.parent / 'logs' / 'matrix_1024'
STATE = Path('/tmp/lumax-1024-' + str(os.getuid()))


def preflight(args):
    args.t = 1024
    hw.preflight(args)
    for name in ('Cin', 'Cout'):
        match = re.search(r'(?m)^\s*' + name + r'\s*=\s*(\d+)', hw.CONFIG.read_text())
        if not match or int(match[1]) < 1024:
            raise RuntimeError(name + ' must be at least 1024; rebuild required')


def worker(args):
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    hw.LOG = Path(args.log)
    hw.STATE = STATE
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: hw.STOP.set())
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    rows = []
    original = selected = None
    hashes = {}
    locks = []
    unchanged = False
    try:
        for path in (STATE / 'lock', hw.CONFIG):
            lock = path.open('a+' if path.name == 'lock' else 'rb')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locks.append(lock)
        preflight(args)
        root = STATE / 'work'
        root.mkdir()
        # sbt's normal /run/user location may be read-only in automation.
        # Batch compilation does not need the interactive sbt server.
        runtime = STATE / 'runtime'
        runtime.mkdir(mode=0o700)
        os.environ['XDG_RUNTIME_DIR'] = str(runtime)
        sbt_options = os.environ.get('SBT_OPTS') or (
            f'-Dsbt.ivy.home={hw.CY}/.ivy2 -Dsbt.global.base={hw.CY}/.sbt '
            f'-Dsbt.boot.directory={hw.CY}/.sbt/boot/')
        os.environ['SBT_OPTS'] = sbt_options + ' -Dsbt.server.forcestart=true -Dsbt.server.autostart=false -Dsbt.supershell=false'
        hw.BASE = (hw.SRC / 'Linear-sw.c').read_text()
        original = hw.CONFIG.read_bytes()
        (STATE / 'original-config.scala').write_bytes(original)
        paths = list((hw.GEN / 'src/main/scala').glob('*.scala'))
        paths += [hw.SRC / n for n in ('Linear-sw.c', 'rocc.h', 'compiler.h', 'build.sh')]
        hashes = {str(p): hw.sha(p) for p in paths}
        hw.log('LUMAX 1024 VERIFICATION\nSTART: ' + hw.now())
        hw.log(f'DESIGN={args.design}; X=1x1024; W=1024x1024; Y=1x1024; input16; W8/W4/W2; output32')
        hw.log('Three independent native HW operations, one per precision. No output tiling, padding, or software partial sums.')
        hw.log('Python references remain on the host; only random operands go into the RISC-V ELF. Actual DMA outputs are checked element by element.')
        hw.log('Only results, seeds, randomness statistics, hashes and cycles are retained; no matrix dumps.')
        hw.log(f'TIMEOUTS: make={args.build_timeout}s; ELF compile={args.compile_timeout}s; simulator={args.timeout}s per precision; max-cycles={args.max_cycles}')
        hw.log('ACTIVE HW cycles = Load X + Generate + combined Load W/Select + Store O. CORE_COMPUTE is the rdcycle interval around matmul_hw_uniform. Printing and Python work are excluded from these counters.')
        hw.log('SOURCE SHA256: ' + json.dumps(hashes, sort_keys=True))
        hw.log('RUNNER SHA256: ' + hw.sha(SELF) + '; HARNESS SHA256: ' + hw.sha(Path(hw.__file__)))
        b, rf = map(int, re.fullmatch(r'b(\d+)_rf(\d+)', args.design).groups())
        config = original.decode()
        for key, value in (('x_slice', b), ('Mem_row_factor', rf)):
            config, count = re.subn(r'(?m)^(\s*' + key + r'\s*=\s*)\d+',
                                    lambda m: m[1] + str(value), config)
            if count != 1:
                raise RuntimeError('Cannot select geometry: ' + key)
        selected = config.encode()
        builddir = root / args.design
        builddir.mkdir()
        sim = builddir / 'simulator-LUMAXROcketConfig'
        cmd = ['make', '-j' + str(args.jobs), 'CONFIG=LUMAXROcketConfig',
               'sim=' + str(sim), 'gen_dir=' + str(builddir / 'generated-src')]
        hw.log('MAKE ONCE CWD=' + str(hw.CY / 'sims/verilator') + '\nCOMMAND: ' + shlex.join(cmd))
        hw.log('sbt batch mode; private writable runtime=' + str(runtime))
        try:
            hw.CONFIG.write_bytes(selected)
            code = hw.command(cmd, hw.CY / 'sims/verilator', builddir / 'make.log', args.build_timeout)
        finally:
            if hw.CONFIG.read_bytes() == selected:
                hw.CONFIG.write_bytes(original)
            elif hw.CONFIG.read_bytes() != original:
                raise RuntimeError('Config.scala changed externally; recovery snapshot retained')
        hw.log('MAKE OUTPUT:\n' + (builddir / 'make.log').read_text(errors='replace') + f'\nMAKE EXIT={code}')
        if code or not sim.is_file():
            raise RuntimeError('1024-capacity make build failed')
        hw.log('SIMULATOR_SHA256=' + hw.sha(sim))
        shutil.rmtree(builddir / 'generated-src')
        for wb in (8, 4, 2):
            if hw.stopped():
                raise InterruptedError('Stop requested')
            if any(hw.sha(p) != v for p, v in hashes.items()):
                raise RuntimeError('Hardware/software sources changed during the run')
            xs, ws = hw.seed(), hw.seed()
            x = hw.input_values(1024, xs)
            if len(set(x)) < 2:
                raise RuntimeError('Degenerate random input')
            packed, expected, weight_hash, distribution = hw.weight_tile(x, 1024, wb, hw.RandomValues(ws))
            hw.log(f'\nCASE W{wb}: INPUT_SEED={xs}; WEIGHT_SEED={ws}; RNG=SplitMix64')
            hw.log(f'X_NONZERO={sum(v != 0 for v in x)}/1024; X_DISTINCT={len(set(x))}; X_MIN={min(x)}; X_MAX={max(x)}')
            hw.log(f'W_NONZERO={1024 * 1024 - distribution.get(0, 0)}/{1024 * 1024}; W_DISTINCT={len(distribution)}; W_DISTRIBUTION=' + json.dumps(distribution, sort_keys=True))
            hw.log('X_SHA256=' + hashlib.sha256(struct.pack('<1024h', *x)).hexdigest() + '; W_SHA256=' + weight_hash + '; PACKED_SHA256=' + hashlib.sha256(packed).hexdigest())
            hw.log(f'PYTHON_REFERENCE_INT32_CHECK=PASS; MIN={min(expected)}; MAX={max(expected)}; PYTHON_OUTPUT_SHA256=' + hashlib.sha256(struct.pack('<1024i', *expected)).hexdigest())
            # One ELF contains exactly one full preload/compute call at k=m=1024.
            # run_tile is the shared harness name; it receives the full matrix.
            row = hw.run_tile(args, sim, root / f'W{wb}', 'MAT1024', 1024, 1024, wb, 0, x, packed, expected)
            row['wb'] = wb
            rows.append(row)
            hw.log('CASE RESULT: ' + json.dumps(row, sort_keys=True))
            if row['status'] == 'STOPPED':
                raise InterruptedError('Stop requested')
    except BaseException as error:
        hw.log(f'RUN INTERRUPTED: {type(error).__name__}: {error}')
        buildlog = STATE / 'work' / args.design / 'make.log'
        if len(rows) == 0 and buildlog.is_file():
            hw.log('LAST MAKE DIAGNOSTICS:\n' + '\n'.join(buildlog.read_text(errors='replace').splitlines()[-30:]))
    finally:
        if original is not None and selected is not None and hw.CONFIG.read_bytes() == selected:
            hw.CONFIG.write_bytes(original)
        unchanged = bool(hashes) and all(Path(p).is_file() and hw.sha(p) == v for p, v in hashes.items())
        hw.log('\nTOTAL RESULTS\nW bits | Exact matches | Status | Active HW cycles | Core compute cycles | Wall seconds')
        for row in rows:
            hw.log(f'{row["wb"]} | {row["matches"]}/1024 | {row["status"]} | {row["cycles"]} | {row["core_compute"]} | {row["seconds"]}')
        hw.log(f'TOTAL: {dict(collections.Counter(r["status"] for r in rows))}; {len(rows)}/3 operations completed.')
        hw.log('Capacity-1024 configuration and original HW/SW sources unchanged after the build: ' + str(unchanged))
        hw.log('RUN COMPLETE' if len(rows) == 3 and unchanged else 'RUN INCOMPLETE')
        hw.log('END: ' + hw.now())
        if original is None or hw.CONFIG.read_bytes() == original:
            hw.log('Temporary RTL/builds, vectors, ELFs and raw UART removed. This script and one results log are retained.')
            shutil.rmtree(STATE)
        else:
            hw.log('Recovery snapshot retained in ' + str(STATE))
        for lock in reversed(locks):
            lock.close()
    return 0 if len(rows) == 3 and unchanged and all(r['status'] == 'PASS' for r in rows) else 1


def status():
    path = None
    if (STATE / 'run.json').is_file():
        info = json.loads((STATE / 'run.json').read_text())
        path = Path(info['log'])
        print('PID:', info['pid'], '\nLog:', path)
        if (STATE / 'lock').exists():
            with (STATE / 'lock').open('a+') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    print('Worker lock free: starting or exited; inspect log.')
                except BlockingIOError:
                    print('Worker active (filesystem lock held).')
    else:
        print('No active runner state.')
        logs = sorted(LOGDIR.glob('run_*.log'))
        if logs:
            path = logs[-1]
    if path and path.exists():
        text = path.read_text(errors='replace')
        rows = [json.loads(row) for row in re.findall(r'^CASE RESULT: (.*)$', text, re.M)]
        print('Completed:', len(rows), '/3', dict(collections.Counter(r['status'] for r in rows)))
        activity = [s for s in text.splitlines() if re.match(r'^(MAKE ONCE|MAKE EXIT|CASE W|RISC-V BUILD EXIT|ACTUAL VERILATOR COMMAND|CASE RESULT|TOTAL:|RUN COMPLETE|RUN INCOMPLETE|RUN INTERRUPTED|END:)', s)]
        print('\n'.join(activity[-5:]))
        print('Log:', path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'status', 'stop', 'check', 'worker'))
    parser.add_argument('--design', choices=hw.DESIGNS, default='b8_rf8')
    parser.add_argument('--timeout', type=int, default=21600, help='wall seconds per precision, default six hours')
    parser.add_argument('--build-timeout', type=int, default=7200)
    parser.add_argument('--compile-timeout', type=int, default=1200)
    parser.add_argument('--max-cycles', type=int, default=100000000000)
    parser.add_argument('--jobs', type=int, default=8)
    parser.add_argument('--log', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if min(args.timeout, args.build_timeout, args.compile_timeout, args.max_cycles, args.jobs) <= 0:
        parser.error('Timeouts, cycles and jobs must be positive')
    if args.action == 'status':
        status(); return 0
    if args.action == 'stop':
        if STATE.exists():
            (STATE / 'stop').touch()
            print('Safe stop requested.')
        else:
            print('No active runner state.')
        return 0
    preflight(args)
    if args.action == 'worker':
        if not args.log or not STATE.exists():
            parser.error('worker is internal; use start')
        return worker(args)
    env = hw.environment()
    if args.action == 'check':
        print(f'PRECHECK OK: {args.design}; capacity1024; input16; W8/W4/W2; three full 1x1024 @ 1024x1024 operations; no tiling.')
        print('One make build, reused for all three ELFs. No build or simulation started.')
        return 0
    if STATE.exists():
        raise RuntimeError('Runner state exists; use status/stop: ' + str(STATE))
    LOGDIR.mkdir(parents=True, exist_ok=True)
    STATE.mkdir(mode=0o700)
    path = LOGDIR / ('run_' + datetime.now().astimezone().strftime('%Y%m%d_%H%M%S_%f') + '.log')
    path.touch(exist_ok=False)
    (STATE / 'run.json').write_text(json.dumps(dict(pid='starting', log=str(path))))
    cmd = [sys.executable, str(SELF), 'worker', '--design', args.design,
           '--timeout', str(args.timeout), '--build-timeout', str(args.build_timeout),
           '--compile-timeout', str(args.compile_timeout), '--max-cycles', str(args.max_cycles),
           '--jobs', str(args.jobs), '--log', str(path)]
    try:
        with (STATE / 'console.log').open('w') as output:
            proc = subprocess.Popen(cmd, cwd=hw.CY, env=env, stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    except BaseException:
        shutil.rmtree(STATE)
        path.unlink()
        raise
    try:
        (STATE / 'run.json').write_text(json.dumps(dict(pid=proc.pid, log=str(path))))
    except FileNotFoundError:
        pass
    print('Started detached in background. PID:', proc.pid)
    print('You may close SSH. Log:', path)
    print('Status: python3 ' + str(SELF) + ' status')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f'ERROR: {type(error).__name__}: {error}', file=sys.stderr)
        raise SystemExit(1)
