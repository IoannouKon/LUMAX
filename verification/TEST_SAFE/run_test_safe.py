#!/usr/bin/env python3
"""TEST_SAFE: make-built Verilator correctness and independent model accuracy.

MEASURED ON CHEETARA SERVER, 2026-11-06:
  b=4, RF=4, DMA IDs=8, Y_SLICE=2, FP16 activations, signed W8/W4/W2.
  The 20 shapes below (60 matmuls) passed in about 2 hours 30 minutes,
  including the hardware build, RISC-V execution and cycle-model comparison.
  This is a measured reference time; other settings/server load change it.

Run: python3 run_test_safe.py start | status | stop | check
Each new run saves ONLY verilator.log, cycles.csv and model_accuracy.md in a
dated runs/ directory. Temporary state/builds stay outside the saved results.
"""

# ---------------- EDIT TEST_SAFE HERE ----------------
B = 4
RF = 4
DMA_IDS = 8
Y_SLICE = 2
ACTIVATION = 'fp16'                # 'fp16' or 'int16'; inputs always 16 bits
WEIGHT_BITS = (8, 4, 2)
# X[Rin,K] times W[K,Cout]; no software tiling.
MATRIX_CASES = [
    (1, 1, 1), (1, 7, 5), (2, 16, 16), (3, 17, 9), (3, 31, 17),
    (4, 32, 32), (5, 33, 31), (2, 63, 33), (3, 64, 64), (5, 65, 17),
    (4, 128, 128), (2, 255, 7), (3, 256, 8), (2, 257, 9), (1, 511, 5),
    (2, 512, 16), (2, 513, 7), (2, 1025, 5), (2, 2049, 3), (2, 4097, 3),
]
MAKE_JOBS = 8
BASE_SEED = None                  # None = OS randomness; seeds are logged
SIM_TIMEOUT_SECONDS = 7200        # Per matrix
BUILD_TIMEOUT_SECONDS = 3600
SUITE_TIMEOUT_HOURS = 24          # Safety ceiling; finish after the fixed cases
# ----------------------------------------------------

import argparse
from collections import Counter
import csv
from datetime import datetime
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import random
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
import traceback

sys.dont_write_bytecode = True
SELF = Path(__file__).resolve()
ROOT = SELF.parent
sys.path.insert(0, str(ROOT.parent / 'OLD_VERIFICATION'))
# Existing tracked framework helpers; no new companion Python file is needed.
import cycle_compare as cc
import run_fp16 as fp
import run_llama_shapes as hw
import run_y_slice as rows

STATE = Path('/tmp') / f'lumax-test-safe-{os.getuid()}'
STOP = threading.Event()
CYCLE_FIELDS = ['case', 'mode', 'wb', 'r', 'k', 'm', 'b', 'rf', 'y_slice', 'dma_ids',
    'status', 'load_x', 'generate', 'load_w', 'select', 'loadw_select', 'store_o',
    'cycles', 'core', 'seed_x', 'seed_w', 'block', 'x_address', 'w_address', 'o_address',
    'elf_sha256', 'simulator_sha256', 'test_source_sha256', 'x_sha256', 'w_sha256',
    'max_output_error', 'max_ulp_error', 'seconds_boot', 'reason']


def now():
    return datetime.now().astimezone().isoformat()


def atomic(path, data):
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(data, indent=2) + '\n')
    temp.replace(path)


def settings():
    if ACTIVATION not in ('int16', 'fp16') or min(B, RF, DMA_IDS, Y_SLICE, MAKE_JOBS) < 1:
        raise ValueError('Invalid design settings')
    if not MATRIX_CASES or any(len(s) != 3 or min(s) < 1 for s in MATRIX_CASES):
        raise ValueError('Every shape must contain positive Rin,K,Cout')
    if not WEIGHT_BITS or len(set(WEIGHT_BITS)) != len(WEIGHT_BITS) or set(WEIGHT_BITS) - {2, 4, 8}:
        raise ValueError('Select unique weight precisions from W8/W4/W2')
    if ACTIVATION == 'fp16' and max(k for _, k, _ in MATRIX_CASES) > 65536:
        raise ValueError('FP16 hardware requires K <= 65536')
    if min(SIM_TIMEOUT_SECONDS, BUILD_TIMEOUT_SECONDS, SUITE_TIMEOUT_HOURS) <= 0:
        raise ValueError('Timeouts must be positive')
    return dict(b=B, rf=RF, dma_ids=DMA_IDS, y_slice=Y_SLICE, mode=ACTIVATION,
                weights=list(WEIGHT_BITS), shapes=[list(s) for s in MATRIX_CASES])


