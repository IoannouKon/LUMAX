#!/usr/bin/env python3
"""Run the complete TC01–TC11 suite in background: start | status | stop | check.

Also supplies the shared execution functions used by run_regression.py.
All cases use actual make-built Verilator, RISC-V and LUMAX RoCC calls.
"""
import argparse
import collections
import concurrent.futures
import csv
from datetime import datetime
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import resource
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

sys.dont_write_bytecode = True
CY = Path('/home/kioannou/chipyard_1.13.0')
GEN = CY / 'generators/LUMAX'
SRC = GEN / 'software/tests/src'
CONFIG = GEN / 'src/main/scala/Config.scala'
SELF = Path(__file__).resolve()
SUITE = 'test_cases'
LOGROOT = SELF.parent / 'logs' / SUITE
LOGDIR = None
STATE = Path('/tmp/lumax-test-cases-' + str(os.getuid()))
GEOMETRIES = [(b, rf) for b in (2, 4, 8) for rf in (4, 8)]
TCS = [f'TC{i:02d}' for i in range(1, 12)]
TIMEOUT = 7200
BUILD_TIMEOUT = 7200
MAX_CYCLES = 20000000000
JOBS = 8
WRITE_LOCK = threading.RLock()
STOP = threading.Event()
ROOT = None
BASE = ''
USED_SEEDS = set()

def configure(suite):
    global SUITE, SELF, LOGROOT, STATE, TCS
    SUITE = suite
    SELF = Path(__file__).resolve().parent / ('run_regression.py' if suite == 'regression' else 'run_test_cases.py')
    LOGROOT = SELF.parent / 'logs' / suite
    STATE = Path('/tmp/lumax-' + suite.replace('_', '-') + '-' + str(os.getuid()))
    TCS = ['REGRESSION'] if suite == 'regression' else [f'TC{i:02d}' for i in range(1, 12)]

def stopped():
    return STOP.is_set() or (STATE / 'stop').exists()

def now(): return datetime.now().astimezone().isoformat()
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def log(tc, text):
    with WRITE_LOCK:
        with (LOGDIR / f'Log_{tc}.log').open('a') as out:
            out.write(text)
            out.flush()

def cases():
    result = []
    def add(tc, k, m, pattern='random', detail=None, stages=None):
        result.append(dict(tc=tc, k=k, m=m, pattern=pattern, detail=detail, stages=stages))
    for k in [4,8,16]: add('TC01',k,k)
    for k,m in [(12,20),(28,36),(60,12)]: add('TC02',k,m)
    for k,m in [(4,260),(260,4),(16,512),(512,16)]: add('TC03',k,m)
    for k in [7,8,9,15,16,17,31,32,33,63,64,65]: add('TC04',k,8)
    for m in [3,4,5,7,8,9,15,16,17]: add('TC05',16,m)
    for pattern in ['zero_x','zero_w','zero_both']: add('TC06',16,8,pattern)
    for k,m in [(16,8),(256,4)]:
        for pattern in ['min_min','max_max','min_max','max_min','alternating']:
            add('TC07',k,m,pattern)
    for j in range(16): add('TC08',16,8,'onehot_x',j)
    for j in range(16):
        for c in range(8): add('TC08',16,8,'onehot_w',(j,c))
    for k,m in [(12,20),(60,12)]:
        for seed_pair in range(10): add('TC09',k,m,'random',seed_pair+1)
    for k in [256,512]: add('TC10',k,k)
    add('TC11',60,20,'sequence',stages=[(16,8,'random'),(16,8,'zero_both'),(16,8,'random'),
                                      (12,20,'random'),(60,12,'random'),(16,8,'random')])
    if SUITE == 'regression':
        result = []
        for k in [4,8,16,32,64,128,256,512]: add('REGRESSION',k,k)
        for k,m in [(64,16),(128,32)]: add('REGRESSION',k,m)
    else:
        result = [entry for entry in result if entry['tc'] in TCS]
    counts = collections.Counter()
    for b,rf in GEOMETRIES:
        for wb in [8,4,2]:
            for entry in result:
                case = dict(entry, b=b, rf=rf, wb=wb, design=f'b{b}_rf{rf}')
                counts[case['tc']] += 1
                case['id'] = f'{case["tc"]}_{counts[case["tc"]]:04d}'
                yield case

