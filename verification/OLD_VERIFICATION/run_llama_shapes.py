#!/usr/bin/env python3
"""Detached LUMAX LQ01-LQ06 shape verification; start | status | stop | check.

One selected design is built once. All inputs are int16; weights are int8,
int4 or int2. Python supplies random operands and independently checks actual
Verilator/RISC-V/RoCC outputs. No reference outputs are placed in the ELF.
Only this script and one dated results log remain after a completed run.
"""
import argparse
import ast
import collections
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import secrets
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time

sys.dont_write_bytecode = True
CY = Path('/home/kioannou/chipyard_1.13.0')
GEN = CY / 'generators/LUMAX'
SRC = GEN / 'software/tests/src'
CONFIG = GEN / 'src/main/scala/Config.scala'
SELF = Path(__file__).resolve()
LOGDIR = SELF.parent / 'logs' / 'llama_shapes'
STATE = Path('/tmp/lumax-llama-shapes-' + str(os.getuid()))
DESIGNS = [f'b{b}_rf{rf}' for b in (2, 4, 8) for rf in (4, 8)]
STOP = threading.Event()
USED_SEEDS = set()
LOG = None
BASE = ''


def now():
    return datetime.now().astimezone().isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def log(message):
    with LOG.open('a') as f:
        f.write(str(message) + '\n')
    print(str(message), flush=True)


def stopped():
    return STOP.is_set() or (STATE / 'stop').exists()


def plans(t):
    # Classifier first; each shape runs at every supported weight precision.
    return [(name, k, m, wb) for name, k, m in
            [('LQ04', 288, 32000), ('LQ01', 288, 288),
             ('LQ02', 288, 768), ('LQ03', 768, 288),
             ('LQ05', 48, t), ('LQ06', t, 48)] for wb in (8, 4, 2)]


def tiles(name, m):
    size = 512 if name == 'LQ04' else m
    return [(start, min(size, m - start)) for start in range(0, m, size)]


def seed():
    while True:
        value = secrets.randbits(64)
        if value and value not in USED_SEEDS:
            USED_SEEDS.add(value)
            return value


class RandomValues:
    """Reproducible SplitMix64 stream initialized with OS entropy."""
    def __init__(self, initial):
        self.state = initial

    def value(self, bits):
        mask = (1 << 64) - 1
        self.state = (self.state + 0x9e3779b97f4a7c15) & mask
        z = self.state
        z = ((z ^ (z >> 30)) * 0xbf58476d1ce4e5b9) & mask
        z = ((z ^ (z >> 27)) * 0x94d049bb133111eb) & mask
        z ^= z >> 31
        return (z >> (64 - bits)) - (1 << (bits - 1))


def input_values(k, initial):
    rng = RandomValues(initial)
    return [rng.value(16) for _ in range(k)]


