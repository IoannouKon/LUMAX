"""Local dashboard storage, planning and real Verilator execution.

No simulator model is implemented in Python. Every native multiplication is
performed by a RISC-V ELF issuing the existing LUMAX RoCC instructions.
"""
import collections
from datetime import datetime
import fcntl
import hashlib
import json
import math
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
import tempfile
import threading
import time
import uuid

sys.dont_write_bytecode = True
import run_llama_shapes as hw
import cycle_compare
import fp_compare

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / 'logs'
DATA = ROOT / 'gui_data'
RUNTIME = ROOT / '.gui'
FINAL = {'PASS', 'FAIL', 'TIMEOUT', 'ERROR', 'BUILD_FAIL', 'STOPPED', 'UNVERIFIED', 'INTERRUPTED', 'ESTIMATED'}
LOCAL_SUITES = ('custom', 'llama_fast')
PATTERNS = ['random', 'zero_x', 'zero_w', 'zero_both', 'min_min', 'max_max',
            'min_max', 'max_min', 'alternating', 'onehot_x', 'onehot_w']
BUILTINS = {
    'regression': {'name': '180-test regression', 'script': 'run_regression.py', 'count': 180},
    'test_cases': {'name': 'TC01–TC11', 'script': 'run_test_cases.py', 'count': 3798},
    'llama_shapes': {'name': 'Llama2 dimensions', 'script': 'run_llama_shapes.py', 'count': 18},
    'matrix_1024': {'name': 'Native 1024×1024', 'script': 'run_1024.py', 'count': 3},
}
DEFAULT_OPTIONS = dict(mode='auto', reference='python', cycles=True, guards=True,
                       integrity=False, matrices=False, timeout=21600,
                       build_timeout=7200, compile_timeout=1200, jobs=8,
                       workers=6, max_cycles=100000000000, stop_on_fail=False,
                       tile_k=0, tile_m=0, t=16, tcs=[],
                       fp_comparison='exact', atol=0.0, rtol=0.0)


def now():
    return datetime.now().astimezone().isoformat()


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, indent=2) + '\n')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def integer(value, name, low=1, high=16384):
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]+', str(value)):
        raise ValueError(name + ' must be a whole number')
    result = int(value)
    if not low <= result <= high:
        raise ValueError(f'{name} must be {low}–{high}')
    return result


def config_defaults():
    text = hw.CONFIG.read_text()
    def get(key):
        return int(re.search(r'(?m)^\s*' + key + r'\s*=\s*(\d+)', text)[1])
    return dict(max_k=get('Cin'), max_m=get('Cout'))


def activation(design):
    return design.get('activation', 'int16')


def nonnegative_float(value, name):
    if isinstance(value, bool):
        raise ValueError(name + ' must be a finite nonnegative number')
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(name + ' must be a finite nonnegative number')
    if not math.isfinite(result) or result < 0:
        raise ValueError(name + ' must be a finite nonnegative number')
    return result


def validate_design(value):
    name = str(value.get('name', '')).strip()
    if not 1 <= len(name) <= 64:
        raise ValueError('Give the design a name, up to 64 characters')
    b = integer(value.get('b'), 'b', 2, 32)
    rf = integer(value.get('rf'), 'RF', 2, 64)
    if b & (b - 1) or rf & (rf - 1):
        raise ValueError('b and RF must be powers of two')
    if activation(value) not in ('int16', 'fp16'):
        raise ValueError('Activation format must be INT16 or FP16')
    return dict(id=value.get('id') or 'design_' + uuid.uuid4().hex[:12], name=name,
                b=b, rf=rf, max_k=integer(value.get('max_k'), 'Maximum input length'),
                max_m=integer(value.get('max_m'), 'Maximum outputs', 1, 4096),
                activation=activation(value), builtin=False)