def plan(seed):
    result = []
    for index, (r, k, m) in enumerate(MATRIX_CASES, 1):
        for wb in WEIGHT_BITS:
            name = f'b{B}_rf{RF}_{ACTIVATION}_ys{Y_SLICE}_id{DMA_IDS}'
            identifier = f'{name}/W{wb}/TC{index:02d}'
            def derived(label):
                return int.from_bytes(hashlib.sha256(f'{seed}:{identifier}/{label}'.encode()).digest()[:8], 'little')
            result.append(dict(id=identifier, case=f'TC{index:02d}', mode=ACTIVATION,
                b=B, rf=RF, dma_ids=DMA_IDS, y_slice=Y_SLICE, wb=wb, r=r, k=k, m=m,
                seed_x=derived('X'), seed_w=derived('W'), status='PENDING', pc=None,
                cycles=None, model_parameters={}))
    return result


def config_text(original):
    text = original.decode()
    values = dict(Rin=max(Y_SLICE, max(r for r, _, _ in MATRIX_CASES)),
                  Cin=max(k for _, k, _ in MATRIX_CASES), Cout=max(m for _, _, m in MATRIX_CASES),
                  x_slice=B, Mem_row_factor=RF, y_slice=Y_SLICE, Dma_Ids=DMA_IDS,
                  DEBUG='true', SCALE='false', FP16_ACTIVATIONS=str(ACTIVATION == 'fp16').lower())
    for key, value in values.items():
        text, count = re.subn(r'(?m)^(\s*'+key+r'\s*=\s*)(?:\d+|true|false)\b',
                             lambda match: match[1]+str(value), text)
        if count != 1:
            raise ValueError('Missing/ambiguous setting '+key)
    return text


def fingerprints():
    paths = [p for p in (hw.GEN/'src/main/scala').rglob('*.scala') if p != hw.CONFIG]
    paths += [SELF, cc.MODEL_PATH, Path(cc.__file__), Path(fp.__file__), Path(rows.__file__), Path(hw.__file__),
              hw.CY/'build.sbt', hw.CY/'generators/chipyard/src/main/scala/config/LUMAXConfigs.scala']
    paths += [hw.SRC/name for name in ('Linear-sw.c', 'build.sh', 'rocc.h', 'compiler.h')]
    return {str(p): hw.sha(p) for p in sorted(set(paths))}


def operands(item):
    rx, rw = random.Random(item['seed_x']), random.Random(item['seed_w'])
    floating = item['mode'] == 'fp16'
    x = [rx.randrange(65536) if floating else rx.randint(-32768, 32767) for _ in range(item['r']*item['k'])]
    if floating:
        x = [v ^ 0x400 if v & 0x7c00 == 0x7c00 else v for v in x]
    lo, hi = -(1 << (item['wb']-1)), (1 << (item['wb']-1))-1
    w = [rw.randint(lo, hi) for _ in range(item['k']*item['m'])]
    nonzero = any(v & 0x7fff for v in x) if floating else any(x)
    if not nonzero:
        x[0] = 0x3c00 if floating else 1
    if not any(w):
        w[0] = -1
    expected = []
    for row in range(item['r']):
        expected.extend(fp.reference(x[row*item['k']:(row+1)*item['k']], w, item['m'], floating))
    return dict(name=item['case'], r=item['r'], k=item['k'], m=item['m'], x=x, w=w,
                block=item['b']*item['rf']*(1 << (8-item['wb'])), expected=expected)


