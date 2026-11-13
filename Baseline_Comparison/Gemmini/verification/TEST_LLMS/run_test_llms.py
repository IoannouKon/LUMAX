#!/usr/bin/env python3
"""TEST_LLMS: edited Gemmini, make-built Verilator, FP16 x signed W8/W4/W2.

Qwen2.5-0.5B-inspired projections, K/N scaled by 1/4: hidden=224,
KV=32, intermediate=1216. Rows 4,5,8; 12 shapes x 3 precisions = 36.
These are independent matmuls, not an execution of the full language model.

Run: python3 run_test_llms.py check | start | status | stop
Each run saves dDIM_spSP_accACC_idIDS/runs/run_<date>/verilator.log and cycles.csv. start detaches
from SSH; all shape/configuration options are editable below. Hardware builds
are reused only when source/configuration/tool hashes match. Python numerical
references stay on the host; actual matrix arithmetic runs in Gemmini RTL.

No total wall-clock cutoff. Per-build/per-case limits detect stalled commands.
Numerical checking uses the same predeclared FP32 accumulation error bound as
Gemmini TEST_SAFE; tolerances are never relaxed after observing results.
"""

# -------------------- EDIT GEMMINI CONFIGURATION HERE --------------------
CHIPYARD = '/home/kioannou/chipyard_1.13.0'
# Read the exact edited source/software snapshot validated by Gemmini TEST_SAFE.
# These sources are snapshotted privately per run, never edited here.
GEMMINI_ASSETS = None  # None = this archive's Gemmini directory; or set an explicit path.
TILE_ROWS = 1
TILE_COLUMNS = 1
MESH_ROWS = 4
MESH_COLUMNS = 4                 # Total array = (tile_rows*mesh_rows) squared
SCRATCHPAD_KIB = 32
ACCUMULATOR_KIB = 4
SCRATCHPAD_BANKS = 4
DMA_IDS = 8                      # PER read/write pool; LUMAX has 8 SHARED IDs
DMA_BUS_BITS = 64
DMA_MAX_BYTES = 64               # Native max transaction; LUMAX requests 8 B
TILE_LATENCY = 2
DISABLE_SCALING = True           # Remove input/accumulator/output scale units
DISABLE_BIAS_INPUT = True        # Hardcode the WS initial addend to zero
WEIGHT_BITS = (8, 4, 2)          # Three separate statically elaborated designs

# Qwen projection families at 1/4 K/N scale; X[R,K] x W[K,N].
# Native Gemmini scratchpad tiling is allowed; this is not a tiled cycle estimate.
MATRIX_CASES = [
    (4, 224, 224), (4, 224, 32), (4, 224, 1216), (4, 1216, 224),
    (5, 224, 224), (5, 224, 32), (5, 224, 1216), (5, 1216, 224),
    (8, 224, 224), (8, 224, 32), (8, 224, 1216), (8, 1216, 224),
]
# New random operands: seeds and hashes are saved for future LUMAX replay.
REUSE_LUMAX_OPERANDS = False
LUMAX_SEEDS_CSV = '../../../../verification/TEST_SAFE/cycles.csv'  # Used only if replay is enabled.
BASE_SEED = None                  # Only used without replay; None = OS entropy
MAKE_JOBS = 2                     # Shared server: limit concurrent compilation
BUILD_TIMEOUT_SECONDS = 3600
SIM_TIMEOUT_SECONDS = 7200        # Per independent matrix/ELF
SUITE_TIMEOUT_HOURS = None       # No overall deadline, including no 3-hour cutoff.
MAX_SIM_CYCLES = 200_000_000

# LUMAX rounds an exact wide sum once; Gemmini rounds FP32 partial additions.
# Default: independent exact-real dot product + a declared FP32 forward bound.
# No threshold is fitted or increased after observing HW output.
NUMERICAL_RULE = 'fp32_bound'      # Or 'fixed_tolerance'
ABS_TOLERANCE = 1e-4              # Used ONLY with fixed_tolerance
REL_TOLERANCE = 1e-5
# -----------------------------------------------------------------------

import argparse
from collections import Counter
import csv
from datetime import datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import secrets
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import traceback

sys.dont_write_bytecode = True
SELF = Path(__file__).resolve()
ROOT = SELF.parent
ASSETS = ROOT.parent.parent if GEMMINI_ASSETS is None else Path(GEMMINI_ASSETS).expanduser().resolve()
STATE_ROOT = Path('/tmp') / ('gemmini-test-llms-' + str(os.getuid()) + '-' +
                            hashlib.sha256(str(ROOT).encode()).hexdigest()[:10])
CACHE_ROOT = Path.home() / '.cache' / 'gemmini-test-safe'
STOP = False
STAGE_NAMES = ('load_only', 'store_only', 'execute_only', 'load_store',
               'load_execute', 'store_execute', 'load_store_execute', 'loop_busy')