class Store:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.data = self.root / 'gui_data'
        self.jobs = self.root / 'logs' / 'gui'
        self.data.mkdir(parents=True, exist_ok=True)
        self.jobs.mkdir(parents=True, exist_ok=True)

    def designs(self):
        limits = config_defaults()
        defaults = [dict(id=f'b{b}_rf{rf}' + ('_fp16' if fmt == 'fp16' else ''),
                         name=f'b{b}_rf{rf}' + (' FP16' if fmt == 'fp16' else ''), b=b, rf=rf,
                         **limits, activation=fmt, builtin=True)
                    for fmt in ('int16', 'fp16') for b in (2, 4, 8) for rf in (4, 8)]
        saved = [dict(d, activation=activation(d)) for d in read_json(self.data / 'designs.json', [])]
        return defaults + saved

    def save_design(self, value):
        item = validate_design(value)
        if not re.fullmatch(r'design_[a-f0-9]{12}', item['id']):
            raise ValueError('Clone a standard design to create an editable design')
        with (self.data / '.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            values = read_json(self.data / 'designs.json', [])
            values = [v for v in values if v['id'] != item['id']] + [item]
            write_json(self.data / 'designs.json', values)
        return item

    def suites(self):
        return read_json(self.data / 'suites.json', [])

    def save_suite(self, value):
        suite = normalize_suite(value, self.designs())
        identifier = value.get('id') or 'suite_' + uuid.uuid4().hex[:12]
        if not re.fullmatch(r'suite_[a-f0-9]{12}', identifier):
            raise ValueError('Invalid suite identifier')
        suite.update(id=identifier, updated=now())
        with (self.data / '.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            values = [s for s in self.suites() if s['id'] != identifier] + [suite]
            write_json(self.data / 'suites.json', values)
        return suite

    def create_job(self, value):
        suite = normalize_suite(value, self.designs())
        plan = make_plan(suite)
        identifier = 'run_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8]
        folder = self.jobs / identifier
        folder.mkdir()
        job = dict(id=identifier, name=suite['name'], status='QUEUED', created=now(),
                   phase='Queued', completed=0, total=plan['cases'], operations_done=0,
                   operations_total=plan['operations'], results=[], suite=suite, plan=plan,
                   log=str((folder / 'run.log').relative_to(self.root / 'logs')))
        write_json(folder / 'job.json', job)
        write_json(folder / 'suite.json', suite)
        return job

    def folder(self, identifier):
        if not re.fullmatch(r'run_\d{8}_\d{6}_[a-f0-9]{8}', identifier):
            raise ValueError('Invalid run identifier')
        folder = self.jobs / identifier
        if not (folder / 'job.json').is_file():
            raise FileNotFoundError('Run not found')
        return folder

    def jobs_list(self):
        return [read_json(p) for p in sorted(self.jobs.glob('*/job.json'), reverse=True)][:100]

    def cancel(self, identifier):
        folder = self.folder(identifier)
        job = read_json(folder / 'job.json')
        if job['status'] in FINAL:
            return job
        (folder / 'STOP').touch()
        if job['status'] == 'QUEUED':
            job.update(status='STOPPED', phase='Stopped before starting', ended=now())
            write_json(folder / 'job.json', job)
        return job


def normalize_suite(value, designs):
    if not isinstance(value, dict):
        raise ValueError('Expected a suite object')
    name = str(value.get('name', '')).strip()
    if not 1 <= len(name) <= 80:
        raise ValueError('Give the suite a name, up to 80 characters')
    kind = value.get('kind', 'custom')
    if kind not in (*LOCAL_SUITES, *BUILTINS):
        raise ValueError('Unknown suite type')
    lookup = {d['id']: d for d in designs}
    identifiers = value.get('design_ids', [])
    if not isinstance(identifiers, list) or not identifiers or len(identifiers) > 12:
        raise ValueError('Select at least one design (at most 12)')
    chosen = []
    for identifier in dict.fromkeys(identifiers):
        if identifier not in lookup:
            raise ValueError('Selected design no longer exists')
        chosen.append(dict(lookup[identifier]))
    if kind not in LOCAL_SUITES and any(not d['builtin'] or activation(d) != 'int16' for d in chosen):
        raise ValueError('Original script suites use standard INT16 designs; choose Custom matrices or Llama2 fast for FP16')
    options = dict(DEFAULT_OPTIONS)
    incoming = value.get('options', {})
    if not isinstance(incoming, dict):
        raise ValueError('Options must be an object')
    options.update({k: v for k, v in incoming.items() if k in options})
    if options['mode'] not in ('auto', 'native', 'tile'):
        raise ValueError('Unknown tiling mode')
    if options['reference'] not in ('python', 'riscv', 'none'):
        raise ValueError('Unknown reference method')
    if options['fp_comparison'] not in ('exact', 'tolerance'):
        raise ValueError('Unknown FP16 comparison mode')
    for key in ('atol', 'rtol'):
        options[key] = nonnegative_float(options[key], key)
    for key in ('cycles', 'guards', 'integrity', 'matrices', 'stop_on_fail'):
        if not isinstance(options[key], bool):
            raise ValueError(key + ' must be true or false')
    for key, low, high in [('timeout', 10, 172800), ('build_timeout', 60, 86400),
                           ('compile_timeout', 30, 7200), ('jobs', 1, 32),
                           ('workers', 1, 12), ('max_cycles', 1000, 10**12),
                           ('tile_k', 0, 16384), ('tile_m', 0, 4096), ('t', 2, 1000)]:
        options[key] = integer(options[key], key, low, high)
    if not isinstance(options['tcs'], list) or any(t not in [f'TC{i:02d}' for i in range(1, 12)] for t in options['tcs']):
        raise ValueError('Invalid TC selection')
    tests = []
    if kind == 'llama_fast':
        # One largest-fitting native tile for each shape and precision.
        options.update(mode='native', tile_k=0, tile_m=0, cycles=True)
        tests = [dict(name=name, k=k, m=m, weights=[8, 4, 2], pattern='random', repeats=1, seed='')
                 for name, k, m, wb in hw.plans(options['t']) if wb == 8]
    if kind == 'custom':
        rows = value.get('tests', [])
        if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
            raise ValueError('Add 1–200 matrix tests')
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError('Each test must be an object')
            k, m = integer(row.get('k'), 'K'), integer(row.get('m'), 'M', 1, 65536)
            if k * m > 8 * 1024 * 1024:
                raise ValueError('Each logical matrix may contain at most 8 million weights')
            weights = row.get('weights', [8, 4, 2])
            if not isinstance(weights, list) or not weights or any(w not in (8, 4, 2) for w in weights):
                raise ValueError('Select weight precision 8, 4 and/or 2')
            pattern = row.get('pattern', 'random')
            if pattern not in PATTERNS:
                raise ValueError('Unknown data pattern')
            initial = str(row.get('seed', '')).strip()
            if initial:
                integer(initial, 'Seed', 0, 2**64 - 1)
            tests.append(dict(name=str(row.get('name') or f'{k}×{m}')[:64], k=k, m=m,
                              weights=list(dict.fromkeys(weights)), pattern=pattern,
                              repeats=integer(row.get('repeats', 1), 'Repeats', 1, 100), seed=initial))
    suite = dict(name=name, kind=kind, design_ids=[d['id'] for d in chosen], designs=chosen,
                 tests=tests, options=options)
    make_plan(suite)
    return suite


def tile_ranges(k, m, design, options):
    mode = options['mode']
    if mode == 'native':
        if k > design['max_k'] or m > design['max_m']:
            raise ValueError(f'{k}×{m} exceeds {design["name"]} capacity ({design["max_k"]}×{design["max_m"]}); choose Auto or Tiles')
        tk, tm = k, m
    else:
        tk = min(k, design['max_k'], options['tile_k'] or design['max_k'])
        tm = min(m, design['max_m'], options['tile_m'] or (512 if mode == 'tile' else design['max_m']))
    return [(a, min(tk, k - a), c, min(tm, m - c))
            for c in range(0, m, tm) for a in range(0, k, tk)]


def representative_tile(test, design):
    """Sample the largest native rectangle; edge tiles are charged full cost."""
    k, m = min(test['k'], design['max_k']), min(test['m'], design['max_m'])
    k_tiles = (test['k'] + k - 1) // k
    m_tiles = (test['m'] + m - 1) // m
    return dict(k=k, m=m, k_tiles=k_tiles, m_tiles=m_tiles, tiles=k_tiles * m_tiles)


def add_cycle_estimate(result, test, design):
    geometry = representative_tile(test, design)
    cycles, core = result.get('cycles'), result.get('core_cycles')
    usable = result['status'] in ('PASS', 'UNVERIFIED') and cycles is not None and cycles > 0
    tiled = geometry['tiles'] > 1
    result.update(full_k=test['k'], full_m=test['m'], tile_grid=[geometry['k_tiles'], geometry['m_tiles']],
                  tiles_needed=geometry['tiles'], sample_status=result['status'],
                  full_result_verified=result['status'] == 'PASS' and not tiled,
                  estimated_cycles=cycles * geometry['tiles'] if usable and tiled else None,
                  estimated_core_cycles=core * geometry['tiles'] if usable and tiled and core else None,
                  estimate_note='One measured tile × required tile count; smaller edge tiles charged as full tiles. Host assembly and boot overhead excluded.')
    if tiled and result['status'] == 'PASS': result['status'] = 'ESTIMATED'
    return result


def suite_status(rows):
    if not rows: return 'ERROR'
    if any(r['status'] == 'TIMEOUT' for r in rows): return 'TIMEOUT'
    if any(r['status'] not in ('PASS', 'ESTIMATED', 'UNVERIFIED') for r in rows): return 'FAIL'
    if any(r['status'] == 'UNVERIFIED' for r in rows): return 'UNVERIFIED'
    return 'ESTIMATED' if any(r['status'] == 'ESTIMATED' for r in rows) else 'PASS'


def make_plan(suite):
    kind = suite['kind']
    if kind == 'llama_fast':
        details = []
        for design in suite['designs']:
            for test in suite['tests']:
                tile = representative_tile(test, design)
                details.append(dict(design=design['name'], test=test['name'], k=test['k'], m=test['m'],
                                    sample_k=tile['k'], sample_m=tile['m'], cases=3, operations=3,
                                    tiles=tile['tiles'], reduction=tile['k_tiles'] > 1))
        return dict(cases=len(details) * 3, operations=len(details) * 3,
                    builds=len(suite['designs']), detail=details, estimated=True,
                    note='Run one largest-fitting HW tile per shape/precision. Estimated full cycles = measured tile cycles × required tile count (ceil in each dimension). Only sampled outputs are checked; edge tiles charged full cost.')
    if kind != 'custom':
        designs = len(suite['designs'])
        per_tc = dict(zip([f'TC{i:02d}' for i in range(1, 12)], [9, 9, 12, 36, 27, 9, 30, 432, 60, 6, 3]))
        count = (30 if kind == 'regression' else
                 sum(per_tc[t] for t in (suite['options']['tcs'] or per_tc)) if kind == 'test_cases' else
                 18 if kind == 'llama_shapes' else 3) * designs
        operations = 204 * designs if kind == 'llama_shapes' else count
        if kind == 'test_cases' and ('TC11' in suite['options']['tcs'] or not suite['options']['tcs']):
            operations += 15 * designs
        return dict(cases=count, operations=operations, builds=designs, detail=[],
                    note='Uses the original script settings and checks; all weight widths, input16. TC11 keeps its six-operation reuse test.')
    cases, operations, details = 0, 0, []
    for design in suite['designs']:
        for test in suite['tests']:
            chunks = tile_ranges(test['k'], test['m'], design, suite['options'])
            count = len(test['weights']) * test['repeats']
            cases += count
            operations += count * len(chunks)
            details.append(dict(design=design['name'], test=test['name'], k=test['k'], m=test['m'],
                                cases=count, operations=count * len(chunks), tiles=len(chunks),
                                reduction=any(a != 0 or n != test['k'] for a, n, c, d in chunks)))
    if cases > 2000 or operations > 20000:
        raise ValueError('Split this suite into smaller runs (maximum 2,000 cases / 20,000 HW operations)')
    return dict(cases=cases, operations=operations, builds=len(suite['designs']), detail=details,
                note='Each tile is computed in LUMAX. Output tiles concatenate; input-length tiles add partial outputs in host software. No padding.')


def is_locked(path):
    path = Path(path)
    if not path.exists():
        return False
    with path.open('rb') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True


def fingerprint():
    paths = list((hw.GEN / 'src/main/scala').glob('*.scala'))
    paths += [hw.SRC / n for n in ('Linear-sw.c', 'rocc.h', 'compiler.h', 'build.sh')]
    paths += [hw.CY / 'build.sbt', hw.CY / 'generators/chipyard/src/main/scala/config/LUMAXConfigs.scala']
    return {str(p): hw.sha(p) for p in paths}


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.data = read_json(self.folder / 'job.json')
        self.lock = threading.RLock()
        self.proc = None
        self.ended = threading.Event()

    def update(self, **values):
        with self.lock:
            self.data.update(values, heartbeat=now())
            write_json(self.folder / 'job.json', self.data)

    def log(self, message):
        with self.lock:
            with (self.folder / 'run.log').open('a') as f:
                f.write(str(message) + '\n')

    def cancelled(self):
        if (self.folder / 'STOP').exists():
            raise Cancelled('Stopped by user')

    def heartbeat(self):
        while not self.ended.wait(3):
            values = {}
            proc = self.proc
            if proc and proc.poll() is None:
                try:
                    stat = Path(f'/proc/{proc.pid}/stat').read_text().rsplit(')', 1)[1].split()
                    values['process'] = dict(pid=proc.pid, state=stat[0],
                        cpu_seconds=round((int(stat[11]) + int(stat[12])) / os.sysconf('SC_CLK_TCK'), 2))
                except OSError:
                    pass
            self.update(**values)

    def command(self, args, cwd, output, timeout, env, stage):
        self.cancelled()
        self.update(phase=stage, command=shlex.join(args), command_started=now(), process=None)
        self.log('COMMAND: ' + shlex.join(args))
        with Path(output).open('w') as stream:
            self.proc = subprocess.Popen(args, cwd=cwd, env=env, stdout=stream,
                stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + timeout
            offset = 0
            try:
                while True:
                    self.cancelled()
                    # Live markers come from the simulated CPU, not a Python model.
                    with Path(output).open(errors='replace') as f:
                        f.seek(offset)
                        lines = f.readlines()
                        offset = f.tell()
                    for line in lines:
                        if line.startswith('PHASE '):
                            self.update(phase=line.strip()[6:], last_uart=now())
                            self.log('TARGET ' + line.strip())
                    try:
                        return self.proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        if time.monotonic() >= deadline:
                            raise TimeoutError(f'{stage}: exceeded {timeout}s')
            finally:
                if self.proc.poll() is None:
                    os.killpg(self.proc.pid, signal.SIGTERM)
                    try:
                        self.proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(self.proc.pid, signal.SIGKILL)
                        self.proc.wait()
                self.proc = None


def random_case(test, wb, initial, fmt='int16'):
    xs = int.from_bytes(hashlib.sha256((initial + ':x').encode()).digest()[:8], 'little')
    ws = int.from_bytes(hashlib.sha256((initial + ':w').encode()).digest()[:8], 'little')
    x = hw.input_values(test['k'], xs)
    if fmt == 'fp16':
        x = [v & 0xffff for v in x]
        x = [v ^ 0x400 if v & 0x7c00 == 0x7c00 else v for v in x]
    rng = hw.RandomValues(ws)
    w = [rng.value(wb) for _ in range(test['k'] * test['m'])]
    lo, hi = -(1 << (wb - 1)), (1 << (wb - 1)) - 1
    pattern = test['pattern']
    if pattern in ('zero_x', 'zero_both'): x = [0] * len(x)
    if pattern in ('zero_w', 'zero_both'): w = [0] * len(w)
    minimum, maximum, one = (0xfbff, 0x7bff, 0x3c00) if fmt == 'fp16' else (-32768, 32767, 1)
    if pattern in ('min_min', 'min_max'): x = [minimum] * len(x)
    if pattern in ('max_min', 'max_max'): x = [maximum] * len(x)
    if pattern in ('min_min', 'max_min'): w = [lo] * len(w)
    if pattern in ('min_max', 'max_max'): w = [hi] * len(w)
    if pattern == 'alternating':
        x = [minimum if j % 2 else maximum for j in range(len(x))]
        w = [lo if (j + c) % 2 else hi for j in range(test['k']) for c in range(test['m'])]
    if pattern == 'onehot_x': x = [one] + [0] * (len(x) - 1)
    if pattern == 'onehot_w': w = [1] + [0] * (len(w) - 1)
    return x, w, str(xs), str(ws)


def pack_weights(w, k, m, wb):
    packed = bytearray((k * m * wb + 7) // 8)
    for c in range(m):
        for j in range(k):
            bit = (c * k + j) * wb
            packed[bit // 8] |= (w[j * m + c] & ((1 << wb) - 1)) << (bit % 8)
    return packed


def reference(x, w, m, fmt='int16'):
    if fmt == 'fp16':
        return fp_compare.model.reference(x, w, m, True)
    result = [sum(v * w[j * m + c] for j, v in enumerate(x)) for c in range(m)]
    if any(not -(1 << 31) <= v < (1 << 31) for v in result):
        raise ValueError('Reference exceeds int32; choose less extreme data or a smaller input length')
    return result


def fnv(data):
    value = 2166136261
    for byte in data:
        value = ((value ^ byte) * 16777619) & 0xffffffff
    return value


def c_array(ctype, name, values):
    return f'static const {ctype} {name}[{len(values)}] = {{' + ','.join(map(str, values)) + '};\n'


def test_source(base, design, x, w, wb, options):
    """Operands are loaded directly by +loadmem; no megabyte CPU memset/copy."""
    k, m = len(x), len(w) // len(x)
    packed = pack_weights(w, k, m, wb)
    fp16 = activation(design) == 'fp16'
    c = base.split('int main() {', 1)[0]
    for key, value in dict(IN_BITS=16, W_BITS=wb, Debug=0, XS=design['b'],
                           YS=1, Mem_row_factor=design['rf']).items():
        c, count = re.subn(r'(^#define\s+' + key + r'\s+)\d+', lambda mt: mt[1] + str(value), c, flags=re.M)
        if count != 1: raise ValueError('Missing C define ' + key)
    c = f'''#ifndef LUMAX_FP16_ACTIVATIONS
#define LUMAX_FP16_ACTIVATIONS {int(fp16)}
#elif LUMAX_FP16_ACTIVATIONS != {int(fp16)}
#error GUI software activation format disagrees with design
#endif
''' + c
    c += '\n#undef printf\n#include <stddef.h>\n'
    guard8 = ','.join(['165'] * 8)
    guard16 = ','.join(['165'] * 16)
    for name, ctype, values in [('xbuf', 'input_t', x), ('wbuf', 'uint8_t', packed),
                                ('obuf', 'output_t', [0x5a5a5a5a] * m)]:
        const = '' if name == 'obuf' else 'const '
        c += f'static {const}struct __attribute__((packed, aligned(8))) {{ uint8_t pre[8]; {ctype} data[{len(values)}]; uint8_t post[16]; }} {name} = '
        c += '{{' + guard8 + '},{' + ','.join(map(str, values)) + '},{' + guard16 + '}};\n'
        c += f'_Static_assert(offsetof(__typeof__({name}), data)==8, "DMA alignment");\n'
        c += f'_Static_assert(offsetof(__typeof__({name}), post)==8+sizeof({name}.data), "Immediate guard");\n'
    if options['reference'] == 'riscv' and not fp16:
        c += c_array('int8_t', 'reference_w', w)
    c += '''
static int boundary(const uint8_t *a,const uint8_t *b) {
  for(int i=0;i<8;++i) if(a[i]!=165) return 0;
  for(int i=0;i<16;++i) if(b[i]!=165) return 0;
  return 1;
}
static uint32_t checksum(const void *p,size_t n) {
  const uint8_t *a=p; uint32_t h=2166136261u;
  for(size_t i=0;i<n;++i) h=(h^a[i])*16777619u;
  return h;
}
static void phase(const char *text) { printf("PHASE %s\\n",text); fflush(stdout); }
'''
    c += f'''static void values(const char *label,const output_t *data) {{
  char report[{m * 16 + 1}]; int used=0;
  for(int i=0;i<{m};++i) {{
    int n=snprintf(report+used,sizeof(report)-used,"{'%u' if fp16 else '%d'} ",data[i]);
    if(n<0||(unsigned)n>=sizeof(report)-used) return;
    used+=n;
  }}
  printf("%s %s\\n",label,report); fflush(stdout);
}}
'''
    c += '''
int main(void) {
  phase("Program started; operands already loaded");
  QuantizedTensor_input tx={.q=(input_t*)xbuf.data};
  QuantizedTensor_weight tw={.q=(weight_t*)wbuf.data};
  output_t *actual=obuf.data;
'''
    c += f'  preload_hw({k},1,{m},x_slice_max*RF,y_slice,tx.q,tw.q,actual);\n'
    c += '  phase("Computing in LUMAX hardware");\n  unsigned long begin=(unsigned long)rdcycle();\n'
    c += f'  matmul_hw_uniform(actual,&tx,&tw,{k},{m},x_slice_max*RF,y_slice,1);\n'
    c += '  unsigned long end=(unsigned long)rdcycle();\n  phase("LUMAX hardware completed");\n'
    if options['cycles']:
        c += '  unsigned long pc[6]; for(int i=0;i<6;++i) pc[i]=read_rd();\n'
        c += '  printf("CYCLES %lu %lu %lu %lu %lu %lu CORE %lu\\n",pc[0],pc[1],pc[2],pc[3],pc[4],pc[5],end-begin);\n'
    c += '  int safe=1;\n'
    if options['guards']:
        c += '  safe=boundary(xbuf.pre,xbuf.post)&&boundary(wbuf.pre,wbuf.post)&&boundary(obuf.pre,obuf.post);\n'
    if options['integrity']:
        c += '  phase("Checking input integrity");\n'
        c += f'  safe &= checksum(xbuf.data,sizeof(xbuf.data))=={fnv(struct.pack("<"+("H" if fp16 else "h")*k,*x))}u;\n'
        c += f'  safe &= checksum(wbuf.data,sizeof(wbuf.data))=={fnv(packed)}u;\n'
    c += '  printf("GUARDS %s\\n",safe?"PASS":"FAIL");\n'
    if options['reference'] == 'riscv':
        c += f'  phase("Computing RISC-V reference");\n  output_t ref[{m}];\n'
        if fp16:
            c += f'  matmul_sw_uniform(ref,&tx,&tw,{k},{m},1);\n'
        else:
            c += f'  for(int col=0;col<{m};++col) {{ int64_t sum=0; for(int j=0;j<{k};++j) sum+=(int64_t)xbuf.data[j]*reference_w[j*{m}+col];\n'
            c += '    if(sum<INT32_MIN||sum>INT32_MAX) { printf("OVERFLOW\\n"); return 2; } ref[col]=(int32_t)sum; }\n'
        c += '  values("C_REFERENCE",ref);\n'
    c += '  values("HW_OUTPUT",actual);\n'
    c += '  printf("TEST_DONE\\n"); fflush(stdout); return safe?0:1;\n}\n'
    return c


def compare_outputs(actual, expected, options, fmt='int16'):
    if fmt == 'fp16':
        return fp_compare.compare(actual, expected, options)
    matches = sum(a == b for a, b in zip(actual, expected))
    return dict(matches=matches, exact_matches=matches,
                numeric_pass=len(actual) == len(expected) and actual == expected,
                mismatches=[dict(index=i, hw=a, reference=b)
                            for i, (a, b) in enumerate(zip(actual, expected)) if a != b][:16])


def parse_result(uart, expected, options, exit_code, count, fmt='int16'):
    outputs = re.findall(r'^HW_OUTPUT (.*)$', uart, re.M)
    try:
        actual = list(map(int, outputs[0].split())) if len(outputs) == 1 else []
    except ValueError:
        actual = []
    if fmt == 'fp16' and any(not 0 <= word <= 0xffffffff for word in actual):
        actual = []
    result = dict(status='FAIL', matches=0, elements=count, cycles=None, core_cycles=None,
                  guards='PASS' if re.findall(r'^GUARDS (PASS|FAIL)$', uart, re.M) == ['PASS'] else 'FAIL',
                  actual=actual, activation=fmt, output_format='fp32' if fmt == 'fp16' else 'int32')
    if options['reference'] == 'riscv':
        rows = re.findall(r'^C_REFERENCE (.*)$', uart, re.M)
        try: expected = list(map(int, rows[0].split())) if len(rows) == 1 else None
        except ValueError: expected = None
        if fmt == 'fp16' and expected is not None and any(not 0 <= word <= 0xffffffff for word in expected):
            expected = None
    result['expected'] = expected
    if expected is not None:
        result.update(compare_outputs(actual, expected, options, fmt))
    cycles = re.findall(r'^CYCLES ((?:\d+ ){5}\d+) CORE (\d+)$', uart, re.M)
    valid_cycles = not options['cycles']
    if len(cycles) == 1:
        pc = list(map(int, cycles[0][0].split()))
        result.update(pc=pc, cycles=sum(pc[i] for i in (0, 1, 4, 5)), core_cycles=int(cycles[0][1]))
        valid_cycles = result['cycles'] > 0 and result['core_cycles'] > 0
    complete = exit_code == 0 and uart.splitlines().count('TEST_DONE') == 1 and len(actual) == count and result['guards'] == 'PASS' and valid_cycles
    if complete and options['reference'] == 'none': result['status'] = 'UNVERIFIED'
    elif complete and expected is not None and len(expected) == count and result['numeric_pass']: result['status'] = 'PASS'
    return result


def build_design(job, design, base_config, temporary, env):
    options = job.data['suite']['options']
    text = base_config.decode()
    for key, value in [('x_slice', design['b']), ('Mem_row_factor', design['rf']),
                       ('Cin', design['max_k']), ('Cout', design['max_m'])]:
        text, count = re.subn(r'(?m)^(\s*' + key + r'\s*=\s*)\d+', lambda m: m[1] + str(value), text)
        if count != 1: raise ValueError('Missing HW parameter ' + key)
    text, count = re.subn(r'(?m)^(\s*DEBUG\s*=\s*)(true|false)',
                          lambda mt: mt[1] + ('true' if options['cycles'] else 'false'), text)
    if count != 1: raise ValueError('Missing HW DEBUG switch')
    text, count = re.subn(r'(?m)^(\s*FP16_ACTIVATIONS\s*=\s*)(true|false)',
                         lambda mt: mt[1] + ('true' if activation(design) == 'fp16' else 'false'), text)
    if count != 1 and activation(design) == 'fp16':
        raise ValueError('Missing build-time FP16_ACTIVATIONS switch')
    build = temporary / design['id']
    build.mkdir()
    (build / 'Config.scala').write_text(text)
    sim = build / 'simulator-LUMAXROcketConfig'
    args = ['make', '-j' + str(options['jobs']), 'CONFIG=LUMAXROcketConfig',
            'sim=' + str(sim), 'gen_dir=' + str(build / 'generated-src')]
    job.log('DESIGN CONFIGURATION\n' + text)
    try:
        hw.CONFIG.write_text(text)
        code = job.command(args, hw.CY / 'sims/verilator', build / 'make.log', options['build_timeout'], env, 'Building ' + design['name'])
    finally:
        if hw.CONFIG.read_bytes() == text.encode(): hw.CONFIG.write_bytes(base_config)
        elif hw.CONFIG.read_bytes() != base_config: raise RuntimeError('Config.scala changed externally; recovery snapshot retained')
    job.log('MAKE OUTPUT\n' + (build / 'make.log').read_text(errors='replace'))
    if code or not sim.is_file(): raise RuntimeError(f'Make failed for {design["name"]} (exit {code})')
    job.log('SIMULATOR SHA256=' + hw.sha(sim))
    shutil.rmtree(build / 'generated-src')
    return sim


def run_operation(job, sim, design, base, options, x, w, wb, folder, env, expected):
    folder.mkdir()
    record = None
    started = time.monotonic()
    try:
        job.cancelled()
        (folder / 'Linear-sw.c').write_text(test_source(base, design, x, w, wb, options))
        for name in ('build.sh', 'rocc.h', 'compiler.h'): shutil.copy2(hw.SRC / name, folder / name)
        job.log('TEST C SHA256=' + hw.sha(folder / 'Linear-sw.c'))
        case_env = dict(env)
        snapshot = sim.parent / 'Config.scala'
        if snapshot.is_file():
            case_env['LUMAX_CONFIG_PATH'] = str(snapshot)
        case_env['TEST_CFLAGS'] = env.get('TEST_CFLAGS', '-O2') + f' -DLUMAX_FP16_ACTIVATIONS={int(activation(design) == "fp16")}'
        code = job.command(['bash', './build.sh', 'baremetal'], folder, folder / 'build.log', options['compile_timeout'], case_env, 'Compiling RISC-V test')
        if code:
            job.log((folder / 'build.log').read_text(errors='replace'))
            return dict(status='BUILD_FAIL', matches=0, elements=len(w) // len(x), cycles=None, core_cycles=None)
        elf = folder / 'Linear-sw.riscv'
        job.log('ELF SHA256=' + hw.sha(elf))
        args = [str(sim), '+permissive', '+max-cycles=' + str(options['max_cycles']), '+loadmem=' + str(elf), '+permissive-off', str(elf)]
        code = job.command(args, folder, folder / 'uart.log', options['timeout'], env, 'Starting Verilator')
        uart = (folder / 'uart.log').read_text(errors='replace').replace('\r\n', '\n')
        job.update(phase='Comparing actual HW output')
        record = parse_result(uart, expected, options, code, len(w) // len(x), activation(design))
        if '*** FAILED ***' in uart and 'timeout' in uart.lower(): record['status'] = 'TIMEOUT'
        job.log('UART STATUS\n' + '\n'.join(line for line in uart.splitlines()
                if not line.startswith(('HW_OUTPUT ', 'C_REFERENCE '))))
        if options['matrices']:
            job.log('X=' + json.dumps(x) + '\nW (row-major)=' + json.dumps(w))
            job.log('REFERENCE=' + json.dumps(record.get('expected')) + '\nHW_OUTPUT=' + json.dumps(record.get('actual')))
            if activation(design) == 'fp16':
                job.log('X_FP16_VALUES=' + json.dumps([struct.unpack('<e', struct.pack('<H', v))[0] for v in x]))
                job.log('HW_FP32_VALUES=' + json.dumps([fp_compare.display(v) for v in record.get('actual', [])]))
        if record.get('actual'):
            word_type = 'I' if activation(design) == 'fp16' else 'i'
            record['output_sha256'] = hashlib.sha256(struct.pack('<' + word_type * len(record['actual']), *record['actual'])).hexdigest()
        return record
    except TimeoutError as error:
        job.log(str(error))
        return dict(status='TIMEOUT', matches=0, elements=len(w) // len(x), cycles=None, core_cycles=None)
    finally:
        job.log(f'OPERATION WALL SECONDS={time.monotonic() - started:.3f}')
        shutil.rmtree(folder)


def run_custom(job, temporary, env):
    suite, options = job.data['suite'], job.data['suite']['options']
    with hw.CONFIG.open('rb') as lock:
        while True:
            job.cancelled()
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                job.update(phase='Waiting for the current verification run', status='WAITING')
                time.sleep(1)
        job.update(status='RUNNING')
        original = hw.CONFIG.read_bytes()
        (temporary / 'original-config.scala').write_bytes(original)
        cycle_parameters = cycle_compare.physical_parameters(original.decode())
        job.update(cycle_parameters=cycle_parameters)
        hashes = fingerprint()
        base = (hw.SRC / 'Linear-sw.c').read_text()
        if not re.search(r'(?m)^#define\s+SCALE\s+0\b', base):
            raise ValueError('Custom verification requires unscaled output (SCALE=0)')
        for key, value in [('y_slice', 1), ('XBitWidth', 16), ('WBitWidth', 8), ('OutBitWidth', 32)]:
            if not re.search(r'(?m)^\s*' + key + r'\s*=\s*' + str(value) + r'\s*,', original.decode()):
                raise ValueError(f'Custom verification requires {key}={value}')
        if not re.search(r'(?m)^\s*SCALE\s*=\s*false\s*,', original.decode()):
            raise ValueError('Custom verification requires hardware SCALE=false')
        if any(activation(d) == 'fp16' for d in suite['designs']):
            for module in (fp_compare, fp_compare.model):
                path = Path(module.__file__)
                hashes[str(path)] = hw.sha(path)
        job.log('SOURCE SHA256=' + json.dumps(hashes, sort_keys=True))
        fast = suite['kind'] == 'llama_fast'
        if fast:
            job.log('LLAMA2 FAST ESTIMATE: one largest-fitting native tile per shape and W8/W4/W2. Extrapolated cycles are estimates, not full-matrix verification.')
        try:
            for design in suite['designs']:
                fmt = activation(design)
                fp16 = fmt == 'fp16'
                sim = build_design(job, design, original, temporary, env)
                for index, logical_test in enumerate(suite['tests']):
                    test = dict(logical_test)
                    if fast:
                        sample_tile = representative_tile(logical_test, design)
                        test.update(k=sample_tile['k'], m=sample_tile['m'])
                    for repeat in range(test['repeats']):
                        for wb in test['weights']:
                            job.cancelled()
                            if any(hw.sha(p) != v for p, v in hashes.items()): raise RuntimeError('Source files changed during run')
                            label = f'{design["name"]} [{fmt.upper()}]: {test["name"]}, W{wb}, repeat {repeat + 1}'
                            job.update(phase='Preparing random operands', current=label)
                            initial = test['seed'] or str(secrets.randbits(64))
                            initial += f':{design["id"]}:{index}:{repeat}:{wb}'
                            x, w, xs, ws = random_case(test, wb, initial, fmt)
                            k, m = test['k'], test['m']
                            full_ref = reference(x, w, m, fmt) if options['reference'] == 'python' or (fp16 and options['reference'] == 'riscv') else None
                            output, combined_ref = [0] * m, [0] * m
                            fp_parts = [[] for _ in range(m)]
                            output_complete = True
                            rows, measured, failed = [], [], False
                            job.log('\nCASE ' + label + f' X=1x{k} W={k}x{m} INPUT_SEED={xs} WEIGHT_SEED={ws} PATTERN={test["pattern"]}')
                            if fast:
                                job.log(f'FULL SHAPE 1x{logical_test["k"]} @ {logical_test["k"]}x{logical_test["m"]}; SAMPLE 1x{k} @ {k}x{m}; TILES NEEDED={sample_tile["tiles"]} ({sample_tile["k_tiles"]} × {sample_tile["m_tiles"]})')
                            job.log(f'ACTIVATION={fmt}; OUTPUT={"fp32" if fp16 else "int32"}; X_NONZERO={sum(fp_compare.model.half_units(v) != 0 if fp16 else v != 0 for v in x)}/{k}; X_DISTINCT={len(set(x))}; W_DISTRIBUTION=' + json.dumps(dict(collections.Counter(w)), sort_keys=True))
                            ranges = tile_ranges(k, m, design, options)
                            for tile, (a, n, c, d) in enumerate(ranges, 1):
                                tx = x[a:a + n]
                                tw = [w[j * m + col] for j in range(a, a + n) for col in range(c, c + d)]
                                expected = reference(tx, tw, d, fmt) if options['reference'] == 'python' else None
                                job.update(current=label, tile=tile, tiles=len(ranges))
                                job.log(f'HW OPERATION {tile}/{len(ranges)} input_range={a}:{a+n} output_range={c}:{c+d}; 1x{n} @ {n}x{d}')
                                result = run_operation(job, sim, design, base, options, tx, tw, wb,
                                    temporary / 'operation', env, expected)
                                actual, expected_part = result.pop('actual', None), result.pop('expected', None)
                                result.update(k=n, m=d, activation=fmt, model_parameters=cycle_compare.parameters(
                                    n, d, wb, design['b'], design['rf'], physical=cycle_parameters))
                                if actual is not None and len(actual) == d:
                                    for col, value in enumerate(actual):
                                        if fp16: fp_parts[c + col].append(value)
                                        else: output[c + col] += value
                                else:
                                    output_complete = False
                                if not fp16 and expected_part is not None and len(expected_part) == d:
                                    for col, value in enumerate(expected_part): combined_ref[c + col] += value
                                failed |= result['status'] not in ('PASS', 'UNVERIFIED')
                                measured.append(result['cycles'])
                                rows.append(result)
                                job.log('HW RESULT ' + json.dumps(result, sort_keys=True))
                                job.update(operations_done=job.data['operations_done'] + 1)
                                if failed and options['stop_on_fail']: break
                            output_complete &= len(rows) == len(ranges)
                            if fp16:
                                output = [fp_compare.sum_words(parts) for parts in fp_parts]
                            elif options['reference'] == 'riscv': full_ref = combined_ref
                            if not fp16 and full_ref is not None and any(not -(1 << 31) <= v < (1 << 31) for v in full_ref): raise ValueError('Combined reference exceeds int32')
                            comparison = compare_outputs(output if output_complete else [], full_ref, options, fmt) if full_ref is not None else dict(matches=0, mismatches=[])
                            status = ('TIMEOUT' if any(r['status'] == 'TIMEOUT' for r in rows) else 'FAIL') if failed else 'UNVERIFIED' if options['reference'] == 'none' else 'PASS' if comparison['numeric_pass'] else 'FAIL'
                            result = dict(name=test['name'], design=design['name'], design_id=design['id'], k=k, m=m, wb=wb, repeat=repeat + 1,
                                status=status, elements=m, operations=len(rows), activation=fmt,
                                output_format='fp32' if fp16 else 'int32',
                                cycles=sum(v for v in measured if v is not None) if all(v is not None for v in measured) else None,
                                core_cycles=sum(r.get('core_cycles') or 0 for r in rows) if options['cycles'] else None,
                                operation_results=rows, **comparison)
                            if fp16:
                                result['reference_contract'] = 'Exact full Python dot, rounded once to FP32' if full_ref is not None else 'Comparison skipped'
                                result['assembly'] = 'Exact host sum of FP32 HW partials, rounded once to FP32; K tiling can differ from a full native dot' if any(a != 0 for a, _, _, _ in ranges) else 'Native result or concatenated output columns'
                                if options['reference'] == 'riscv':
                                    result['reference_contract'] += '; independent C reference also checked for each HW operation'
                            if fast:
                                add_cycle_estimate(result, logical_test, design)
                                job.log('CYCLE ESTIMATE: ' + json.dumps(dict(measured_tile_cycles=result['cycles'],
                                    tiles_needed=result['tiles_needed'], estimated_full_cycles=result['estimated_cycles'],
                                    full_result_verified=result['full_result_verified'], note=result['estimate_note'])))
                            with job.lock:
                                job.data['results'].append(result)
                                job.update(completed=job.data['completed'] + 1)
                            job.log('CASE RESULT: ' + json.dumps(result, sort_keys=True))
                            if result['status'] not in ('PASS', 'ESTIMATED', 'UNVERIFIED') and options['stop_on_fail']: return
                shutil.rmtree(sim.parent)
        finally:
            # Every make restores immediately; never overwrite an external edit.
            unchanged = all(Path(p).is_file() and hw.sha(p) == v for p, v in hashes.items())
            job.log('ORIGINAL HW/SW SOURCES UNCHANGED: ' + str(unchanged))
            if hw.CONFIG.read_bytes() != original:
                job.update(recovery=str(temporary / 'original-config.scala'))
                raise RuntimeError('Configuration changed externally; original snapshot retained')
            if not unchanged: raise RuntimeError('Sources changed during the suite')


def legacy_state(kind):
    stem = {'regression': 'lumax-regression-', 'test_cases': 'lumax-test-cases-',
            'llama_shapes': 'lumax-llama-shapes-', 'matrix_1024': 'lumax-1024-'}[kind]
    return Path('/tmp') / (stem + str(os.getuid()))


def legacy_counts(folder, kind):
    if kind in ('llama_shapes', 'matrix_1024'):
        if not folder.is_file(): return collections.Counter(), 0
        text = folder.read_text(errors='replace')
        rows = []
        for row in re.findall(r'^CASE RESULT: (.*)$', text, re.M):
            try: rows.append(json.loads(row)['status'])
            except (ValueError, KeyError): pass  # A worker may be writing the final line.
        return collections.Counter(rows), len(rows)
    rows = []
    for path in folder.glob('Log_*.log'):
        text = path.read_text(errors='replace')
        result = re.findall(r'^FINAL (?:RUN )?RESULT: (\w+)', text, re.M)
        if 'RECOVERED ORIGINAL RESULTS' in text:
            result = re.findall(r'^RUN \d+/54 .*: (PASS|FAIL) \d+/\d+', text, re.M)
        rows.extend(result)
    return collections.Counter(rows), len(rows)


def legacy_operations(folder, kind, completed):
    if kind == 'llama_shapes' and folder.is_file():
        return len(re.findall(r'^TILE RESULT .*?: \{.*\}$', folder.read_text(errors='replace'), re.M))
    if kind == 'test_cases' and (folder / 'Log_TC11.log').is_file():
        # Each TC11 boot contains six real HW multiplications.
        text = (folder / 'Log_TC11.log').read_text(errors='replace')
        return completed + 5 * len(re.findall(r'^FINAL (?:RUN )?RESULT: ', text, re.M))
    return completed


def run_builtin(job, env):
    suite, options = job.data['suite'], job.data['suite']['options']
    kind = suite['kind']
    for design in suite['designs']:
        while is_locked(hw.CONFIG):
            job.cancelled()
            job.update(status='WAITING', phase='Waiting for the current verification run')
            time.sleep(1)
        state = legacy_state(kind)
        if state.exists(): raise RuntimeError('Existing script state found: ' + str(state) + '; inspect its status first')
        job.update(status='RUNNING', current=design['name'], phase='Starting original suite script')
        args = [sys.executable, str(ROOT / BUILTINS[kind]['script']), 'start', '--design', design['id'],
                '--timeout', str(options['timeout']), '--build-timeout', str(options['build_timeout']), '--jobs', str(options['jobs'])]
        if kind in ('regression', 'test_cases'): args += ['--workers', str(options['workers'])]
        if kind == 'test_cases' and options['tcs']: args += ['--tc', *options['tcs']]
        if kind == 'llama_shapes': args += ['--t', str(options['t'])]
        result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=180)
        job.log(shlex.join(args) + '\n' + result.stdout + result.stderr)
        if result.returncode: raise RuntimeError('Original suite launcher failed')
        info = read_json(state / 'run.json', {})
        output = Path(info.get('log') or info.get('logdir') or '')
        job.update(phase='Original script running', linked_logs=job.data.get('linked_logs', []) + [str(output.relative_to(LOGS))])
        previous = 0
        previous_ops = 0
        while state.exists():
            if (job.folder / 'STOP').exists(): (state / 'stop').touch()
            tally, done = legacy_counts(output, kind)
            ops = legacy_operations(output, kind, done)
            if done != previous or ops != previous_ops:
                job.update(completed=job.data['completed'] + done - previous, counts=dict(tally),
                           operations_done=job.data['operations_done'] + ops - previous_ops)
                previous = done
                previous_ops = ops
            time.sleep(2)
        tally, done = legacy_counts(output, kind)
        job.update(completed=job.data['completed'] + done - previous,
                   operations_done=job.data['operations_done'] + legacy_operations(output, kind, done) - previous_ops)
        lines = output.read_text(errors='replace') if output.is_file() else '\n'.join(p.read_text(errors='replace')[-3000:] for p in output.glob('Log_*.log'))
        if (job.folder / 'STOP').exists(): raise Cancelled('Stopped by user')
        status = 'PASS' if done and tally['PASS'] == done and 'RUN COMPLETE' in lines else 'TIMEOUT' if tally['TIMEOUT'] else 'FAIL'
        row = dict(name=BUILTINS[kind]['name'], design=design['name'], status=status, counts=dict(tally), completed=done, log=str(output.relative_to(LOGS)))
        with job.lock:
            job.data['results'].append(row)
            job.update()
        job.log('SCRIPT RESULT: ' + json.dumps(row))


def execute_job(folder):
    job = Job(folder)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: (job.folder / 'STOP').touch())
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    with (job.folder / 'worker.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        thread = threading.Thread(target=job.heartbeat, daemon=True)
        thread.start()
        temporary = Path(tempfile.mkdtemp(prefix='lumax-gui-'))
        try:
            job.cancelled()
            job.update(status='STARTING', started=now(), pid=os.getpid(), phase='Loading Chipyard environment')
            job.log('LUMAX GUI VERIFICATION\nSTART: ' + now() + '\nSUITE: ' + json.dumps(job.data['suite'], sort_keys=True))
            job.log('ALL MATRIX MULTIPLICATIONS USE MAKE-BUILT VERILATOR AND RISC-V ROCC. Python does not simulate the hardware.')
            env = hw.environment()
            runtime = temporary / 'runtime'
            runtime.mkdir(mode=0o700)
            env['XDG_RUNTIME_DIR'] = str(runtime)
            env['SBT_OPTS'] = (env.get('SBT_OPTS') or f'-Dsbt.ivy.home={hw.CY}/.ivy2 -Dsbt.global.base={hw.CY}/.sbt -Dsbt.boot.directory={hw.CY}/.sbt/boot/') + ' -Dsbt.server.forcestart=true -Dsbt.server.autostart=false -Dsbt.supershell=false'
            if job.data['suite']['kind'] in LOCAL_SUITES: run_custom(job, temporary, env)
            else: run_builtin(job, env)
            rows = job.data['results']
            status = suite_status(rows)
            job.update(status=status, phase='Finished', ended=now(), process=None)
        except Cancelled as error:
            job.update(status='STOPPED', phase=str(error), ended=now(), process=None)
        except BaseException as error:
            job.log(f'ERROR: {type(error).__name__}: {error}')
            job.update(status='ERROR', phase=str(error), ended=now(), process=None)
        finally:
            job.ended.set()
            thread.join(timeout=5)
            if not job.data.get('recovery'): shutil.rmtree(temporary)
            job.log('END: ' + now() + '\nSTATUS: ' + job.data['status'])
    return 0 if job.data['status'] in ('PASS', 'ESTIMATED') else 1


def external_runs():
    result = []
    for kind in BUILTINS:
        state = legacy_state(kind)
        info = read_json(state / 'run.json', {})
        if not info or not is_locked(state / 'lock'): continue
        output = Path(info.get('log') or info.get('logdir') or '')
        if not output.exists(): continue
        counts, completed = legacy_counts(output, kind)
        try: log = str(output.relative_to(LOGS))
        except ValueError: log = None
        result.append(dict(name=BUILTINS[kind]['name'], kind=kind, completed=completed,
                           counts=dict(counts), active=True, log=log))
    return result


_HISTORY = {}


def history():
    result = []
    active = {r['log'] for r in external_runs()}
    for group in ('regression', 'test_cases', 'llama_shapes', 'matrix_1024'):
        for path in sorted((LOGS / group).glob('run_*'), reverse=True):
            files = [path] if path.is_file() else sorted(p for p in path.rglob('*')
                    if p.is_file() and p.suffix in ('.log', '.md', '.csv'))
            key = str(path)
            stamp = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files)
            if _HISTORY.get(key, (None,))[0] != stamp:
                counts, completed = legacy_counts(path, group)
                if group == 'regression' and (path / 'total_results.csv').exists():
                    import csv
                    with (path / 'total_results.csv').open() as f: rows = list(csv.DictReader(f))
                    counts, completed = collections.Counter(r['status'] for r in rows), len(rows)
                if group == 'llama_shapes' and path.is_file():
                    text = path.read_text(errors='replace')
                    tile_rows = re.findall(r'^TILE RESULT .*?: (\{.*\})$', text, re.M)
                    tile_count = collections.Counter()
                    for row in tile_rows:
                        try: tile_count[json.loads(row)['status']] += 1
                        except (ValueError, KeyError): pass
                else: tile_count = None
                entry = dict(id=key, name=BUILTINS[group]['name'], group=group,
                             path=str(path.relative_to(LOGS)), completed=completed, counts=dict(counts),
                             tiles=dict(tile_count) if tile_count else None,
                             modified=datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(),
                             files=[str(p.relative_to(LOGS)) for p in files])
                _HISTORY[key] = (stamp, entry)
            entry = dict(_HISTORY[key][1])
            entry['active'] = entry['path'] in active
            result.append(entry)
    return result