def random_values(seed, bits, count):
    state = seed & 0xffffffff
    lo = -(1 << (bits-1))
    values = []
    for _ in range(count):
        state = (state * 6364136223846793005 + 1) & ((1 << 64)-1)
        values.append(lo + ((state >> 32) & 0x7fffffff) % (1 << bits))
    return values

def seed():
    with WRITE_LOCK:
        while True:
            value = secrets.randbits(32)
            if value and value not in USED_SEEDS:
                USED_SEEDS.add(value)
                return value

def vectors(k, m, wb, pattern, detail):
    xs, ws = seed(), seed()
    x = random_values(xs,16,k)
    w = random_values(ws,wb,k*m)
    lo, hi = -(1 << (wb-1)), (1 << (wb-1))-1
    if pattern in ['zero_x','zero_both']: x = [0]*k
    if pattern in ['zero_w','zero_both']: w = [0]*(k*m)
    if pattern in ['min_min','min_max']: x = [-32768]*k
    if pattern in ['max_min','max_max']: x = [32767]*k
    if pattern in ['min_min','max_min']: w = [lo]*(k*m)
    if pattern in ['min_max','max_max']: w = [hi]*(k*m)
    if pattern == 'alternating':
        x = [-32768 if j%2 else 32767 for j in range(k)]
        w = [lo if (j+c)%2 else hi for j in range(k) for c in range(m)]
    if pattern == 'onehot_x':
        x = [0]*k
        x[detail] = -32768 if detail%2 else 32767
    if pattern == 'onehot_w':
        j,c = detail
        if not x[j]: x[j] = 19
        w = [0]*(k*m)
        w[j*m+c] = lo if (j+c)%2 else hi
    ref = [sum(x[j]*w[j*m+c] for j in range(k)) for c in range(m)]
    assert all(-(1 << 31) <= v < (1 << 31) for v in ref)
    # Global column-major bit packing, without per-column or dimension padding.
    packed = [0xa5]*((k*m*wb+7)//8)
    for index,v in enumerate(w[j*m+c] for c in range(m) for j in range(k)):
        byte,shift = divmod(index*wb,8)
        mask = ((1 << wb)-1) << shift
        packed[byte] = (packed[byte] & (255 ^ mask)) | ((v & ((1 << wb)-1)) << shift)
    # Verify every packed element independently, including byte/column tails.
    for c in range(m):
        for j in range(k):
            bit=(c*k+j)*wb
            v=(packed[bit//8] >> (bit%8)) & ((1 << wb)-1)
            if v & (1 << (wb-1)): v -= 1 << wb
            assert v == w[j*m+c]
    return dict(k=k,m=m,x=x,w=w,packed=packed,ref=ref,xs=xs,ws=ws,pattern=pattern)

def array(ctype, name, values):
    return f'static const {ctype} {name}[{len(values)}] = {{'+','.join(map(str,values))+'};\n'

def source(case, stages):
    c = BASE.split('int main() {',1)[0]
    for key,value in {'IN_BITS':16,'W_BITS':case['wb'],'Debug':0,'XS':case['b'],'YS':1,'Mem_row_factor':case['rf']}.items():
        c,n = re.subn(r'(^#define\s+'+key+r'\s+)\d+',lambda mt:mt[1]+str(value),c,flags=re.M)
        assert n == 1, key
    # Only the test harness is replaced; original RoCC/preload/compute helpers are used.
    c += '\n#undef printf\n'
    for index,stage in enumerate(stages):
        c += array('input_t',f'test_x_{index}',stage['x'])
        c += array('uint8_t',f'test_w_{index}',stage['packed'])
        c += array('output_t',f'test_ref_{index}',stage['ref'])
    max_k=max(s['k'] for s in stages)
    max_m=max(s['m'] for s in stages)
    max_w=max(len(s['packed']) for s in stages)
    c += f'''
static uint8_t arena_x[{max_k*2+24}] __attribute__((aligned(8)));
static uint8_t arena_w[{max_w+24}] __attribute__((aligned(8)));
static uint8_t arena_o[{max_m*4+24}] __attribute__((aligned(8)));
static unsigned long previous_combined=0;
static int guards(const uint8_t *a,int cap,int used) {{
  for(int i=0;i<8;++i) if(a[i]!=0xa5) return 0;
  for(int i=8+used;i<cap;++i) if(a[i]!=0xa5) return 0;
  return 1;
}}
static int run_stage(int stage,int k,int m,const input_t *x,const uint8_t *w,int nb,const output_t *ref) {{
  memset(arena_x,0xa5,sizeof(arena_x));
  memset(arena_w,0xa5,sizeof(arena_w));
  memset(arena_o,0xa5,sizeof(arena_o));
  memcpy(arena_x+8,x,k*sizeof(input_t));
  memcpy(arena_w+8,w,nb);
  memset(arena_o+8,0x5a,m*sizeof(output_t));
  QuantizedTensor_input tx={{.q=(input_t*)(arena_x+8)}};
  QuantizedTensor_weight tw={{.q=(weight_t*)(arena_w+8)}};
  output_t *hw=(output_t*)(arena_o+8);
  preload_hw(k,1,m,x_slice_max*RF,y_slice,tx.q,tw.q,hw);
  matmul_hw_uniform(hw,&tx,&tw,k,m,x_slice_max*RF,y_slice,1);
  unsigned long counts[6];
  for(int i=0;i<6;++i) counts[i]=read_rd();
  /* The selector is five bits and does not reset at each operation. */
  for(int i=6;i<32;++i) (void)read_rd();
  /* The combined phase counter is cumulative; other PCs reset in preload. */
  unsigned long combined=counts[5];
  counts[5]-=previous_combined;
  previous_combined=combined;
  int same=0;
  for(int i=0;i<m;++i) if(hw[i]==ref[i]) ++same;
  int safe=guards(arena_x,sizeof(arena_x),k*sizeof(input_t)) &&
           guards(arena_w,sizeof(arena_w),nb) &&
           guards(arena_o,sizeof(arena_o),m*sizeof(output_t)) &&
           !memcmp(arena_x+8,x,k*sizeof(input_t)) && !memcmp(arena_w+8,w,nb);
  printf("STEP %d MATCHES %d/%d GUARDS %s HW_CYCLES %lu\\n",stage,same,m,safe?"PASS":"FAIL",counts[0]+counts[1]+counts[5]+counts[4]);
  char report[{max_m*16+1}];
  int used=0;
  for(int i=0;i<m;++i) {{
    int n=snprintf(report+used,sizeof(report)-used,"%d ",hw[i]);
    if(n<0 || n>=sizeof(report)-used) return 1;
    used+=n;
  }}
  printf("HW_OUTPUT_%d %s\\n",stage,report);
  return same!=m || !safe;
}}
int main(void) {{
  int failed=0;
'''
    for index,s in enumerate(stages):
        c += f'  failed |= run_stage({index+1},{s["k"]},{s["m"]},test_x_{index},test_w_{index},{len(s["packed"])},test_ref_{index});\n'
    c += '  printf("TEST_FINAL %s\\n",failed?"FAIL":"PASS");\n  return failed;\n}\n'
    return c

def command(args, cwd, output, timeout):
    if stopped(): raise InterruptedError('Stopped by user')
    with output.open('w') as out:
        proc = subprocess.Popen(args,cwd=cwd,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
        deadline=time.monotonic()+timeout
        try:
            while True:
                try: return proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    if stopped(): raise InterruptedError('Stopped by user')
                    if time.monotonic()>deadline: raise TimeoutError('Command timed out')
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid,signal.SIGTERM)
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid,signal.SIGKILL)
                    proc.wait()

def matrix(out,label,rows,cols,values):
    out.write(f'{label} ({rows} x {cols}):\n')
    for r in range(rows):out.write(' '.join(map(str,values[r*cols:(r+1)*cols]))+'\n')

def execute(case, sim):
    if stopped(): raise InterruptedError('Stopped by user')
    folder=ROOT/case['id']
    folder.mkdir()
    out=io.StringIO()
    out.write('\n'+'='*100+'\n'+case['id']+' '+case['design']+f' input16 W{case["wb"]} pattern={case["pattern"]} detail={case["detail"]}\n'+'='*100+'\n')
    specs=case['stages'] or [(case['k'],case['m'],case['pattern'])]
    stages=[vectors(k,m,case['wb'],pattern,case['detail']) for k,m,pattern in specs]
    for index,s in enumerate(stages,1):
        out.write(f'STEP {index}: X=1x{s["k"]}, W={s["k"]}x{s["m"]}; INPUT_SEED={s["xs"]}; WEIGHT_SEED={s["ws"]}; pattern={s["pattern"]}\n')
        if s['k']*s['m'] <= 2048:
            matrix(out,'INPUT X',1,s['k'],s['x'])
            matrix(out,'WEIGHTS W',s['k'],s['m'],s['w'])
        else:
            out.write('Large matrix reproducibility: the seeds above use newlib_rand; logical row-major W and column-major bit packing.\n')
            out.write('X_SHA256='+hashlib.sha256(json.dumps(s['x']).encode()).hexdigest()+'\n')
            out.write('W_SHA256='+hashlib.sha256(json.dumps(s['w']).encode()).hexdigest()+'\n')
        matrix(out,'PYTHON OUTPUT',1,s['m'],s['ref'])
    result={key:case[key] for key in ['id','tc','design','k','m','wb','pattern','detail']}
    result.update(status='ERROR',matches=0,elements=sum(s['m'] for s in stages),steps=len(stages),cycles=[],seconds=0)
    start=time.monotonic()
    try:
        c=source(case,stages)
        (folder/'Linear-sw.c').write_text(c)
        for name in ['rocc.h','compiler.h','build.sh']:shutil.copy2(SRC/name,folder/name)
        out.write(f'TEST_C_SHA256={sha(folder/"Linear-sw.c")}\nSIMULATOR_SHA256={sha(sim)}\n')
        build=command(['bash','./build.sh','baremetal'],folder,folder/'build.log',600)
        out.write((folder/'build.log').read_text(errors='replace')+f'\nRISC-V BUILD EXIT={build}\n')
        if build:
            result['status']='BUILD_FAIL'
        else:
            elf=folder/'Linear-sw.riscv'
            out.write('ELF_SHA256='+sha(elf)+'\n')
            args=[str(sim),'+permissive',f'+max-cycles={MAX_CYCLES}',f'+loadmem={elf}','+permissive-off',str(elf)]
            out.write('ACTUAL VERILATOR COMMAND: '+' '.join(args)+'\nBEGIN ACTUAL VERILATOR OUTPUT\n')
            code=command(args,folder,folder/'uart.log',TIMEOUT)
            uart=(folder/'uart.log').read_text(errors='replace').replace('\r\n','\n')
            out.write(uart+'\nEND ACTUAL VERILATOR OUTPUT\nVERILATOR_EXIT='+str(code)+'\n')
            ok=code==0 and bool(re.search(r'^TEST_FINAL PASS$',uart,re.M))
            for index,s in enumerate(stages,1):
                row=re.search(r'^STEP '+str(index)+r' MATCHES (\d+)/(\d+) GUARDS (PASS|FAIL) HW_CYCLES (\d+)$',uart,re.M)
                observed=re.search(r'^HW_OUTPUT_'+str(index)+r' (.*)$',uart,re.M)
                if not row or not observed:
                    ok=False
                    out.write(f'STEP {index}: missing HW output/status; cannot verify.\n')
                    continue
                hw=list(map(int,observed[1].split()))
                count=sum(a==b for a,b in zip(hw,s['ref']))
                result['matches']+=count
                result['cycles'].append(int(row[4]))
                ok=ok and len(hw)==s['m'] and hw==s['ref'] and (int(row[1]),int(row[2]))==(count,s['m']) and row[3]=='PASS' and int(row[4])>0
                matrix(out,f'OBSERVED HW OUTPUT STEP {index}',1,len(hw),hw)
                out.write(f'HW/PYTHON MATCHES={count}/{s["m"]}; GUARDS={row[3]}\n')
            result['status']='PASS' if ok else 'FAIL'
    except TimeoutError as e:
        result['status']='TIMEOUT'
        out.write(str(e)+'\n')
        if (folder/'uart.log').exists():out.write((folder/'uart.log').read_text(errors='replace'))
    except InterruptedError:
        result['status']='STOPPED'
        out.write('Stopped by user.\n')
    except Exception as e:out.write(f'RUNNER ERROR: {type(e).__name__}: {e}\n')
    result['seconds']=round(time.monotonic()-start,3)
    out.write(f'FINAL RESULT: {result["status"]}; MATCHES={result["matches"]}/{result["elements"]}; SECONDS={result["seconds"]}\n')
    log(case['tc'],out.getvalue())
    print(case['id'],case['design'],result['status'],flush=True)
    shutil.rmtree(folder)
    return result

def worker(workers):
    global ROOT, BASE
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    records=[]
    completed=False
    original_config=None
    original_sources={}
    lock=(STATE/'lock').open('a+')
    config_lock=CONFIG.open('rb')
    for sig in [signal.SIGTERM,signal.SIGINT]:signal.signal(sig,lambda *_:STOP.set())
    signal.signal(signal.SIGHUP,signal.SIG_IGN)
    plans=list(cases())
    counts=collections.Counter(c['tc'] for c in plans)
    unchanged=False
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        fcntl.flock(config_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        preflight()
        ROOT=STATE/'work'
        ROOT.mkdir()
        BASE=(SRC/'Linear-sw.c').read_text()
        os.environ['TEST_CFLAGS']='-O2 -DLUMAX_QUIET_TEST=1'
        original_config=CONFIG.read_bytes()
        (STATE/'original-config.scala').write_bytes(original_config)
        original_sources={str(p):sha(p) for p in (GEN/'src/main/scala').glob('*.scala')}
        original_sources.update({str(SRC/name):sha(SRC/name) for name in ['Linear-sw.c','rocc.h','compiler.h','build.sh']})
        for tc in TCS:
            log(tc,f'{tc}: LUMAX CHIPYARD 1.13.0\nSTART: {now()}\nPLANNED: {counts[tc]} independent simulator invocations. Input16, weights8/4/2, RIN=1, {len(GEOMETRIES)} designs.\n'
                +'Each design built once with make and reused; Python checks actual HW outputs against independently calculated integer references.\n'
                +'Only geometry in Config.scala is selected for each build, then restored. Original C helpers are unchanged. No dimension padding.\n'
                +'Guard bytes surround every logical buffer. TC11 uses six operations in one ELF, with no accelerator reset.\n'
                +'TC11 counter reads wrap the 5-bit selector; the cumulative combined phase is differenced per operation.\n'
                +f'TIMEOUT: each simulator={TIMEOUT}s; each make={BUILD_TIMEOUT}s; max-cycles={MAX_CYCLES}. TIMEOUT is inconclusive.\n'
                +'Full X/W retained for <=2048 weights; seeds/hashes for larger matrices. All Python/HW outputs retained.\n'
                +'SOURCE SHA256: '+json.dumps(original_sources,sort_keys=True)+'\n')
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            for b,rf in GEOMETRIES:
                if stopped():raise InterruptedError('Stopped by user')
                if any(sha(Path(p))!=v for p,v in original_sources.items()):raise RuntimeError('HW/SW sources changed during the run')
                design=f'b{b}_rf{rf}'
                folder=ROOT/design
                folder.mkdir()
                cfg=original_config.decode()
                for key,value in [('x_slice',b),('Mem_row_factor',rf)]:
                    cfg,n=re.subn(r'(?m)^(\s*'+key+r'\s*=\s*)\d+',lambda mt:mt[1]+str(value),cfg)
                    assert n==1
                sim=folder/'simulator-LUMAXROcketConfig'
                args=['make','-j'+str(JOBS),'CONFIG=LUMAXROcketConfig','sim='+str(sim),'gen_dir='+str(folder/'generated-src')]
                print('BUILD',design,flush=True)
                try:
                    CONFIG.write_text(cfg)
                    code=command(args,CY/'sims/verilator',folder/'build.log',BUILD_TIMEOUT)
                finally:
                    if CONFIG.read_bytes()==cfg.encode():CONFIG.write_bytes(original_config)
                    elif CONFIG.read_bytes()!=original_config:raise RuntimeError('Config.scala was changed externally; original snapshot retained in '+str(STATE))
                log(TCS[0],'\nMAKE BUILD '+design+'\nCWD: '+str(CY/'sims/verilator')+'\nCOMMAND: '+shlex.join(args)+'\nCONFIGURATION:\n'+cfg+'\n'+(folder/'build.log').read_text(errors='replace')+f'\nMAKE EXIT={code}\n')
                if code or not sim.exists():raise RuntimeError(design+' make build failed')
                for tc in TCS:log(tc,f'\nREBUILT DESIGN {design}: SIMULATOR_SHA256={sha(sim)}; build recorded in Log_{TCS[0]}.log.\n')
                shutil.rmtree(folder/'generated-src')
                for tc in TCS:
                    group=[c for c in plans if c['design']==design and c['tc']==tc]
                    for start in range(0,len(group),workers):
                        if stopped():raise InterruptedError('Stopped by user')
                        futures=[pool.submit(execute,c,sim) for c in group[start:start+workers]]
                        records.extend(f.result() for f in futures)
        completed=len(records)==len(plans)
    except BaseException as e:
        for tc in TCS:log(tc,f'\nRUN INTERRUPTED: {type(e).__name__}: {e}\n')
    finally:
        # Builds restore immediately; do not overwrite a later external edit.
        unchanged=bool(original_sources) and all(Path(p).exists() and sha(Path(p))==v for p,v in original_sources.items())
        for tc in TCS:
            rows=sorted([r for r in records if r['tc']==tc],key=lambda r:r['id'])
            table=['\n'+'='*100,'TOTAL RESULTS','='*100,'ID | Design | X | W | W bits | Pattern | Exact matches | Status | HW cycles']
            for r in rows:table.append(f'{r["id"]} | {r["design"]} | 1x{r["k"]} | {r["k"]}x{r["m"]} | {r["wb"]} | {r["pattern"]}:{r["detail"]} | {r["matches"]}/{r["elements"]} | {r["status"]} | {r["cycles"]}')
            tally=collections.Counter(r['status'] for r in rows)
            table.extend([f'TOTAL: {tally["PASS"]} PASS, {tally["FAIL"]} FAIL, {tally["TIMEOUT"]} TIMEOUT, {tally["BUILD_FAIL"]} BUILD_FAIL, {tally["ERROR"]} ERROR, {tally["STOPPED"]} STOPPED; {len(rows)}/{counts[tc]} completed.',
                          f'EXACT OUTPUT MATCHES: {sum(r["matches"] for r in rows)}/{sum(r["elements"] for r in rows)}.',
                          'PER DESIGN:'])
            for b,rf in GEOMETRIES:
                design=f'b{b}_rf{rf}'
                table.append(design+': '+str(dict(collections.Counter(r['status'] for r in rows if r['design']==design))))
            table.extend([f'Original HW/SW sources unchanged and Config.scala restored: {unchanged}',
                          'Temporary builds/binaries/vector headers/ELFs/objects removed; dated logs and suite scripts retained.',
                          'RUN COMPLETE' if completed and unchanged else 'RUN INCOMPLETE', 'END: '+now()])
            log(tc,'\n'.join(table)+'\n')
        safe=original_config is None or CONFIG.read_bytes()==original_config
        if safe:shutil.rmtree(STATE)
        else:print('Recovery state retained:',STATE,flush=True)
        config_lock.close()
        lock.close()
    return 0 if completed and unchanged and all(r['status']=='PASS' for r in records) else 1


def preflight():
    for path in [CY/'env.sh',CONFIG,SRC/'Linear-sw.c',SRC/'rocc.h',SRC/'compiler.h',SRC/'build.sh']:
        if not path.is_file():raise RuntimeError('Missing '+str(path))
    if 'packedReadCount' not in (GEN/'src/main/scala/LUMAX_Top.scala').read_text():raise RuntimeError('Fixed hardware loader not found')
    if not re.search(r'(?m)^\s*y_slice\s*=\s*1\s*,',CONFIG.read_text()):raise RuntimeError('Requires y_slice=1')
    if not re.search(r'(?m)^\s*DEBUG\s*=\s*true\s*,',CONFIG.read_text()):raise RuntimeError('Requires enabled HW cycle counters')


def environment():
    shell=('source /home/kioannou/miniforge3/etc/profile.d/conda.sh >/dev/null && cd '+shlex.quote(str(CY))+
           ' && source ./env.sh >/dev/null && command -v riscv64-unknown-elf-gcc >/dev/null && command -v verilator >/dev/null && '
           'python3 -c '+shlex.quote('import os,json; print(json.dumps(dict(os.environ)))'))
    result=subprocess.run(['bash','-c',shell],capture_output=True,text=True,timeout=120)
    if result.returncode:raise RuntimeError('Chipyard environment setup failed: '+result.stderr[-1000:])
    env=json.loads(result.stdout)
    env['PYTHONDONTWRITEBYTECODE']='1'
    return env


def report_status():
    global LOGDIR
    if (STATE/'run.json').exists():
        info=json.loads((STATE/'run.json').read_text())
        LOGDIR=Path(info['logdir'])
        print('Runner state:',STATE,'\nPID:',info['pid'])
        if (STATE/'lock').exists():
            with (STATE/'lock').open('a+') as lock:
                try:
                    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    print('Worker lock free: starting or exited. Inspect logs/temporary console.')
                except BlockingIOError:print('Worker active (filesystem lock held).')
    else:
        folders=sorted(p for p in LOGROOT.glob('run_*') if p.is_dir())
        LOGDIR=folders[-1] if folders else None
        print('No active runner state.')
    if LOGDIR is None:
        print('No saved results yet.');return
    print('Results:',LOGDIR)
    if (LOGDIR/'total_results.csv').exists():
        with (LOGDIR/'total_results.csv').open() as stream:rows=list(csv.DictReader(stream))
        print('Saved regression:',len(rows),'/180',dict(collections.Counter(r['status'] for r in rows)))
    overall=collections.Counter()
    for path in sorted(LOGDIR.glob('Log_*.log')):
        text=path.read_text(errors='replace')
        results=re.findall(r'^FINAL (?:RUN )?RESULT: (\w+)',text,re.M)
        if 'RECOVERED ORIGINAL RESULTS' in text or not results:
            results=re.findall(r'^RUN \d+/54 .*: (PASS|FAIL) \d+/\d+',text,re.M)
        overall.update(results)
        print(path.stem.removeprefix('Log_'),len(results),dict(collections.Counter(results)))
        if 'RECOVERED ORIGINAL RESULTS' in text:print('  TC01: recovered original summary; full data for 53 cases unavailable.')
    if overall:print('TOTAL:',sum(overall.values()),dict(overall))
    if (STATE/'console.log').exists():
        print('Current activity:\n'+'\n'.join((STATE/'console.log').read_text(errors='replace').splitlines()[-5:]))


def main(suite=None):
    global LOGDIR, TCS, GEOMETRIES, TIMEOUT, BUILD_TIMEOUT, MAX_CYCLES, JOBS
    parser=argparse.ArgumentParser(description=__doc__ if suite!='regression' else '180-case LUMAX regression: start | status | stop | check')
    parser.add_argument('action',choices=['start','status','stop','check','worker'])
    parser.add_argument('--workers',type=int,default=6)
    parser.add_argument('--timeout',type=int,default=7200,help='wall seconds per simulator invocation')
    parser.add_argument('--build-timeout',type=int,default=7200)
    parser.add_argument('--max-cycles',type=int,default=20000000000)
    parser.add_argument('--jobs',type=int,default=8)
    parser.add_argument('--design',choices=[f'b{b}_rf{rf}' for b,rf in GEOMETRIES],help='default: all six designs')
    if suite!='regression':parser.add_argument('--tc',nargs='+',choices=[f'TC{i:02d}' for i in range(1,12)],help='default: all TC01–TC11')
    parser.add_argument('--logdir',help=argparse.SUPPRESS)
    args=parser.parse_args()
    configure(suite or 'test_cases')
    if not 1<=args.workers<=12:parser.error('workers must be 1..12')
    if min(args.timeout,args.build_timeout,args.max_cycles,args.jobs)<=0:parser.error('timeouts, cycles and jobs must be positive')
    TIMEOUT,BUILD_TIMEOUT,MAX_CYCLES,JOBS=args.timeout,args.build_timeout,args.max_cycles,args.jobs
    if args.design:
        GEOMETRIES=[tuple(map(int,re.fullmatch(r'b(\d+)_rf(\d+)',args.design).groups()))]
    if getattr(args,'tc',None):TCS=sorted(set(args.tc))
    if args.action=='status':report_status();return 0
    if args.action=='stop':
        if STATE.exists():
            (STATE/'stop').touch()
            print('Stop requested; child processes will stop and original Config.scala will be restored.')
        else:print('No active runner state.')
        return 0
    preflight()
    if args.action=='worker':
        if not args.logdir or not STATE.is_dir():parser.error('worker is internal; use start')
        LOGDIR=Path(args.logdir)
        return worker(args.workers)
    env=environment()
    if args.action=='check':
        planned=list(cases())
        print(f'PRECHECK OK. Suite={SUITE}; {len(GEOMETRIES)} make builds, reused for all selected tests.')
        print('Planned Verilator invocations:',len(planned))
        for tc,n in sorted(collections.Counter(c['tc'] for c in planned).items()):print(tc,n)
        print('No simulation or HW build started.');return 0
    if STATE.exists():raise RuntimeError(f'Runner state already exists: {STATE}; use status/stop first.')
    LOGROOT.mkdir(parents=True,exist_ok=True)
    LOGDIR=LOGROOT/('run_'+datetime.now().astimezone().strftime('%Y%m%d_%H%M%S_%f'))
    STATE.mkdir(mode=0o700)
    LOGDIR.mkdir()
    # Write metadata before launching; it also survives an interrupted bootstrap.
    (STATE/'run.json').write_text(json.dumps(dict(pid='starting',logdir=str(LOGDIR))))
    cmd=[sys.executable,str(SELF),'worker','--workers',str(args.workers),
         '--timeout',str(TIMEOUT),'--build-timeout',str(BUILD_TIMEOUT),
         '--max-cycles',str(MAX_CYCLES),'--jobs',str(JOBS),'--logdir',str(LOGDIR)]
    if args.design:cmd+=['--design',args.design]
    if getattr(args,'tc',None):cmd+=['--tc',*args.tc]
    try:
        with (STATE/'console.log').open('w') as out:
            proc=subprocess.Popen(cmd,cwd=CY,env=env,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
    except BaseException:
        shutil.rmtree(STATE)
        LOGDIR.rmdir()
        raise
    try:(STATE/'run.json').write_text(json.dumps(dict(pid=proc.pid,logdir=str(LOGDIR))))
    except FileNotFoundError:pass  # Worker may already have failed and cleaned up.
    print('Started in background. PID:',proc.pid)
    print('You may disconnect from SSH; server shutdown/reboot interrupts the run.')
    print('Status: python3 '+str(SELF)+' status')
    print('Results:',LOGDIR)
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print(f'ERROR: {type(error).__name__}: {error}',file=sys.stderr)
        raise SystemExit(1)