def now():
    return datetime.now().astimezone().isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def atomic(path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


def environment(cy):
    env = os.environ.copy()
    env['PATH'] = ':'.join(str(cy / p) for p in (
        '.conda-env/bin', '.conda-env/riscv-tools/bin', '.conda-env/lib/jvm/bin')) + ':' + env['PATH']
    env['RISCV'] = str(cy / '.conda-env/riscv-tools')
    env['JAVA_HOME'] = str(cy / '.conda-env/lib/jvm')
    env['CHISEL_FIRTOOL_PATH'] = str(cy / '.conda-env/riscv-tools/bin')
    env['VERILATOR_BIN'] = str(cy / '.conda-env/bin/verilator_bin')
    env.pop('VERILATOR_ROOT', None)
    return env


def settings():
    cfg = dict(chipyard=str(Path(CHIPYARD).expanduser().resolve()),
        tile_rows=TILE_ROWS, tile_columns=TILE_COLUMNS,
        mesh_rows=MESH_ROWS, mesh_columns=MESH_COLUMNS,
        sp_kib=SCRATCHPAD_KIB, acc_kib=ACCUMULATOR_KIB, sp_banks=SCRATCHPAD_BANKS,
        dma_ids=DMA_IDS, dma_bits=DMA_BUS_BITS, dma_maxbytes=DMA_MAX_BYTES,
        tile_latency=TILE_LATENCY, disable_scaling=DISABLE_SCALING,
        disable_bias=DISABLE_BIAS_INPUT, weights=list(WEIGHT_BITS),
        shapes=[list(s) for s in MATRIX_CASES], jobs=MAKE_JOBS,
        build_timeout=BUILD_TIMEOUT_SECONDS, sim_timeout=SIM_TIMEOUT_SECONDS,
        suite_hours=SUITE_TIMEOUT_HOURS, max_cycles=MAX_SIM_CYCLES,
        numerical_rule=NUMERICAL_RULE, atol=ABS_TOLERANCE, rtol=REL_TOLERANCE,
        replay=REUSE_LUMAX_OPERANDS)
    dim = TILE_ROWS * MESH_ROWS
    positive = (TILE_ROWS, TILE_COLUMNS, MESH_ROWS, MESH_COLUMNS,
                SCRATCHPAD_KIB, ACCUMULATOR_KIB, DMA_IDS, DMA_MAX_BYTES, MAKE_JOBS)
    if min(positive) < 1 or dim != TILE_COLUMNS * MESH_COLUMNS or dim < 2 or dim & (dim-1):
        raise ValueError('Gemmini needs a square, power-of-two array with DIM >= 2')
    if SCRATCHPAD_BANKS < 2 or SCRATCHPAD_BANKS & (SCRATCHPAD_BANKS-1):
        raise ValueError('Use a power-of-two scratchpad bank count >= 2')
    if not WEIGHT_BITS or len(set(WEIGHT_BITS)) != len(WEIGHT_BITS) or set(WEIGHT_BITS)-{2,4,8}:
        raise ValueError('Select unique weight widths from 8, 4 and 2')
    if any(dim*w % 8 for w in WEIGHT_BITS):
        raise ValueError('Each full packed row must contain whole bytes (W2 needs DIM >= 4)')
    if DMA_BUS_BITS not in (64, 128) or DMA_MAX_BYTES < DMA_BUS_BITS//8 or DMA_MAX_BYTES & (DMA_MAX_BYTES-1):
        raise ValueError('DMA max bytes must be a power of two >= bus beat bytes')
    if DMA_MAX_BYTES < dim*2:
        raise ValueError('Use DMA_MAX_BYTES >= one unpacked activation row (DIM*2)')
    if not MATRIX_CASES or any(len(s) != 3 or min(s) < 1 for s in MATRIX_CASES):
        raise ValueError('Every shape needs positive R,K,N')
    if max(k for _,k,_ in MATRIX_CASES) > 65536:
        raise ValueError('This suite bounds K to 65536 for its finite FP32 reference')
    if min(BUILD_TIMEOUT_SECONDS, SIM_TIMEOUT_SECONDS, MAX_SIM_CYCLES) <= 0 or (SUITE_TIMEOUT_HOURS is not None and SUITE_TIMEOUT_HOURS <= 0):
        raise ValueError('Timeouts/cycle limit must be positive')
    if TILE_LATENCY < 0 or NUMERICAL_RULE not in ('fp32_bound', 'fixed_tolerance'):
        raise ValueError('Invalid latency or numerical rule')
    if not all(math.isfinite(t) and t >= 0 for t in (ABS_TOLERANCE, REL_TOLERANCE)):
        raise ValueError('Tolerances must be finite and nonnegative')
    cfg['dim'] = dim
    return cfg


def make_plan(cfg):
    replay = {}
    seed_path = (ROOT / LUMAX_SEEDS_CSV).resolve()
    if cfg['replay']:
        with seed_path.open(newline='') as f:
            for row in csv.DictReader(f):
                if row['mode'] != 'fp16':
                    continue
                key = tuple(int(row[k]) for k in ('r', 'k', 'm', 'wb'))
                if key in replay:
                    raise ValueError('Duplicate shape/precision in LUMAX seed CSV')
                replay[key] = row
        cfg['seed_csv'] = str(seed_path)
        cfg['seed_csv_sha256'] = sha(seed_path)
    base = BASE_SEED if BASE_SEED is not None else secrets.randbits(64)
    plan = []
    for w in cfg['weights']:
        for index, (r,k,n) in enumerate(cfg['shapes'], 1):
            item = dict(case=f'LLM{index:02d}', wb=w, r=r, k=k, n=n, status='PENDING')
            if cfg['replay']:
                original = replay.get((r,k,n,w))
                if original is None:
                    raise ValueError(f'No saved LUMAX seeds for {(r,k,n,w)}; set REUSE_LUMAX_OPERANDS=False for new shapes')
                item.update(seed_x=int(original['seed_x']), seed_w=int(original['seed_w']),
                    expected_x_hash=original['x_sha256'], expected_w_hash=original['w_sha256'])
            else:
                for label in ('x', 'w'):
                    item['seed_'+label] = int.from_bytes(hashlib.sha256(
                        f'{base}:{w}:{index}:{label}'.encode()).digest()[:8], 'little')
            plan.append(item)
    return plan


def operands(item):
    rx, rw = random.Random(item['seed_x']), random.Random(item['seed_w'])
    x = [rx.randrange(65536) for _ in range(item['r']*item['k'])]
    x = [v ^ 0x400 if v & 0x7c00 == 0x7c00 else v for v in x]
    lo, hi = -(1 << (item['wb']-1)), (1 << (item['wb']-1))-1
    w = [rw.randint(lo,hi) for _ in range(item['k']*item['n'])]
    if not any(v & 0x7fff for v in x):
        x[0] = 0x3c00
    if not any(w):
        w[0] = -1
    for label, data in [('x',x), ('w',w)]:
        digest = hashlib.sha256(json.dumps(data).encode()).hexdigest()
        if item.get('expected_'+label+'_hash', digest) != digest:
            raise ValueError('Saved operand hash mismatch: '+label)
    row_bytes = (item['n']*item['wb']+7)//8
    packed = bytearray(item['k']*row_bytes)
    for i in range(item['k']):
        for j in range(item['n']):
            packed[i*row_bytes+j*item['wb']//8] |= (w[i*item['n']+j] & ((1 << item['wb'])-1)) << (j*item['wb'] % 8)
    return x,w,packed


def half_units(raw):
    exponent, fraction = (raw >> 10) & 31, raw & 1023
    assert exponent != 31
    value = ((1024+fraction) << (exponent-1)) if exponent else fraction
    return -value if raw & 0x8000 else value


def round_fp32(units):
    if units == 0:
        return 0
    sign, value = (0x80000000 if units < 0 else 0), abs(units)
    exponent = value.bit_length()-1-24
    shift = value.bit_length()-24
    if shift > 0:
        q, r = divmod(value, 1 << shift)
        q += r*2 > (1 << shift) or (r*2 == (1 << shift) and q & 1)
    else:
        q = value << -shift
    if q == 1 << 24:
        q >>= 1
        exponent += 1
    return sign | ((exponent+127) << 23) | (q & 0x7fffff)


def fp32(word):
    return struct.unpack('<f', struct.pack('<I', word))[0]


def errors(item, x, w, actual, cfg):
    units = [half_units(v) for v in x]
    output = []
    # At most K PE additions plus ceil(K/DIM) accumulator additions per dot.
    # 2*K+2 is conservative; adding the padded zeros introduces no error.
    additions = 2*item['k']+2
    for i in range(item['r']):
        for j in range(item['n']):
            terms = [units[i*item['k']+k]*w[k*item['n']+j] for k in range(item['k'])]
            exact_units, abs_units = sum(terms), sum(abs(t) for t in terms)
            expected_bits = round_fp32(exact_units)
            reference = exact_units / (1 << 24)
            index = i*item['n']+j
            bits = actual[index]
            value = fp32(bits)
            finite = math.isfinite(value)
            if finite:
                numerator, denominator = value.as_integer_ratio()
                error_numerator = abs(numerator*(1 << 24)-exact_units*denominator)
                error_denominator = denominator*(1 << 24)
                error = error_numerator/error_denominator
            else:
                error = None
            rounded_error = abs(value-fp32(expected_bits)) if finite else None
            bound_numerator = additions*abs_units
            bound_denominator = ((1 << 24)-additions)*(1 << 24)
            bound = math.nextafter(bound_numerator/bound_denominator, math.inf) if abs_units else 0.0
            within_bound = finite and error_numerator*bound_denominator <= bound_numerator*error_denominator
            fixed_limit = cfg['atol']+cfg['rtol']*abs(reference)
            limit = bound if cfg['numerical_rule']=='fp32_bound' else fixed_limit
            def order(v):
                return 0x80000000-(v & 0x7fffffff) if v & 0x80000000 else 0x80000000+v
            output.append(dict(row=i, column=j, hw_bits=f'{bits:08x}', hw=value if finite else None,
                python_fp32_bits=f'{expected_bits:08x}', python_fp32=fp32(expected_bits),
                exact_real=reference, exact_units_2pow_minus24=exact_units,
                abs_error_vs_real=error, abs_error_vs_rounded=rounded_error,
                relative_error=error/abs(reference) if finite and reference else (0.0 if error==0 else None),
                ulp_error=abs(order(bits)-order(expected_bits)) if finite else None,
                fp32_forward_bound=bound, fixed_tolerance_limit=fixed_limit,
                selected_limit=limit, exact_match=bits==expected_bits,
                within_fixed_tolerance=finite and error <= fixed_limit,
                within_fp32_bound=within_bound,
                pass_value=within_bound if cfg['numerical_rule']=='fp32_bound' else finite and error <= limit))
    return output


def scala_config(cfg):
    boolean = lambda v: str(bool(v)).lower()
    scale = ('mvin_scale_args = None, mvin_scale_acc_args = None, acc_scale_args = None,'
             if cfg['disable_scaling'] else '')
    source = f'''package gemmini.testsafe
import gemmini._
import gemmini.Arithmetic.FloatArithmetic._
import org.chipsalliance.cde.config.{{Config, Parameters}}
import freechips.rocketchip.diplomacy.{{LazyModule, ValName}}
import freechips.rocketchip.tile.BuildRoCC

class WithTestSafeGemmini(w: Int) extends Config((site, here, up) => {{
  case BuildRoCC => Seq((p: Parameters) => {{
    implicit val q = p
    implicit val v = implicitly[ValName]
    val cfg: GemminiArrayConfig[gemmini.Float, gemmini.Float, gemmini.Float] = GemminiFPConfigs.fpIntConfig(true,w).copy(
      tileRows = {cfg['tile_rows']}, tileColumns = {cfg['tile_columns']},
      meshRows = {cfg['mesh_rows']}, meshColumns = {cfg['mesh_columns']},
      sp_capacity = CapacityInKilobytes({cfg['sp_kib']}),
      acc_capacity = CapacityInKilobytes({cfg['acc_kib']}),
      sp_banks = {cfg['sp_banks']}, acc_banks = 1,
      max_in_flight_mem_reqs = {cfg['dma_ids']},
      dma_buswidth = {cfg['dma_bits']}, dma_maxbytes = {cfg['dma_maxbytes']},
      tile_latency = {cfg['tile_latency']},
      hardcode_d_to_garbage_addr = {boolean(cfg['disable_bias'])},
      {scale}
      acc_read_small_width = false, acc_read_full_width = true,
      has_training_convs = false, has_dw_convs = false,
      has_max_pool = false, has_nonlinear_activations = false,
      has_normalizations = false, num_counter = 8)
    LazyModule(new Gemmini(cfg))
  }})
}})
'''
    for w in cfg['weights']:
        source += f'class GemminiSafeW{w}Config extends Config(new WithTestSafeGemmini({w}) ++ new chipyard.RocketConfig)\n'
    return source


def preflight(cfg):
    cy = Path(cfg['chipyard'])
    required = [ASSETS/'fpint/src/main/scala/gemmini/FPIntMac.scala', ASSETS/'software/gemmini_fpint.h',
        ASSETS/'software/rocc-software/src/xcustom.h', cy/'.classpath_cache/chipyard.jar',
        cy/'.classpath_cache/tapeout.jar', cy/'.conda-env/lib/jvm/bin/java',
        cy/'.conda-env/lib/jvm/bin/jar', cy/'.conda-env/riscv-tools/bin/riscv64-unknown-elf-gcc',
        cy/'.conda-env/bin/verilator', cy/'.conda-env/bin/verilator_bin',
        cy/'.conda-env/riscv-tools/bin/firtool', cy/'.conda-env/bin/dtc']
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        raise ValueError('Missing prerequisites:\n'+'\n'.join(missing))
    cache = Path.home()/'.cache/coursier'
    jars = {}
    for name in ('scala-compiler-2.13.12.jar', 'scala-reflect-2.13.12.jar', 'chisel-plugin_2.13.12-6.5.0.jar'):
        found = sorted(cache.rglob(name))
        if not found:
            raise ValueError('Missing cached compiler dependency: '+name)
        jars[name] = str(found[0])
    return jars


class Stopped(Exception):
    pass


class CommandFailure(Exception):
    def __init__(self, phase, code, timeout=False):
        super().__init__(f'{phase}: '+('TIMEOUT' if timeout else f'exit={code}'))
        self.code, self.timeout = code, timeout


class Runner:
    def __init__(self, state):
        self.state = state
        self.options = json.loads((state/'options.json').read_text())
        self.cfg = self.options['config']
        self.records = self.options['plan']
        self.cy = Path(self.cfg['chipyard'])
        self.env = environment(self.cy)
        self.phase = 'starting'
        self.current = ''
        self.begin = time.monotonic()
        self.log_path = Path(self.options['log'])

    def log(self, text):
        print(f'[{now()}] {text}', flush=True)

    def checkpoint(self, status='RUNNING', reason=''):
        atomic(self.state/'status.json', dict(status=status, phase=self.phase, current=self.current,
            completed=sum(r['status']!='PENDING' for r in self.records), total=len(self.records),
            results=dict(Counter(r['status'] for r in self.records)), log=str(self.log_path),
            pid=os.getpid(), seconds=round(time.monotonic()-self.begin,1), reason=reason,
            records=self.records))
        self.write_cycles()

    def write_cycles(self):
        fields = ('case','mode','wb','r','k','m','n','status','reason',
            'core_cycles','controller_active_cycles','load_busy_cycles',
            'store_busy_cycles','execute_busy_cycles',*STAGE_NAMES,
            'exact_matches','elements','guards','max_absolute_error','max_ulp_error',
            'fixed_tolerance_failures','seed_x','seed_w','x_sha256','w_sha256',
            'packed_sha256','simulator_sha256','elf_sha256','test_source_sha256',
            'seconds','array_dim','dma_read_ids','dma_write_ids','dma_bus_bits',
            'dma_maxbytes','scratchpad_kib','accumulator_kib','numerical_rule')
        path = self.log_path.parent/'cycles.csv'
        temporary = path.with_suffix('.csv.tmp')
        with temporary.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
            writer.writeheader()
            for record in self.records:
                row = dict(record)
                row.update(record.get('counters',{}))
                row.update(mode='fp16',m=record['n'],array_dim=self.cfg['dim'],
                    dma_read_ids=self.cfg['dma_ids'],dma_write_ids=self.cfg['dma_ids'],
                    dma_bus_bits=self.cfg['dma_bits'],dma_maxbytes=self.cfg['dma_maxbytes'],
                    scratchpad_kib=self.cfg['sp_kib'],accumulator_kib=self.cfg['acc_kib'],
                    numerical_rule=self.cfg['numerical_rule'])
                writer.writerow(row)
        temporary.replace(path)

    def guard(self):
        if STOP or (self.state/'stop.request').exists():
            raise Stopped('Stop requested')
        if self.cfg['suite_hours'] is not None and time.monotonic()-self.begin > self.cfg['suite_hours']*3600:
            raise Stopped('Suite safety time limit reached')

    def command(self, cmd, cwd, timeout, phase, binary=False):
        self.guard()
        self.phase = phase
        self.checkpoint()
        self.log('COMMAND '+shlex.join([str(v) for v in cmd]))
        path = self.state/'command-output.tmp'
        start = time.monotonic()
        expired = False
        with path.open('wb') as out:
            proc = subprocess.Popen([str(v) for v in cmd], cwd=cwd, env=self.env,
                stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                start_new_session=True)
            try:
                while proc.poll() is None:
                    self.guard()
                    if time.monotonic()-start > timeout:
                        expired = True
                        raise CommandFailure(phase, -1, True)
                    time.sleep(.25)
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                out.flush()
                raw = path.read_bytes()
                if not binary:
                    self.log('ACTUAL OUTPUT\n'+raw.decode(errors='replace'))
                elif expired or STOP or (self.state/'stop.request').exists():
                    self.log('INCOMPLETE SIMULATOR OUTPUT '+repr(raw))
                self.log(f'COMMAND EXIT={proc.returncode}; seconds={time.monotonic()-start:.3f}')
                path.unlink(missing_ok=True)
        if proc.returncode:
            if binary:
                self.log('FAILED SIMULATOR OUTPUT '+repr(raw))
            raise CommandFailure(phase, proc.returncode)
        return raw

    def tool_info(self):
        info = {}
        for name,args in [('verilator',['verilator','--version']), ('firtool',['firtool','--version']),
                          ('gcc',['riscv64-unknown-elf-gcc','--version'])]:
            p = subprocess.run(args,env=self.env,capture_output=True,text=True,timeout=15)
            if p.returncode:
                raise RuntimeError('Cannot run '+name+': '+p.stderr)
            info[name] = p.stdout.strip()
        return info

    def build_overlay(self):
        cfg_source = scala_config(self.cfg)
        snapshot = self.state/'source'
        sources = sorted(snapshot.rglob('*.scala'))
        assembly = self.cy/'.classpath_cache/chipyard.jar'
        info = self.tool_info()
        h = hashlib.sha256(b'gemmini-test-safe-build-v1')
        for p in sources:
            h.update(str(p.relative_to(snapshot)).encode()); h.update(p.read_bytes())
        h.update(cfg_source.encode()); h.update(sha(assembly).encode())
        h.update(sha(self.cy/'.classpath_cache/tapeout.jar').encode())
        h.update(json.dumps(info,sort_keys=True).encode())
        self.cache = CACHE_ROOT/h.hexdigest()[:24]
        self.cache.mkdir(parents=True,exist_ok=True)
        self.phase = 'cache lock'; self.checkpoint()
        self.cache_lock = (self.cache/'build.lock').open('a+')
        while True:
            self.guard()
            try:
                fcntl.flock(self.cache_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(.5)
        self.log('TOOLS '+json.dumps(info))
        self.log('ELABORATION CONFIGURATION\n'+cfg_source)
        self.log('CACHE '+str(self.cache)+'; BASE_ASSEMBLY_SHA256='+sha(assembly))
        self.overlay = self.cache/'chipyard-gemmini-safe.jar'
        marker = self.cache/'overlay.json'
        if marker.exists() and self.overlay.is_file() and json.loads(marker.read_text()).get('sha256')==sha(self.overlay):
            self.log('REUSE compiled source overlay')
            return
        classes = self.cache/'classes'
        if classes.exists():
            shutil.rmtree(classes)
        classes.mkdir()
        extra = self.cache/'GemminiTestSafeConfig.scala'; extra.write_text(cfg_source)
        j = self.options['compiler_jars']
        cp = ':'.join([j['scala-compiler-2.13.12.jar'],j['scala-reflect-2.13.12.jar'],str(assembly)])
        java = self.cy/'.conda-env/lib/jvm/bin/java'
        self.command([java,'-Xmx5G','-cp',cp,'scala.tools.nsc.Main','-classpath',assembly,
            '-Xplugin:'+j['chisel-plugin_2.13.12-6.5.0.jar'],'-language:reflectiveCalls',
            '-d',classes,*sources,extra],self.cache,self.cfg['build_timeout'],'Scala compilation')
        shutil.copyfile(assembly,self.overlay)
        self.command([self.cy/'.conda-env/lib/jvm/bin/jar','uf',self.overlay,'-C',classes,'.'],
                     self.cache,120,'private overlay jar')
        atomic(marker,dict(sha256=sha(self.overlay)))

    def build(self, wb):
        name = f'GemminiSafeW{wb}Config'
        long_name = 'chipyard.harness.TestHarness.'+name
        out = self.cache/f'w{wb}'
        gen = out/'generated-src'/long_name
        sim = out/('simulator-chipyard.harness-'+name)
        params = gen/f'gemmini_params_fpint_w{wb}.h'
        marker = out/'success.json'
        self.current = f'W{wb} BUILD'
        if marker.exists() and sim.is_file() and params.is_file():
            saved = json.loads(marker.read_text())
            if saved.get('simulator_sha256')==sha(sim) and saved.get('params_sha256')==sha(params):
                self.log(f'REUSE W{wb} simulator '+sha(sim))
                return sim,params
        gen.mkdir(parents=True,exist_ok=True)
        for arch in (32,64):
            shutil.copyfile(self.cy/f'generators/testchipip/src/main/resources/testchipip/bootrom/bootrom.rv{arch}.img',gen/f'bootrom.rv{arch}.img')
        self.command([self.cy/'.conda-env/lib/jvm/bin/java','-Xmx5G','-Xss8M','-cp',self.overlay,
            'chipyard.Generator','--target-dir',gen,'--name',long_name,
            '--top-module','chipyard.harness.TestHarness','--legacy-configs','gemmini.testsafe:'+name],
            gen,self.cfg['build_timeout'],f'W{wb} Chisel elaboration')
        # Preserve Make's expected generator timestamp without rerunning it in
        # Chipyard's working source tree. All lowering/C++ compilation is real.
        (gen/(long_name+'.chisel.log')).touch()
        self.command(['make','-C',self.cy/'sims/verilator','CONFIG='+name,
            'CONFIG_PACKAGE=gemmini.testsafe','sim_dir='+str(out),'build_dir='+str(gen),
            'GENERATOR_CLASSPATH='+str(self.overlay),'-o',self.overlay,
            '-o',self.cy/'.classpath_cache/tapeout.jar',
            '-o',gen/(long_name+'.fir'),'-o',gen/(long_name+'.anno.json'),
            '-o',gen/(long_name+'.chisel.log'),'-j'+str(self.cfg['jobs'])],
            out,self.cfg['build_timeout'],f'W{wb} make Verilator')
        if not sim.is_file() or not params.is_file():
            raise RuntimeError('Build did not produce simulator and matching parameters')
        atomic(marker,dict(simulator_sha256=sha(sim),params_sha256=sha(params)))
        return sim,params

    def test_source(self,item,x,packed):
        def array(kind,name,values):
            return f'static const {kind} {name}[] __attribute__((aligned(64))) = {{'+','.join(map(str,values))+'};\n'
        r,k,n = (item[t] for t in ('r','k','n'))
        return '''#include <stdio.h>
#include <unistd.h>
#include "gemmini_fpint.h"
static unsigned long ticks(void) { unsigned long v; asm volatile("rdcycle %0":"=r"(v)::"memory"); return v; }
static uint32_t counter(uint32_t cmd) {
  uint64_t value; ROCC_INSTRUCTION_R_R_R(XCUSTOM_ACC,value,cmd,0,126);
  return (uint32_t)value;
}
'''+array('uint16_t','x',x)+array('uint8_t','w',packed)+f'''
static uint32_t out[{r*n+32}] __attribute__((aligned(64)));
int main(void) {{
  gemmini_fpint_flush();
  for(unsigned i=0;i<{r*n+32};i++) out[i]=0xa5a5a5a5u;
  for(unsigned i=0;i<8;i++) counter((i<<4)|8|((i<7?i+1:43)<<12));
  counter(1);
  asm volatile("fence":::"memory");
  unsigned long start=ticks();
  gemmini_fpint_matmul({r},{k},{n},x,w,out+16);
  unsigned long cycles=ticks()-start;
  counter(4);
  uint32_t pc[8]; for(unsigned i=0;i<8;i++) pc[i]=counter(i<<4);
  unsigned guard=1;
  for(unsigned i=0;i<16;i++) if(out[i]!=0xa5a5a5a5u || out[{r*n}+16+i]!=0xa5a5a5a5u) guard=0;
  printf("CORE_CYCLES %lu\\n",cycles);
  printf("COUNTERS %u %u %u %u %u %u %u %u\\n",pc[0],pc[1],pc[2],pc[3],pc[4],pc[5],pc[6],pc[7]);
  printf("OUTPUT_BITS {r*n}\\n");
  write(1,out+16,{r*n}*sizeof(uint32_t)); write(1,"\\n",1);
  printf("TEST_DONE %u\\n",guard);
  return guard ? 0 : 3;
}}
'''

    def execute(self,item,sim,params):
        self.current = f"W{item['wb']} {item['case']} {item['r']}x{item['k']}x{item['n']}"
        self.checkpoint()
        start = time.monotonic()
        with tempfile.TemporaryDirectory(prefix='matrix-',dir=self.state) as directory:
            work = Path(directory)
            x,w,packed = operands(item)
            item.update(x_sha256=hashlib.sha256(json.dumps(x).encode()).hexdigest(),
                w_sha256=hashlib.sha256(json.dumps(w).encode()).hexdigest(),
                packed_sha256=hashlib.sha256(packed).hexdigest(),simulator_sha256=sha(sim))
            self.log('CASE '+json.dumps(item))
            self.log('RANDOM STATISTICS '+json.dumps(dict(x_nonzero=sum(bool(v&0x7fff) for v in x),
                x_distinct=len(set(x)),w_nonzero=sum(v!=0 for v in w),w_distinct=len(set(w)),
                packed_weight_bytes=len(packed))))
            inc = work/'include'; inc.mkdir()
            shutil.copyfile(params,inc/'gemmini_params.h')
            shutil.copyfile(self.state/'gemmini_fpint.h',work/'gemmini_fpint.h')
            rocc = work/'rocc-software/src';rocc.mkdir(parents=True)
            shutil.copyfile(self.state/'xcustom.h',rocc/'xcustom.h')
            source = work/'test.c';source.write_text(self.test_source(item,x,packed))
            item['test_source_sha256'] = sha(source)
            gcc = self.cy/'.conda-env/riscv-tools/bin/riscv64-unknown-elf-gcc'
            self.command([gcc,'-O2','-fno-common','-fno-builtin-printf','-DBAREMETAL=1',
                '-specs=htif_nano.specs','-I'+str(work),'-c',source,'-o',work/'test.o'],
                work,180,'RISC-V compile')
            elf = work/'test.riscv'
            self.command([gcc,'-static','-specs=htif_nano.specs',work/'test.o','-lm','-o',elf],
                         work,120,'RISC-V link')
            item['elf_sha256'] = sha(elf)
            self.log('ELF '+json.dumps({key:item[key] for key in ('elf_sha256','test_source_sha256','simulator_sha256')}))
            raw = self.command([sim,'+permissive','+max-cycles='+str(self.cfg['max_cycles']),
                '+loadmem='+str(elf),'+permissive-off',elf],work,self.cfg['sim_timeout'],
                'ACTUAL VERILATOR '+self.current,binary=True)
            marker = re.search(rb'(?m)^OUTPUT_BITS (\d+)\n',raw)
            if not marker or int(marker[1]) != item['r']*item['n']:
                raise RuntimeError('Missing/malformed HW output record: '+repr(raw[-2048:]))
            begin,end = marker.end(),marker.end()+int(marker[1])*4
            if end > len(raw):
                raise RuntimeError('Truncated HW output payload')
            actual = list(struct.unpack('<'+'I'*int(marker[1]),raw[begin:end]))
            text = (raw[:marker.start()] + ('HW_BITS32 '+ ' '.join(f'{v:08x}' for v in actual)).encode()+raw[end:]).decode(errors='replace')
            self.log('ACTUAL VERILATOR OUTPUT\n'+text)
            core = re.findall(r'^CORE_CYCLES (\d+)$',text,re.M)
            pcs = re.findall(r'^COUNTERS ((?:\d+ ){7}\d+)$',text,re.M)
            done = re.findall(r'^TEST_DONE ([01])$',text,re.M)
            if len(core)!=1 or len(pcs)!=1 or done!=['1']:
                raise RuntimeError('Incomplete UART or failed output guards')
            values = errors(item,x,w,actual,self.cfg)
            pc = list(map(int,pcs[0].split()))
            if int(core[0]) <= 0 or sum(pc[:7]) <= 0:
                raise RuntimeError('Missing/zero cycle measurements')
            exact = sum(v['exact_match'] for v in values)
            passed = all(v['pass_value'] for v in values)
            for v in values:
                self.log('VALUE '+json.dumps(dict(case=item['case'],wb=item['wb'],**v),allow_nan=False))
            item.update(status='PASS' if passed else 'FAIL',core_cycles=int(core[0]),
                controller_active_cycles=sum(pc[:7]),counters=dict(zip(STAGE_NAMES,pc)),
                load_busy_cycles=pc[0]+pc[3]+pc[4]+pc[6],
                store_busy_cycles=pc[1]+pc[3]+pc[5]+pc[6],
                execute_busy_cycles=pc[2]+pc[4]+pc[5]+pc[6],
                exact_matches=exact,elements=len(values),guards='PASS',
                max_absolute_error=max(v['abs_error_vs_real'] for v in values) if all(v['hw'] is not None for v in values) else None,
                max_ulp_error=max(v['ulp_error'] for v in values) if all(v['hw'] is not None for v in values) else None,
                fixed_tolerance_failures=sum(not v['within_fixed_tolerance'] for v in values),
                seconds=round(time.monotonic()-start,3))
            self.log('RESULT '+json.dumps(item))
            self.checkpoint()

    def run(self):
        self.log('GEMMINI TEST_LLMS START '+json.dumps(self.cfg,sort_keys=True))
        self.log('FROZEN SOURCE PROVENANCE '+json.dumps({k:self.options[k] for k in ('runner_sha256','assets','source_sha256','software_sha256')}))
        self.log('FP16 x signed integers; static W8/W4/W2. No scaling/bias in the workload.')
        self.log('LUMAX shared IDs vs Gemmini separate read/write pools; transaction sizes differ. Log these differences when comparing.')
        self.log('Numerical rule fixed before execution: '+self.cfg['numerical_rule']+
            '; forward bound gamma_(2*K+2)*sum(abs(X*W)), u=2^-24; fixed atol/rtol='+
            str((self.cfg['atol'],self.cfg['rtol'])))
        failure = ''
        stopped = False
        try:
            self.build_overlay()
            for wb in self.cfg['weights']:
                self.guard()
                selected = [r for r in self.records if r['wb']==wb]
                try:
                    sim,params = self.build(wb)
                except (CommandFailure,RuntimeError,OSError) as exc:
                    for r in selected:
                        r.update(status='BUILD_ERROR',reason=str(exc))
                    self.log('BUILD_ERROR W'+str(wb)+' '+str(exc));self.checkpoint()
                    continue
                for item in selected:
                    self.guard()
                    try:
                        self.execute(item,sim,params)
                    except (CommandFailure,RuntimeError,OSError,ValueError) as exc:
                        item.update(status='TIMEOUT' if isinstance(exc,CommandFailure) and exc.timeout else 'ERROR',reason=str(exc))
                        self.log('RESULT '+json.dumps(item));self.checkpoint()
        except Stopped as exc:
            stopped = True;failure = str(exc)
        except Exception as exc:
            failure = str(exc);self.log(traceback.format_exc())
        finally:
            counts = dict(Counter(r['status'] for r in self.records))
            status = 'STOPPED' if stopped else 'PASS' if counts=={'PASS':len(self.records)} and not failure else 'FAIL'
            self.phase = 'finished'
            self.log('TOTAL RESULTS\nW | Case | R x K x N | Result | core cycles | controller-active | exact outputs | max abs error')
            for r in self.records:
                self.log(f"{r['wb']} | {r['case']} | {r['r']} x {r['k']} x {r['n']} | {r['status']} | "
                    f"{r.get('core_cycles','-')} | {r.get('controller_active_cycles','-')} | "
                    f"{r.get('exact_matches','-')}/{r['r']*r['n']} | {r.get('max_absolute_error','-')}")
            self.log('FINAL '+json.dumps(dict(status=status,counts=counts,seconds=round(time.monotonic()-self.begin,3),reason=failure)))
            self.log('Counter events 1..7 are exclusive combinations: sum = controller-active cycles. Load/execute/store busy counts overlap; do not add them. rdcycle includes CPU submission/configuration and final fence. These are not LUMAX stage counters.')
            self.checkpoint(status,failure)
        return 0 if status=='PASS' else 1


def active(state):
    with (state/'worker.lock').open('a+') as f:
        try:
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['check','start','status','stop','worker'])
    parser.add_argument('--state',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--lock-fd',type=int,help=argparse.SUPPRESS)
    args = parser.parse_args()
    STATE_ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
    try:
        if args.command == 'check':
            cfg = settings();preflight(cfg);plan = make_plan(cfg)
            for item in plan:
                operands(item)
            print('CHECK PASS: prerequisites, settings and all random operand generation verified.')
            print(json.dumps(cfg,indent=2));print(f'{len(plan)} matmuls. No builds or simulations were started.')
            return 0
        if args.command == 'worker':
            if args.state is None or args.lock_fd is None:
                raise ValueError('worker is internal; use start')
            lock = os.fdopen(args.lock_fd,'a+')  # Inherited lifetime lock
            def stop_signal(*_):
                global STOP
                STOP = True
            signal.signal(signal.SIGTERM,stop_signal)
            signal.signal(signal.SIGINT,stop_signal)
            try:
                return Runner(args.state).run()
            finally:
                lock.close()
        pointer = STATE_ROOT/'latest.json'
        if args.command == 'start':
            with (STATE_ROOT/'start.lock').open('a+') as launcher:
                fcntl.flock(launcher,fcntl.LOCK_EX)
                if pointer.exists():
                    previous = json.loads(pointer.read_text())
                    if active(Path(previous['state'])):
                        print('Already running. Log: '+previous['log']);return 0
                cfg = settings();jars = preflight(cfg);plan = make_plan(cfg)
                # Catch changed operand generation/seed metadata before building.
                for item in plan:
                    operands(item)
                name = datetime.now().strftime('run_%Y%m%d_%H%M%S_%f')
                state = STATE_ROOT/name;state.mkdir(mode=0o700)
                source = state/'source';source.mkdir()
                for p in (ASSETS/'fpint/src/main/scala').rglob('*.scala'):
                    target = source/p.relative_to(ASSETS/'fpint/src/main/scala')
                    target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
                shutil.copyfile(ASSETS/'software/gemmini_fpint.h',state/'gemmini_fpint.h')
                shutil.copyfile(ASSETS/'software/rocc-software/src/xcustom.h',state/'xcustom.h')
                output = ROOT/f"d{cfg['dim']}_sp{cfg['sp_kib']}_acc{cfg['acc_kib']}_id{cfg['dma_ids']}"/'runs'/name;output.mkdir(parents=True)
                log = output/'verilator.log'
                atomic(state/'options.json',dict(config=cfg,plan=plan,compiler_jars=jars,log=str(log), runner_sha256=sha(SELF), assets=str(ASSETS), source_sha256={str(p.relative_to(source)):sha(p) for p in sorted(source.rglob('*.scala'))}, software_sha256={name:sha(state/name) for name in ('gemmini_fpint.h','xcustom.h')}))
                atomic(pointer,dict(state=str(state),log=str(log)))
                lock = (state/'worker.lock').open('a+')
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                try:
                    with log.open('wb') as stream:
                        proc = subprocess.Popen([sys.executable,'-B',str(SELF),'worker','--state',str(state),
                            '--lock-fd',str(lock.fileno())],stdin=subprocess.DEVNULL,stdout=stream,
                            stderr=subprocess.STDOUT,start_new_session=True,pass_fds=(lock.fileno(),))
                finally:
                    lock.close()  # Child holds the same lock; do not LOCK_UN here.
                for _ in range(30):
                    if (state/'status.json').exists() or proc.poll() is not None:
                        break
                    time.sleep(.1)
                if proc.poll() is not None and not (state/'status.json').exists():
                    raise RuntimeError('Worker failed at startup; inspect '+str(log))
                print(f'Gemmini TEST_LLMS started, PID {proc.pid}.\nLog: {log}\nSafe to close SSH.')
            return 0
        if not pointer.exists():
            print('No Gemmini TEST_LLMS run has been started.');return 0
        latest = json.loads(pointer.read_text());state = Path(latest['state'])
        running = active(state)
        if args.command == 'stop':
            if running:
                (state/'stop.request').touch()
            print('Stop requested for this runner only.' if running else 'Already stopped.')
        else:
            print('Runner active.' if running else 'Runner stopped.')
            print('Log: '+latest['log'])
            if (state/'status.json').exists():
                data = json.loads((state/'status.json').read_text())
                data.pop('records',None)
                print(json.dumps(data,indent=2))
        return 0
    except (OSError,ValueError,RuntimeError) as exc:
        print(str(exc),file=sys.stderr);return 1


if __name__ == '__main__':
    raise SystemExit(main())