def parse_uart(text):
    outputs = re.findall(r'^HW 0 (.*)$', text, re.M)
    flags = re.findall(r'^RESULT 0 GUARDS (\d) C_MATCH (\d) CORE (\d+) PC ((?:\d+ ){5}\d+)$', text, re.M)
    done = re.findall(r'^TEST_DONE ([01])$', text, re.M)
    if len(outputs) != 1 or len(flags) != 1 or len(done) != 1:
        return [], (0, 0, 0, [0]*6), None
    guard, same, core, counters = flags[0]
    return ([int(v, 16) for v in outputs[0].split()],
            (int(guard), int(same), int(core), list(map(int, counters.split()))), int(done[0]))


def payload_addresses(elf, env):
    nm = shutil.which('riscv64-unknown-elf-nm', path=env.get('PATH'))
    if not nm:
        raise RuntimeError('RISC-V nm is required')
    result = subprocess.run([nm, '--defined-only', str(elf)], env=env, capture_output=True, text=True, timeout=20)
    found = {name: int(address, 16)+8 for address, name in re.findall(r'(?m)^([0-9a-fA-F]+)\s+\w\s+([xwo]0)$', result.stdout)}
    if result.returncode or set(found) != {'x0', 'w0', 'o0'}:
        raise RuntimeError('Cannot determine actual guarded ELF payload addresses')
    return {key: found[symbol] for key, symbol in [('x_address', 'x0'), ('w_address', 'w0'), ('o_address', 'o0')]}


def numerical_errors(item, test, actual):
    def fp32(word):
        return struct.unpack('<f', struct.pack('<I', word))[0]
    def signed(word):
        return word-(1 << 32) if word & 0x80000000 else word
    def ordered(word):
        return 0x80000000-(word & 0x7fffffff) if word & 0x80000000 else 0x80000000+(word & 0x7fffffff)
    floating = item['mode'] == 'fp16'
    x = [struct.unpack('<e', struct.pack('<H', v))[0] for v in test['x']] if floating else test['x']
    output = []
    for index, expected in enumerate(test['expected']):
        row, col = divmod(index, item['m'])
        observed = actual[index] if index < len(actual) else None
        hw_value = (fp32(observed) if floating else signed(observed)) if observed is not None else None
        expected_value = fp32(expected) if floating else signed(expected)
        python_float = math.fsum(x[row*item['k']+j]*test['w'][j*item['m']+col] for j in range(item['k']))
        valid = hw_value is not None and math.isfinite(hw_value)
        error = abs(hw_value-expected_value) if valid else None
        exact_error = None
        if floating and valid:
            units = sum(fp.half_units(test['x'][row*item['k']+j])*test['w'][j*item['m']+col] for j in range(item['k']))
            exact_error = abs(int(hw_value*(1 << 24))-units)/(1 << 24)
        output.append(dict(case=item['case'], wb=item['wb'], row=row, column=col,
            hw_bits=f'{observed:08x}' if observed is not None else '', python_fp32_bits=f'{expected:08x}',
            hw_value=hw_value, python_rounded_value=expected_value, absolute_error=error,
            relative_error_pct=100*error/abs(expected_value) if error is not None and expected_value else (0 if error == 0 else None),
            ulp_error=abs(ordered(observed)-ordered(expected)) if floating and valid else None,
            python_fsum_fp64=python_float, absolute_error_vs_fsum=abs(hw_value-python_float) if valid else None,
            absolute_error_vs_exact_real=exact_error, bitwise_match=observed == expected))
    return output


def write_cycles(path, records):
    temporary = path.with_name(path.name+'.tmp')
    with temporary.open('w', newline='') as out:
        writer = csv.DictWriter(out, fieldnames=CYCLE_FIELDS, extrasaction='ignore')
        writer.writeheader()
        for record in records:
            item = dict(record, **cc.pc_stages(record.get('pc')))
            item.update({key: value for key, value in record.get('model_parameters', {}).items() if key.endswith('_address')})
            writer.writerow(item)
    temporary.replace(path)