def weight_tile(x, m, wb, rng):
    """Generate columns in order, pack signed weights, calculate Python dots."""
    k = len(x)
    packed = bytearray((k * m * wb + 7) // 8)
    expected = []
    digest = hashlib.sha256()
    counts = collections.Counter()
    for c in range(m):
        column = [rng.value(wb) for _ in range(k)]
        digest.update(bytes(v & 255 for v in column))
        counts.update(column)
        result = sum(a * b for a, b in zip(x, column))
        if not -(1 << 31) <= result < (1 << 31):
            raise ValueError('Python reference exceeds int32; test is not valid')
        expected.append(result)
        for j, v in enumerate(column):
            bit = (c * k + j) * wb
            packed[bit // 8] |= (v & ((1 << wb) - 1)) << (bit % 8)
        # Independently decode exactly the bytes supplied to HW.
        for j, v in enumerate(column):
            bit = (c * k + j) * wb
            decoded = (packed[bit // 8] >> (bit % 8)) & ((1 << wb) - 1)
            if decoded & (1 << (wb - 1)):
                decoded -= 1 << wb
            if decoded != v:
                raise ValueError('Packed weights do not match logical weights')
    if counts[0] == k * m or len(counts) < 2 or not any(x):
        raise ValueError('Degenerate random data; refusing this test')
    return packed, expected, digest.hexdigest(), dict(sorted(counts.items()))


def source(k, m, wb, b, rf, x, packed):
    c = BASE.split('int main() {', 1)[0]
    for key, value in {'IN_BITS': 16, 'W_BITS': wb, 'Debug': 0,
                       'XS': b, 'YS': 1, 'Mem_row_factor': rf}.items():
        c, n = re.subn(r'(^#define\s+' + key + r'\s+)\d+',
                       lambda mt: mt[1] + str(value), c, flags=re.M)
        if n != 1:
            raise ValueError('Cannot set harness macro ' + key)
    # Original functions remain intact. Only the test main/operands are added.
    c += '\n#undef printf\n'
    c += f'static const input_t test_x[{k}] = {{' + ','.join(map(str, x)) + '};\n'
    c += f'static const uint8_t test_w[{len(packed)}] = {{' + ','.join(map(str, packed)) + '};\n'
    c += f'''
static uint8_t arena_x[{k * 2 + 24}] __attribute__((aligned(8)));
static uint8_t arena_w[{len(packed) + 24}] __attribute__((aligned(8)));
static uint8_t arena_o[{m * 4 + 24}] __attribute__((aligned(8)));
static int guards(const uint8_t *p, int size, int used) {{
  for (int i=0; i<8; ++i) if (p[i]!=0xa5) return 0;
  for (int i=8+used; i<size; ++i) if (p[i]!=0xa5) return 0;
  return 1;
}}
int main(void) {{
  memset(arena_x,0xa5,sizeof(arena_x));
  memset(arena_w,0xa5,sizeof(arena_w));
  memset(arena_o,0xa5,sizeof(arena_o));
  memcpy(arena_x+8,test_x,sizeof(test_x));
  memcpy(arena_w+8,test_w,sizeof(test_w));
  memset(arena_o+8,0x5a,{m}*sizeof(output_t));
  QuantizedTensor_input tx={{.q=(input_t*)(arena_x+8)}};
  QuantizedTensor_weight tw={{.q=(weight_t*)(arena_w+8)}};
  output_t *hw=(output_t*)(arena_o+8);
  unsigned long begin=(unsigned long)rdcycle();
  preload_hw({k},1,{m},x_slice_max*RF,y_slice,tx.q,tw.q,hw);
  unsigned long loaded=(unsigned long)rdcycle();
  matmul_hw_uniform(hw,&tx,&tw,{k},{m},x_slice_max*RF,y_slice,1);
  unsigned long done=(unsigned long)rdcycle();
  unsigned long pc[6];
  for (int i=0; i<6; ++i) pc[i]=read_rd();
  /* Fresh Verilator boot for this operation: selector and all PCs start at 0.
     Select/loadW counters overlap the combined phase, so do not double count. */
  unsigned long active=pc[0]+pc[1]+pc[4]+pc[5];
  int safe=guards(arena_x,sizeof(arena_x),sizeof(test_x)) &&
           guards(arena_w,sizeof(arena_w),sizeof(test_w)) &&
           guards(arena_o,sizeof(arena_o),{m}*sizeof(output_t)) &&
           !memcmp(arena_x+8,test_x,sizeof(test_x)) &&
           !memcmp(arena_w+8,test_w,sizeof(test_w));
  printf("CYCLES ACTIVE %lu CORE_PRELOAD %lu CORE_COMPUTE %lu PC %lu %lu %lu %lu %lu %lu\\n",
         active,loaded-begin,done-loaded,pc[0],pc[1],pc[2],pc[3],pc[4],pc[5]);
  printf("GUARDS %s\\n",safe?"PASS":"FAIL");
  /* Emit actual DMA output for the HOST to compare; no reference in this ELF. */
  char report[{m * 16 + 1}];
  int used=0;
  for (int i=0; i<{m}; ++i) {{
    int n=snprintf(report+used,sizeof(report)-used,"%d ",hw[i]);
    if (n<0 || (unsigned)n>=sizeof(report)-used) return 2;
    used+=n;
  }}
  printf("HW_OUTPUT %s\\n",report);
  printf("TEST_DONE\\n");
  return safe?0:1;
}}
'''
    return c


def command(args, cwd, output, timeout):
    if stopped():
        raise InterruptedError('Stop requested')
    with output.open('w') as f:
        proc = subprocess.Popen(args, cwd=cwd, stdout=f,
                                stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + timeout
        try:
            while True:
                if stopped():
                    raise InterruptedError('Stop requested')
                try:
                    return proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f'Exceeded {timeout}s wall timeout')
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()


def compare_uart(uart, expected, exit_code):
    result = dict(status='FAIL', matches=0, cycles=None, core_preload=None,
                  core_compute=None, guards='MISSING', pc=None)
    output = re.findall(r'^HW_OUTPUT (.*)$', uart, re.M)
    cycles = re.findall(r'^CYCLES ACTIVE (\d+) CORE_PRELOAD (\d+) CORE_COMPUTE (\d+) PC ((?:\d+ ){5}\d+)$', uart, re.M)
    guards = re.findall(r'^GUARDS (PASS|FAIL)$', uart, re.M)
    if len(output) == 1:
        actual = list(map(int, output[0].split()))
        result['matches'] = sum(a == b for a, b in zip(actual, expected))
        result['actual_elements'] = len(actual)
        result['hw_sha256'] = hashlib.sha256(b''.join(struct.pack('<i', v) for v in actual)).hexdigest()
    else:
        actual = None
    if len(cycles) == 1:
        active, preload, compute, counters = cycles[0]
        result.update(cycles=int(active), core_preload=int(preload),
                      core_compute=int(compute), pc=list(map(int, counters.split())))
    if len(guards) == 1:
        result['guards'] = guards[0]
    valid_cycles = (result['pc'] is not None and result['cycles'] > 0 and
                    result['core_compute'] > 0 and
                    result['cycles'] == sum(result['pc'][i] for i in (0, 1, 4, 5)))
    if (exit_code == 0 and actual == expected and guards == ['PASS'] and
            uart.splitlines().count('TEST_DONE') == 1 and valid_cycles):
        result['status'] = 'PASS'
    return result


def run_tile(args, sim, folder, name, k, m, wb, start, x, packed, expected):
    folder.mkdir()
    b, rf = map(int, re.fullmatch(r'b(\d+)_rf(\d+)', args.design).groups())
    record = dict(status='ERROR', matches=0, elements=m, cycles=None,
                  core_preload=None, core_compute=None, pc=None, guards='MISSING')
    started = time.monotonic()
    try:
        cfile = folder / 'Linear-sw.c'
        cfile.write_text(source(k, m, wb, b, rf, x, packed))
        for filename in ('rocc.h', 'compiler.h', 'build.sh'):
            shutil.copy2(SRC / filename, folder / filename)
        log('TEST_C_SHA256=' + sha(cfile) + '; no Python reference array in test ELF')
        code = command(['bash', './build.sh', 'baremetal'], folder,
                       folder / 'build.log', args.compile_timeout)
        log(f'RISC-V BUILD EXIT={code}')
        if code:
            record['status'] = 'BUILD_FAIL'
            log((folder / 'build.log').read_text(errors='replace'))
            return record
        elf = folder / 'Linear-sw.riscv'
        log('ELF_SHA256=' + sha(elf))
        cmd = [str(sim), '+permissive', f'+max-cycles={args.max_cycles}',
               '+loadmem=' + str(elf), '+permissive-off', str(elf)]
        log('ACTUAL VERILATOR COMMAND: ' + shlex.join(cmd))
        timed_out = False
        try:
            code = command(cmd, folder, folder / 'uart.log', args.timeout)
        except TimeoutError:
            timed_out = True
            code = None
        uart = (folder / 'uart.log').read_text(errors='replace').replace('\r\n', '\n')
        observed = compare_uart(uart, expected, code)
        record.update(observed)
        if timed_out:
            record['status'] = 'TIMEOUT'
        elif '*** FAILED ***' in uart:
            record['status'] = 'TIMEOUT' if 'timeout' in uart.lower() else 'FAIL'
        log(f'VERILATOR EXIT={code}; wall timeout={timed_out}')
        # Matrices and actual output values only exist in temporary files/memory.
        if record['status'] != 'PASS':
            diagnostics = [line[:500] for line in uart.splitlines()
                           if not line.startswith('HW_OUTPUT ')]
            log('VERILATOR DIAGNOSTICS (no matrix values):\n' + '\n'.join(diagnostics[-12:]))
    except InterruptedError:
        record['status'] = 'STOPPED'
    except TimeoutError as error:
        record['status'] = 'BUILD_TIMEOUT'
        log(str(error))
    except Exception as error:
        log(f'TILE ERROR: {type(error).__name__}: {error}')
    finally:
        record['seconds'] = round(time.monotonic() - started, 3)
        log(f'TILE RESULT {name} W{wb} columns={start}:{start + m}: ' + json.dumps(record, sort_keys=True))
        shutil.rmtree(folder)
    return record


def preflight(args):
    for path in (CY / 'env.sh', CONFIG, SRC / 'Linear-sw.c', SRC / 'rocc.h',
                 SRC / 'compiler.h', SRC / 'build.sh'):
        if not path.is_file():
            raise RuntimeError('Missing ' + str(path))
    cfg = CONFIG.read_text()
    for key, value in [('y_slice', 1), ('XBitWidth', 16), ('WBitWidth', 8), ('OutBitWidth', 32)]:
        if not re.search(r'(?m)^\s*' + key + r'\s*=\s*' + str(value) + r'\s*,', cfg):
            raise RuntimeError('Requires ' + key + '=' + str(value))
    if not re.search(r'(?m)^\s*DEBUG\s*=\s*true\s*,', cfg):
        raise RuntimeError('Requires existing HW performance counters (DEBUG=true)')
    for key, minimum in [('Cin', max(768, args.t)), ('Cout', max(768, args.t))]:
        found = re.search(r'(?m)^\s*' + key + r'\s*=\s*(\d+)', cfg)
        if not found or int(found[1]) < minimum:
            raise RuntimeError('Current ' + key + ' limit is too small')
    if not re.search(r'(?m)^#define\s+SCALE\s+0\b', (SRC / 'Linear-sw.c').read_text()):
        raise RuntimeError('Requires integer output SCALE=0')
    if 'packedReadCount' not in (GEN / 'src/main/scala/LUMAX_Top.scala').read_text():
        raise RuntimeError('Fixed DMA loader was not found')
    config_path = CY / 'generators/chipyard/src/main/scala/config/LUMAXConfigs.scala'
    if 'WithLUMAXAccelerator' not in config_path.read_text():
        raise RuntimeError('LUMAXROcketConfig must instantiate WithLUMAXAccelerator')


def environment():
    # Capture the activated environment internally; never print its contents.
    shell = ('source /home/kioannou/miniforge3/etc/profile.d/conda.sh >/dev/null && '
             'cd ' + shlex.quote(str(CY)) + ' && source ./env.sh >/dev/null && '
             'command -v riscv64-unknown-elf-gcc >/dev/null && '
             'command -v make >/dev/null && command -v verilator >/dev/null && '
             'python3 -c ' + shlex.quote('import os,json; print(json.dumps(dict(os.environ)))'))
    result = subprocess.run(['bash', '-c', shell], capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError('Chipyard environment/toolchain setup failed: ' + result.stderr[-1000:])
    env = json.loads(result.stdout)
    env['TEST_CFLAGS'] = '-O2 -DLUMAX_QUIET_TEST=1'
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


def worker(args):
    global LOG, BASE
    LOG = Path(args.log)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: STOP.set())
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    locks = []
    root = STATE / 'work'
    records = []
    original = None
    selected = None
    hashes = {}
    complete = False
    unchanged = False
    try:
        lock = (STATE / 'lock').open('a+')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locks.append(lock)
        # All three suites hold this same lock while building/running.
        for path in (CONFIG,):
            if path.is_file():
                lock = path.open('rb')
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locks.append(lock)
        preflight(args)
        root.mkdir()
        (STATE / 'pid').write_text(str(os.getpid()))
        BASE = (SRC / 'Linear-sw.c').read_text()
        original = CONFIG.read_bytes()
        (STATE / 'original-config.scala').write_bytes(original)
        paths = list((GEN / 'src/main/scala').glob('*.scala'))
        paths += [SRC / name for name in ('Linear-sw.c', 'rocc.h', 'compiler.h', 'build.sh')]
        paths += [CY / 'generators/chipyard/src/main/scala/config/LUMAXConfigs.scala']
        hashes = {str(path): sha(path) for path in paths}
        log(f'LUMAX LQ SHAPE VERIFICATION\nSTART: {now()}\nDESIGN: {args.design}; T={args.t}; input16, W8/W4/W2; output32')
        log('PLANNED: 18 logical tests, 204 independent HW matmuls/Verilator boots; LQ04 first.')
        log('Classifier: 62 tiles of 512 outputs + one tile of 256; no dimension padding.')
        log('Python generates random operands and references. ELF has operands only; host checks actual DMA output.')
        log('No matrix input/weight/output values are retained in this log; seeds, distributions, hashes, matches and cycles are retained.')
        log(f'TIMEOUTS: make={args.build_timeout}s; ELF compile={args.compile_timeout}s; each native HW tile={args.timeout}s; max-cycles={args.max_cycles}')
        log('CYCLE DEFINITIONS: ACTIVE=PC_loadX+PC_Generate+PC_storeO+PC_loadW_and_Select. PC_loadW/PC_Select overlap the combined phase. CORE_COMPUTE=rdcycle interval around matmul_hw_uniform; CORE_PRELOAD=preload interval. Printing and Python comparison excluded.')
        log('Each native operation boots Verilator afresh: performance counters and read selector start at zero.')
        log('SOURCE SHA256: ' + json.dumps(hashes, sort_keys=True))
        log('RUNNER SHA256: ' + sha(SELF))
        b, rf = map(int, re.fullmatch(r'b(\d+)_rf(\d+)', args.design).groups())
        cfg = original.decode()
        for key, value in [('x_slice', b), ('Mem_row_factor', rf)]:
            cfg, n = re.subn(r'(?m)^(\s*' + key + r'\s*=\s*)\d+',
                             lambda mt: mt[1] + str(value), cfg)
            if n != 1:
                raise RuntimeError('Cannot select ' + key)
        selected = cfg.encode()
        builddir = root / args.design
        builddir.mkdir()
        sim = builddir / 'simulator-LUMAXROcketConfig'
        cmd = ['make', '-j' + str(args.jobs), 'CONFIG=LUMAXROcketConfig',
               'sim=' + str(sim), 'gen_dir=' + str(builddir / 'generated-src')]
        log('MAKE ONCE, CWD=' + str(CY / 'sims/verilator') + '\nCOMMAND: ' + shlex.join(cmd))
        try:
            CONFIG.write_bytes(selected)
            code = command(cmd, CY / 'sims/verilator', builddir / 'make.log', args.build_timeout)
        finally:
            if CONFIG.read_bytes() == selected:
                CONFIG.write_bytes(original)
            elif CONFIG.read_bytes() != original:
                raise RuntimeError('Config.scala changed externally; snapshot preserved in ' + str(STATE))
        log('MAKE OUTPUT:\n' + (builddir / 'make.log').read_text(errors='replace') + f'\nMAKE EXIT={code}')
        if code or not sim.is_file():
            raise RuntimeError('Selected design make build failed')
        log('SIMULATOR_SHA256=' + sha(sim))
        shutil.rmtree(builddir / 'generated-src')
        for name, k, m, wb in plans(args.t):
            if stopped():
                raise InterruptedError('Stop requested')
            xs, ws = seed(), seed()
            x = input_values(k, xs)
            rng = RandomValues(ws)
            histogram = collections.Counter(x)
            if len(histogram) < 2 or not any(x):
                raise RuntimeError('Degenerate input; refusing test')
            log(f'\nCASE {name} X=1x{k} W={k}x{m} input16 W{wb}; INPUT_SEED={xs}; WEIGHT_SEED={ws}')
            log('RNG=SplitMix64; X uses one stream; W uses one continuous column-major stream across all tiles.')
            log(f'X_NONZERO={k - histogram[0]}/{k}; X_MIN={min(x)}; X_MAX={max(x)}; X_DISTINCT={len(histogram)}; X_SHA256=' + hashlib.sha256(struct.pack('<' + 'h' * k, *x)).hexdigest())
            rows = []
            for index, (start, count) in enumerate(tiles(name, m), 1):
                if stopped():
                    raise InterruptedError('Stop requested')
                if any(sha(path) != value for path, value in hashes.items()):
                    raise RuntimeError('HW/SW sources changed during run; refusing further tests')
                packed, expected, weight_hash, counts = weight_tile(x, count, wb, rng)
                log(f'TILE {name} W{wb} #{index} X=1x{k} W={k}x{count}; COLUMNS={start}:{start + count}; W_NONZERO={k * count - counts.get(0, 0)}/{k * count}; W_DISTINCT={len(counts)}')
                log('W_DISTRIBUTION=' + json.dumps(counts, sort_keys=True) + '; W_SHA256=' + weight_hash + '; PACKED_SHA256=' + hashlib.sha256(packed).hexdigest())
                log('PYTHON_OUTPUT_SHA256=' + hashlib.sha256(struct.pack('<' + 'i' * count, *expected)).hexdigest())
                folder = root / f'{name}_w{wb}_tile{index:03d}'
                row = run_tile(args, sim, folder, name, k, count, wb, start, x, packed, expected)
                rows.append(row)
                if row['status'] == 'STOPPED':
                    raise InterruptedError('Stop requested')
            tally = collections.Counter(r['status'] for r in rows)
            status = 'PASS' if tally['PASS'] == len(rows) else ('TIMEOUT' if tally['TIMEOUT'] else 'FAIL')
            record = dict(case=name, k=k, m=m, wb=wb, status=status,
                          tiles=len(rows), matches=sum(r['matches'] for r in rows),
                          active_cycles=sum(r['cycles'] or 0 for r in rows),
                          measured_tiles=sum(r['cycles'] is not None for r in rows),
                          core_compute=sum(r['core_compute'] or 0 for r in rows),
                          seconds=round(sum(r['seconds'] for r in rows), 3), tile_results=dict(tally))
            records.append(record)
            log('CASE RESULT: ' + json.dumps(record, sort_keys=True))
        complete = len(records) == 18
    except BaseException as error:
        log(f'RUN INTERRUPTED: {type(error).__name__}: {error}')
    finally:
        if original is not None:
            if selected is not None and CONFIG.read_bytes() == selected:
                CONFIG.write_bytes(original)
            unchanged = bool(hashes) and all(Path(p).is_file() and sha(p) == v for p, v in hashes.items())
        log('\nTOTAL RESULTS\nCase | X | W | W bits | Status | Exact matches | HW tiles | Measured tiles | Active HW cycles | Core compute cycles')
        for r in records:
            log(f'{r["case"]} | 1x{r["k"]} | {r["k"]}x{r["m"]} | {r["wb"]} | {r["status"]} | {r["matches"]}/{r["m"]} | {r["tiles"]} | {r["measured_tiles"]} | {r["active_cycles"]} | {r["core_compute"]}')
        log(f'TOTAL: {dict(collections.Counter(r["status"] for r in records))}; {len(records)}/18 logical tests completed. Missing cycles are unavailable, never estimated; totals cover measured tiles only.')
        log(f'Original HW/SW sources unchanged and Config.scala restored: {unchanged}')
        log('RUN COMPLETE' if complete and unchanged else 'RUN INCOMPLETE')
        log('END: ' + now())
        if original is None or CONFIG.read_bytes() == original:
            log('Temporary vector sources, binaries, UART output and build files removed; script and this single log retained.')
            shutil.rmtree(STATE)
        else:
            log('Temporary recovery state retained because Config.scala was changed externally: ' + str(STATE))
        for lock in reversed(locks):
            lock.close()
    return 0 if complete and unchanged and all(r['status'] == 'PASS' for r in records) else 1


def latest_log():
    for path in sorted(LOGDIR.glob('run_*.log'), reverse=True):
        with path.open(errors='replace') as f:
            if 'LUMAX LQ SHAPE VERIFICATION' in f.read(512):
                return path
    return None


def status():
    active = False
    if STATE.exists() and (STATE / 'run.json').is_file():
        info = json.loads((STATE / 'run.json').read_text())
        path = Path(info['log'])
        print('Runner state:', STATE, '\nPID:', info['pid'], '\nLog:', path)
        if (STATE / 'lock').exists():
            with (STATE / 'lock').open('a+') as f:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    print('Worker lock is free: worker is starting or has exited; inspect the log.')
                except BlockingIOError:
                    active = True
                    print('Worker active (filesystem lock held).')
    else:
        path = latest_log()
        print('No active runner state.')
    if path and path.exists():
        text = path.read_text(errors='replace')
        cases = re.findall(r'^CASE RESULT: (.*)$', text, re.M)
        rows = [json.loads(row) for row in cases]
        print(f'Completed logical tests: {len(rows)}/18', dict(collections.Counter(r['status'] for r in rows)))
        tile_lines = re.findall(r'^TILE RESULT .*$', text, re.M)
        print('Completed native HW tests:', len(tile_lines), '/204')
        statuses = collections.Counter(json.loads(line.split(': ', 1)[1])['status'] for line in tile_lines)
        print('Native results:', dict(statuses))
        activity = [line for line in text.splitlines()
                    if re.match(r'^(TILE RESULT |TILE LQ|CASE RESULT:|TOTAL:|RUN COMPLETE|RUN INCOMPLETE|END:)', line)]
        print('\n'.join(activity[-4:]))
        if not active and not re.search(r'^END:', text, re.M):
            print('Latest saved run was interrupted; activity above is historical, not currently running.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'status', 'stop', 'check', 'worker'))
    parser.add_argument('--design', choices=DESIGNS, default='b8_rf8')
    parser.add_argument('--t', type=int, default=16)
    parser.add_argument('--timeout', type=int, default=7200, help='seconds per native HW operation')
    parser.add_argument('--build-timeout', type=int, default=7200)
    parser.add_argument('--compile-timeout', type=int, default=600)
    parser.add_argument('--max-cycles', type=int, default=20000000000)
    parser.add_argument('--jobs', type=int, default=8)
    parser.add_argument('--log', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 2 <= args.t <= 1000:
        parser.error('--t must be 2..1000')
    if min(args.timeout, args.build_timeout, args.compile_timeout, args.max_cycles, args.jobs) <= 0:
        parser.error('Timeouts, max-cycles and jobs must be positive')
    if args.action == 'status':
        status()
        return 0
    if args.action == 'stop':
        if STATE.is_dir():
            (STATE / 'stop').touch()
            print('Stop requested; child processes will stop and Config.scala will be restored.')
        else:
            print('No active runner state.')
        return 0
    if args.action == 'worker':
        if not args.log or not STATE.is_dir():
            parser.error('worker is an internal command; use start')
        return worker(args)
    preflight(args)
    env = environment()
    if args.action == 'check':
        ast.parse(SELF.read_text())
        print('PRECHECK OK. No make build, RISC-V build or simulation started.')
        print(f'Design: {args.design}; T={args.t}; one make build, reused throughout.')
        for name, k, m, wb in plans(args.t):
            print(f'{name} input16 W{wb}: 1x{k} x {k}x{m}; native HW operations={len(tiles(name, m))}')
        print('TOTAL: 18 logical tests; 204 native HW operations. Per-operation timeout:', args.timeout, 'seconds.')
        return 0
    if STATE.exists():
        raise RuntimeError('Runner state already exists; use status/stop: ' + str(STATE))
    STATE.mkdir(mode=0o700)
    LOGDIR.mkdir(exist_ok=True)
    path = LOGDIR / ('run_' + datetime.now().astimezone().strftime('%Y%m%d_%H%M%S_%f') + '.log')
    path.touch(exist_ok=False)
    (STATE / 'run.json').write_text(json.dumps(dict(pid='starting', log=str(path))))
    cmd = [sys.executable, str(SELF), 'worker', '--design', args.design, '--t', str(args.t),
           '--timeout', str(args.timeout), '--build-timeout', str(args.build_timeout),
           '--compile-timeout', str(args.compile_timeout), '--max-cycles', str(args.max_cycles),
           '--jobs', str(args.jobs), '--log', str(path)]
    try:
        # Bootstrap messages are temporary; all retained results go into path.
        with (STATE / 'console.log').open('w') as output:
            proc = subprocess.Popen(cmd, cwd=CY, env=env, stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        # A worker can fail/clean up immediately (e.g. another runner owns Config).
        if STATE.exists():
            metadata = STATE / 'run.json'
            metadata.write_text(json.dumps(dict(pid=proc.pid, log=str(path))))
    except BaseException:
        if 'proc' in locals() and proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)
        if STATE.exists():
            shutil.rmtree(STATE)
        if not path.stat().st_size:
            path.unlink()
        raise
    print('Started detached in background. PID:', proc.pid)
    print('You can close SSH. Log:', path)
    print('Status: python3 ' + str(SELF) + ' status')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f'ERROR: {type(error).__name__}: {error}', file=sys.stderr)
        raise SystemExit(1)