def predictions(records):
    # Only called once ALL requested native outputs passed; no timing replay.
    function, info = cc.load_model()
    results = []
    for r in records:
        output = function(**r['model_parameters'])
        if output.get('diagnostic_dma_replay'):
            raise RuntimeError('Diagnostic replay cannot be used for model accuracy')
        stages = {}
        measured = dict(cc.pc_stages(r['pc']), total=r['cycles'])
        for stage, (_, model_key) in cc.STAGES.items():
            predicted = output[model_key]
            if not isinstance(predicted, (int, float)) or not math.isfinite(predicted) or predicted < 0:
                raise RuntimeError('Invalid model cycle prediction')
            hw_cycles = measured[stage]
            stages[stage] = dict(hw=hw_cycles, predicted=predicted,
                absolute_error=abs(predicted-hw_cycles), ape=100*abs(predicted-hw_cycles)/hw_cycles if hw_cycles else None)
        results.append(dict(r, stages=stages, predicted=output['total_cycles'],
                            model_context={key: output.get(key) for key in ('model_version', 'memory_profile', 'memory_assumptions')}))
    return dict(model=info, results=results, summary=cc.stats([(r['cycles'], r['predicted']) for r in results]))


def write_accuracy(path, records, comparison, reason, elapsed):
    def fmt(value):
        return '—' if value is None else f'{value:.4f}'
    counts = dict(Counter(r['status'] for r in records))
    lines = ['# TEST_SAFE model accuracy', '', f'Completion: {reason}. Elapsed: {elapsed/3600:.2f} hours.', '',
        'Configuration: '+json.dumps({key: records[0][key] for key in ('b', 'rf', 'dma_ids', 'y_slice', 'mode')})+'.', '',
        'Hardware results: '+json.dumps(counts)+'. Actual outputs and every per-value error are in '
        '[verilator.log](verilator.log); per-matmul HW stage counts and provenance are in [cycles.csv](cycles.csv).', '',
        'FP16 values are exact binary fractions. Hardware sums integer-weight products exactly in units of '
        '2^-24, then rounds once to FP32, nearest/ties-to-even. Host Python directly calculates that mathematical '
        'dot product using arbitrary-precision integers. Its correctly rounded output bits should therefore match. '
        'Differences from the unrounded real sum or Python binary64 math.fsum may reflect normal FP32 rounding; '
        'they are separately logged. No tolerance is automatically relaxed.', '']
    if not comparison:
        lines += ['Cycle-model evaluation was not completed. All requested native tests must pass before the model is run.']
    else:
        # Existing cycle_compare reports total fields at the top level, while
        # this runner also includes total in its stage dictionary.
        for r in comparison['results']:
            r.setdefault('cycles', r.get('hw'))
            if 'total' not in r['stages']:
                r['stages']['total'] = dict(hw=r['hw'], predicted=r['predicted'],
                    absolute_error=r['absolute_error'], ape=r['ape'])
        info, summary = comparison['model'], comparison['summary']
        lines += [f'Model: `{info["version"]}`; SHA256 `{info["sha256"]}`.', '',
            'Predictions use saved dimensions, elaborated configuration and actual ELF buffer addresses. '
            'Memory assumptions are unchanged; no fitting to observed counters or timing replay is used.', '',
            f'Mean absolute percentage error: **{summary["mape"]:.4f}%**; minimum **{summary["min_ape"]:.4f}%**; '
            f'maximum **{summary["max_ape"]:.4f}%**. Mean absolute cycle error: **{summary["mae"]:.4f}**; '
            f'minimum **{summary["min_absolute_error"]}**; maximum **{summary["max_absolute_error"]}**. '
            f'Cycle-weighted absolute error: **{summary["wape"]:.4f}%**. '
            f'**{summary["within_10_pct"]:.2f}%** of predictions fall within ±10% of measured cycles.', '',
            'Error % = 100 × |prediction − measured| / measured; lower is better. '
            'Zero measured stage counts have no defined percentage error.', '',
            '| Stage | Samples | Mean abs cycles | Min abs | Max abs | Mean error % | Min % | Max % | Weighted % |',
            '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
        for stage in cc.STAGES:
            pairs = [(r['stages'][stage]['hw'], r['stages'][stage]['predicted']) for r in comparison['results'] if r['stages'][stage]['hw'] > 0]
            stats = cc.stats(pairs)
            lines.append(f'| {stage} | {stats["n"]} | '+' | '.join(fmt(stats[key]) for key in (
                'mae', 'min_absolute_error', 'max_absolute_error', 'mape', 'min_ape', 'max_ape', 'wape'))+' |')
        lines += ['', 'Load W and selection overlap. Total active cycles = Load X + generation + '
                  'combined Load W/selection + Store O. Do not add the overlap diagnostics again. '
                  'CPU reference work, printing and simulator startup are outside this active total.', '',
                  '| W bits | Samples | Mean abs cycles | Min abs | Max abs | Mean error % | Min % | Max % |',
                  '|---|---:|---:|---:|---:|---:|---:|---:|']
        for wb in sorted({r['wb'] for r in records}, reverse=True):
            stats = cc.stats([(r['cycles'], r['predicted']) for r in comparison['results'] if r['wb'] == wb])
            lines.append(f'| {wb} | {stats["n"]} | '+' | '.join(fmt(stats[key]) for key in (
                'mae', 'min_absolute_error', 'max_absolute_error', 'mape', 'min_ape', 'max_ape'))+' |')
        for stage, (name, _) in cc.STAGES.items():
            lines += ['', '## '+name, '', '| Case | W bits | Rin×K×Cout | HW cycles | Model cycles | Absolute error | Error % |',
                      '|---|---:|---|---:|---:|---:|---:|']
            for r in comparison['results']:
                value = r['stages'][stage]
                case = r['case'].rsplit('/', 1)[-1]
                lines.append(f'| {case} | {r["wb"]} | {r["r"]}×{r["k"]}×{r["m"]} | '
                    f'{value["hw"]} | {value["predicted"]} | {value["absolute_error"]} | {fmt(value["ape"])} |')
        contexts = {json.dumps(r.get('model_context', {}), sort_keys=True) for r in comparison['results']}
        if contexts:
            lines += ['', 'Model environment assumptions: '+ '; '.join(sorted(contexts)), '']
    path.write_text('\n'.join(lines)+'\n')


class Interrupted(Exception):
    pass


class Runner:
    def __init__(self, state, options):
        self.state, self.options = state, options
        self.output = Path(options['output'])
        self.records = plan(options['seed'])
        self.started = time.monotonic()
        self.deadline = self.started+options['hours']*3600
        self.work = Path(tempfile.mkdtemp(prefix='lumax-test-safe-'))
        self.phase, self.current, self.child = 'starting', '', None
        self.sources, self.model_sha = {}, None
        self.sequence = 0

    def log(self, message):
        with (self.output/'verilator.log').open('a') as out:
            out.write(f'[{now()}] {message}\n')

    def checkpoint(self, status='RUNNING', reason=''):
        atomic(self.state/'status.json', dict(status=status, reason=reason, phase=self.phase,
            current=self.current, pid=os.getpid(), child_pid=self.child, updated=now(),
            elapsed_seconds=round(time.monotonic()-self.started, 1),
            counts=dict(Counter(r['status'] for r in self.records)), output=str(self.output)))
        write_cycles(self.output/'cycles.csv', self.records)

    def guard(self):
        if STOP.is_set() or (self.state/'stop.request').exists():
            raise Interrupted('User requested stop')
        if time.monotonic() >= self.deadline:
            raise Interrupted('Suite safety timeout reached')
        if self.sources and fingerprints() != self.sources:
            raise RuntimeError('Source files changed during verification')

    def command(self, args, cwd, env, timeout, phase):
        self.guard()
        self.phase = phase
        self.sequence += 1
        output = self.work/f'command_{self.sequence}.log'
        self.log('COMMAND: '+shlex.join(list(map(str, args))))
        start, result = time.monotonic(), None
        with output.open('w') as stream:
            proc = subprocess.Popen(list(map(str, args)), cwd=cwd, env=env, stdout=stream,
                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
            self.child = proc.pid
            self.checkpoint()
            heartbeat = time.monotonic()
            try:
                while proc.poll() is None:
                    if STOP.is_set() or (self.state/'stop.request').exists():
                        raise Interrupted('User requested stop')
                    if time.monotonic() >= self.deadline:
                        raise Interrupted('Suite safety timeout reached')
                    if time.monotonic()-start >= timeout:
                        result = 'TIMEOUT'
                        break
                    if time.monotonic()-heartbeat >= 15:
                        self.checkpoint()
                        heartbeat = time.monotonic()
                    time.sleep(.25)
                else:
                    result = proc.returncode
            finally:
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        proc.wait(5)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        proc.wait()
                self.child = None
                stream.flush()
                self.log('ACTUAL '+phase.upper()+' OUTPUT\n'+output.read_text(errors='replace'))
        self.log(f'COMMAND EXIT={result}; seconds={time.monotonic()-start:.3f}')
        self.guard()
        return result, output.read_text(errors='replace')

    def build(self, original, env):
        self.text = config_text(original)
        self.snapshot = self.work/'Config.scala'
        self.snapshot.write_text(self.text)
        self.sim = self.work/'simulator-LUMAXROcketConfig'
        self.log('ELABORATION CONFIGURATION\n'+self.text)
        if hw.CONFIG.read_bytes() != original:
            raise RuntimeError('External Config.scala change; refusing to overwrite it')
        try:
            hw.CONFIG.write_text(self.text)
            code, _ = self.command(['make', '-j'+str(self.options['jobs']), 'CONFIG=LUMAXROcketConfig',
                'sim='+str(self.sim), 'gen_dir='+str(self.work/'generated-src')],
                hw.CY/'sims/verilator', env, self.options['build_timeout'], 'make')
        finally:
            if hw.CONFIG.read_bytes() == self.text.encode():
                hw.CONFIG.write_bytes(original)
            elif hw.CONFIG.read_bytes() != original:
                raise RuntimeError('Config.scala edited externally; original snapshot is in verilator.log')
        if code != 0 or not self.sim.is_file():
            for r in self.records:
                r.update(status='BUILD_ERROR', reason=f'make exit={code}')
            raise RuntimeError(f'make failed: {code}')
        self.sim_sha = hw.sha(self.sim)
        self.log('MAKE PASS SIMULATOR_SHA256='+self.sim_sha)
        shutil.rmtree(self.work/'generated-src', ignore_errors=True)

    def execute(self, item, env, base):
        self.guard()
        self.current = item['id']
        folder = Path(tempfile.mkdtemp(prefix='case-', dir=self.work))
        started = time.monotonic()
        try:
            test = operands(item)
            item.update(block=test['block'], simulator_sha256=self.sim_sha,
                x_sha256=hashlib.sha256(json.dumps(test['x']).encode()).hexdigest(),
                w_sha256=hashlib.sha256(json.dumps(test['w']).encode()).hexdigest())
            self.log('CASE '+json.dumps(item))
            self.log('RANDOM STATISTICS '+json.dumps(dict(x_nonzero=sum(v & 0x7fff != 0 if item['mode']=='fp16' else v != 0 for v in test['x']),
                w_nonzero=sum(v != 0 for v in test['w']), w_distinct=len(set(test['w'])))))
            floating = item['mode'] == 'fp16'
            (folder/'Linear-sw.c').write_text(rows.source(base, [test], item['wb'], floating, Y_SLICE, B, RF))
            for name in ('build.sh', 'rocc.h', 'compiler.h'):
                shutil.copy2(hw.SRC/name, folder/name)
            item['test_source_sha256'] = hw.sha(folder/'Linear-sw.c')
            case_env = dict(env, LUMAX_CONFIG_PATH=str(self.snapshot), TEST_CFLAGS=f'-O2 -DLUMAX_FP16_ACTIVATIONS={int(floating)}')
            code, _ = self.command(['bash', './build.sh', 'baremetal'], folder, case_env, 300, 'RISC-V build')
            if code != 0:
                item.update(status='COMPILE_ERROR', reason=f'compile exit={code}')
                return
            elf = folder/'Linear-sw.riscv'
            item['elf_sha256'] = hw.sha(elf)
            addresses = payload_addresses(elf, case_env)
            item['model_parameters'] = dict(cc.parameters(item['k'], item['m'], item['wb'], B, RF,
                rows=item['r'], activation=item['mode'], physical=cc.physical_parameters(self.text),
                addresses=addresses), block_size=test['block'])
            command = [str(self.sim), '+permissive', '+max-cycles=20000000000', '+loadmem='+str(elf), '+permissive-off', str(elf)]
            code, uart = self.command(command, folder, case_env, self.options['timeout'], 'Verilator UART')
            actual, (safe, same, core, pc), done = parse_uart(uart)
            errors = numerical_errors(item, test, actual)
            stream = io.StringIO()
            writer = csv.DictWriter(stream, fieldnames=list(errors[0]))
            writer.writeheader()
            writer.writerows(errors)
            self.log('PER-VALUE PYTHON COMPARISON\n'+stream.getvalue())
            numeric = [r['absolute_error'] for r in errors if r['absolute_error'] is not None]
            ulps = [r['ulp_error'] for r in errors if r['ulp_error'] is not None]
            item.update(pc=pc, cycles=sum(pc[j] for j in (0, 1, 4, 5)), core=core,
                        max_output_error=max(numeric, default=None), max_ulp_error=max(ulps, default=None))
            passed = code == 0 and done == 0 and safe == same == 1 and actual == test['expected'] and item['cycles'] > 0 and core > 0
            item.update(status='PASS' if passed else 'TIMEOUT' if code == 'TIMEOUT' or 'timeout' in uart.lower() else 'FAIL',
                        reason='' if passed else f'exit={code}; TEST_DONE={done}; guards={safe}; C_match={same}; exact_matches={sum(a==e for a,e in zip(actual,test["expected"]))}/{len(test["expected"])}')
        except Interrupted:
            item.update(status='SKIPPED', reason='Stopped during operation')
            raise
        except Exception as exc:
            item.update(status='ERROR', reason=str(exc))
            self.log(traceback.format_exc())
            self.guard()
        finally:
            item['seconds_boot'] = round(time.monotonic()-started, 3)
            self.log('RESULT '+json.dumps(item))
            shutil.rmtree(folder, ignore_errors=True)
            self.checkpoint()

    def run(self):
        failure, reason = False, 'All requested TEST_SAFE operations completed'
        comparison = None
        try:
            with hw.CONFIG.open('rb') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                original, env = preflight()
                self.sources, self.model_sha = fingerprints(), hw.sha(cc.MODEL_PATH)
                self.log('TEST_SAFE START '+json.dumps(self.options))
                self.log('SOURCE SHA256 '+json.dumps(self.sources))
                self.log('ORIGINAL CONFIG JSON '+json.dumps(original.decode()))
                runtime = self.work/'runtime'
                runtime.mkdir()
                env['XDG_RUNTIME_DIR'] = str(runtime)
                env['SBT_OPTS'] = (env.get('SBT_OPTS') or f'-Dsbt.ivy.home={hw.CY}/.ivy2 -Dsbt.global.base={hw.CY}/.sbt -Dsbt.boot.directory={hw.CY}/.sbt/boot/')+' -Dsbt.server.forcestart=true -Dsbt.server.autostart=false -Dsbt.supershell=false'
                self.build(original, env)
                base = (hw.SRC/'Linear-sw.c').read_text()
                for item in self.records:
                    self.execute(item, env, base)
                if hw.CONFIG.read_bytes() != original:
                    raise RuntimeError('Config.scala was not restored')
        except Interrupted as exc:
            reason = str(exc)
        except Exception as exc:
            failure, reason = True, str(exc)
            self.log(traceback.format_exc())
        finally:
            for item in self.records:
                if item['status'] == 'PENDING':
                    item.update(status='SKIPPED', reason=reason)
            if all(r['status'] == 'PASS' for r in self.records):
                self.phase, self.current = 'cycle-model comparison', ''
                self.checkpoint()
                try:
                    if hw.sha(cc.MODEL_PATH) != self.model_sha:
                        raise RuntimeError('Model.py changed during verification')
                    comparison = predictions(self.records)
                except Exception as exc:
                    failure, reason = True, 'Model comparison failed: '+str(exc)
                    self.log(traceback.format_exc())
            try:
                write_accuracy(self.output/'model_accuracy.md', self.records, comparison, reason, time.monotonic()-self.started)
                self.phase, self.current = 'finished', ''
                status = ('ERROR' if failure else 'STOPPED' if STOP.is_set() or (self.state/'stop.request').exists()
                          else 'COMPLETE' if all(r['status']=='PASS' for r in self.records) else 'COMPLETE_WITH_FAILURES')
                self.checkpoint(status, reason)
                self.log('FINISHED '+json.dumps(dict(status=status, counts=dict(Counter(r['status'] for r in self.records)),
                    model_summary=comparison['summary'] if comparison else None)))
            finally:
                shutil.rmtree(self.work, ignore_errors=True)
        return int(failure)


def preflight():
    settings()
    original = hw.CONFIG.read_bytes()
    for key, value in [('XBitWidth', 16), ('WBitWidth', 8), ('OutBitWidth', 32), ('DMA_Bytes', 8)]:
        if not re.search(r'(?m)^\s*'+key+r'\s*=\s*'+str(value)+r'\s*,', original.decode()):
            raise ValueError(f'Requires {key}={value}')
    if not re.search(r'(?m)^#define\s+SCALE\s+0\b', (hw.SRC/'Linear-sw.c').read_text()):
        raise ValueError('Requires SCALE=0 in Linear-sw.c')
    config_text(original)
    env = hw.environment()
    for tool in ('make', 'verilator', 'riscv64-unknown-elf-gcc', 'riscv64-unknown-elf-nm'):
        if not shutil.which(tool, path=env.get('PATH')):
            raise RuntimeError('Missing '+tool)
    return original, env


def active(state):
    with (state/'worker.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['start', 'status', 'stop', 'check', 'worker'])
    parser.add_argument('--output', type=Path, help='New output directory; defaults to runs/run_<date>')
    parser.add_argument('--state', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.command == 'check':
            preflight()
            print('Setup OK: '+json.dumps(settings())+'; '+str(len(plan(1)))+' tests; no run started.')
        elif args.command == 'worker':
            if not args.state:
                parser.error('Use start')
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, lambda *_: STOP.set())
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
            with (args.state/'worker.lock').open('a+') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                options = json.loads((args.state/'options.json').read_text())
                if options['settings'] != settings():
                    raise RuntimeError('Settings changed after launch')
                runner = Runner(args.state, options)
                runner.checkpoint()
                return runner.run()
        elif args.command == 'start':
            settings()
            STATE.mkdir(mode=0o700, exist_ok=True)
            with (STATE/'launch.lock').open('a+') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                pointer = STATE/'latest.json'
                if pointer.exists():
                    old = json.loads(pointer.read_text())
                    if active(Path(old.get('state_dir', old['run_dir']))):
                        raise RuntimeError('TEST_SAFE is already running')
                name = 'run_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                output = (args.output or ROOT/'runs'/name).resolve()
                output.mkdir(parents=True, exist_ok=False)
                state = STATE/name
                state.mkdir()
                atomic(state/'options.json', dict(settings=settings(), seed=BASE_SEED if BASE_SEED is not None else secrets.randbits(64),
                    jobs=MAKE_JOBS, timeout=SIM_TIMEOUT_SECONDS, build_timeout=BUILD_TIMEOUT_SECONDS,
                    hours=SUITE_TIMEOUT_HOURS, output=str(output), python=sys.version))
                atomic(pointer, dict(run_dir=str(output), state_dir=str(state)))
                with (output/'verilator.log').open('a') as out:
                    proc = subprocess.Popen([sys.executable, '-B', str(SELF), 'worker', '--state', str(state)],
                        stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
                for _ in range(40):
                    if (state/'status.json').exists() or proc.poll() is not None:
                        break
                    time.sleep(.1)
                if proc.poll() is not None and not (state/'status.json').exists():
                    raise RuntimeError('Worker failed at startup; inspect verilator.log')
                print(f'TEST_SAFE background PID {proc.pid}\nResults: {output}\nSafe to close SSH.')
        else:
            pointer = json.loads((STATE/'latest.json').read_text())
            state = Path(pointer.get('state_dir', pointer['run_dir']))
            running = active(state)
            if args.command == 'stop':
                if running:
                    (state/'stop.request').touch()
                print('Stop requested.' if running else 'Already stopped.')
            else:
                print('TEST_SAFE active.' if running else 'TEST_SAFE stopped.')
                print('Results: '+pointer['run_dir'])
                print((state/'status.json').read_text() if (state/'status.json').exists() else 'Starting; inspect verilator.log.')
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
