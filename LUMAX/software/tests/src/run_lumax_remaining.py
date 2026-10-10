#!/usr/bin/env python3
"""Temporary detached TC03–TC11 runner. Usage: start | status | stop | check."""
import argparse
import collections
import concurrent.futures
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
LOGDIR = SRC / 'Log'
CONFIG = GEN / 'src/main/scala/Config.scala'
SELF = Path(__file__).resolve()
STATE = Path('/tmp/lumax-remaining-state-' + str(os.getuid()))
GEOMETRIES = [(b, rf) for b in (2, 4, 8) for rf in (4, 8)]
TCS = [f'TC{i:02d}' for i in range(3, 12)]
WRITE_LOCK = threading.RLock()
STOP = threading.Event()
ROOT = None
BASE = ''
USED_SEEDS = set()

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
    if STOP.is_set(): raise InterruptedError('Stopped by user')
    with output.open('w') as out:
        proc = subprocess.Popen(args,cwd=cwd,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
        deadline=time.monotonic()+timeout
        try:
            while True:
                try: return proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    if STOP.is_set(): raise InterruptedError('Stopped by user')
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
    if STOP.is_set(): raise InterruptedError('Stopped by user')
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
        build=command(['bash','./build.sh','baremetal'],folder,folder/'build.log',180)
        out.write((folder/'build.log').read_text(errors='replace')+f'\nRISC-V BUILD EXIT={build}\n')
        if build:
            result['status']='BUILD_FAIL'
        else:
            elf=folder/'Linear-sw.riscv'
            out.write('ELF_SHA256='+sha(elf)+'\n')
            args=[str(sim),'+permissive','+max-cycles=2000000000',f'+loadmem={elf}','+permissive-off',str(elf)]
            out.write('ACTUAL VERILATOR COMMAND: '+' '.join(args)+'\nBEGIN ACTUAL VERILATOR OUTPUT\n')
            code=command(args,folder,folder/'uart.log',1800)
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
                ok=ok and len(hw)==s['m'] and hw==s['ref'] and (int(row[1]),int(row[2]))==(count,s['m']) and row[3]=='PASS'
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
    global ROOT,BASE
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    ROOT=STATE/'work'
    ROOT.mkdir()
    (STATE/'pid').write_text(str(os.getpid()))
    BASE=(SRC/'Linear-sw.c').read_text()
    os.environ['TEST_CFLAGS']='-O2 -DLUMAX_QUIET_TEST=1'
    original_config=CONFIG.read_bytes()
    (STATE/'original-config.scala').write_bytes(original_config)
    original_sources={str(p):sha(p) for p in (GEN/'src/main/scala').glob('*.scala')}
    original_sources[str(SRC/'Linear-sw.c')]=sha(SRC/'Linear-sw.c')
    plans=list(cases())
    counts=collections.Counter(c['tc'] for c in plans)
    records=[]
    completed=False
    lock=SELF.open('rb')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    for sig in [signal.SIGTERM,signal.SIGINT]:signal.signal(sig,lambda *_:STOP.set())
    signal.signal(signal.SIGHUP,signal.SIG_IGN)
    try:
        for tc in TCS:
            log(tc,f'{tc}: LUMAX CHIPYARD 1.13.0\nSTART: {now()}\nPLANNED: {counts[tc]} independent simulator invocations. Input16, weights8/4/2, RIN=1, six designs.\n'
                +'Each design built once with make and reused across every TC; Python generates/checks integer references only.\n'
                +'No HW logic edits. Config.scala geometry is temporarily selected during the six initial builds and restored immediately after each.\n'
                +'No dimension padding. DMA-readable buffers have nonzero guard bytes; output guards begin immediately after the logical output.\n'
                +'Packed bytes use ceiling allocation for fractional-byte tails; unused packed bits are nonzero.\n'
                +'TC11 uses six operations in one RISC-V ELF/Verilator boot, with shared buffers and no accelerator reset.\n'
                +'Full X/W printed for up to 2048 weights; larger matrices include seeds and hashes, plus every Python/HW output.\n'
                +'SOURCE SHA256: '+json.dumps(original_sources,sort_keys=True)+'\n')
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            for b,rf in GEOMETRIES:
                if STOP.is_set():raise InterruptedError('Stopped by user')
                design=f'b{b}_rf{rf}'
                folder=ROOT/design
                folder.mkdir()
                cfg=original_config.decode()
                for key,value in [('x_slice',b),('Mem_row_factor',rf)]:
                    cfg,n=re.subn(r'(?m)^(\s*'+key+r'\s*=\s*)\d+',lambda mt:mt[1]+str(value),cfg)
                    assert n==1
                sim=folder/'simulator-LUMAXROcketConfig'
                args=['make','-j8','CONFIG=LUMAXROcketConfig','sim='+str(sim),'gen_dir='+str(folder/'generated-src')]
                print('BUILD',design,flush=True)
                try:
                    CONFIG.write_text(cfg)
                    code=command(args,CY/'sims/verilator',folder/'build.log',2400)
                finally:CONFIG.write_bytes(original_config)
                log('TC03','\nMAKE BUILD '+design+'\nCOMMAND: '+' '.join(args)+'\nCONFIGURATION:\n'+cfg+'\n'+(folder/'build.log').read_text(errors='replace')+f'\nMAKE EXIT={code}\n')
                if code or not sim.exists():raise RuntimeError(design+' initial make build failed')
                for tc in TCS:log(tc,f'\nREBUILT DESIGN {design}: SIMULATOR_SHA256={sha(sim)}; make completed once, see Log_TC03.log.\n')
                # Keep the executable, remove generated build intermediates immediately.
                shutil.rmtree(folder/'generated-src')
                for tc in TCS:
                    group=[c for c in plans if c['design']==design and c['tc']==tc]
                    for start in range(0,len(group),workers):
                        if STOP.is_set():raise InterruptedError('Stopped by user')
                        futures=[pool.submit(execute,c,sim) for c in group[start:start+workers]]
                        records.extend(f.result() for f in futures)
        completed=len(records)==len(plans)
    except BaseException as e:
        for tc in TCS:log(tc,f'\nRUN INTERRUPTED: {type(e).__name__}: {e}\n')
    finally:
        CONFIG.write_bytes(original_config)
        unchanged=all(sha(Path(p))==v for p,v in original_sources.items())
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
                          'Temporary builds/binaries/vector headers/ELFs/objects removed; runner script retained; no separate summary files.',
                          'RUN COMPLETE' if completed and unchanged else 'RUN INCOMPLETE', 'END: '+now()])
            log(tc,'\n'.join(table)+'\n')
        lock.close()
        shutil.rmtree(STATE)

def preflight():
    for path in [CY/'env.sh',CONFIG,SRC/'Linear-sw.c',SRC/'rocc.h',SRC/'build.sh']:
        if not path.is_file():raise RuntimeError('Missing '+str(path))
    if 'packedReadCount' not in (GEN/'src/main/scala/LUMAX_Top.scala').read_text():raise RuntimeError('Fixed hardware loader not found')
    if not re.search(r'(?m)^\s*y_slice\s*=\s*1\s*,',CONFIG.read_text()):raise RuntimeError('Requires y_slice=1')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['start','status','stop','check','worker'])
    parser.add_argument('--workers',type=int,default=6)
    args=parser.parse_args()
    if not 1<=args.workers<=12:parser.error('workers must be 1..12')
    if args.action=='worker':
        preflight()
        worker(args.workers)
        return
    if args.action=='check':
        preflight()
        planned=list(cases())
        print('PRECHECK OK. Initial make builds:6, reused across all TCs.')
        print('Planned Verilator invocations:',len(planned))
        for tc,n in sorted(collections.Counter(c['tc'] for c in planned).items()):print(tc,n)
        print('No simulation or HW build started.')
        return
    if args.action in ['status','stop']:
        if not (STATE/'pid').exists():
            print('No active runner PID. Check Log_TC03.log through Log_TC11.log for completion/results.')
            return
        pid=int((STATE/'pid').read_text())
        try:os.kill(pid,0)
        except ProcessLookupError:
            print('Runner is not running. Temporary recovery information:',STATE)
            return
        if args.action=='stop':
            os.kill(pid,signal.SIGTERM)
            print('Stop requested; runner will stop its child processes, restore Config.scala and clean temporary files.')
            return
        print('Runner active, PID',pid)
        for tc in TCS:
            path=LOGDIR/f'Log_{tc}.log'
            if path.exists():
                text=path.read_text(errors='replace')
                results=re.findall(r'^FINAL RESULT: (\w+)',text,re.M)
                planned=re.search(r'^PLANNED: (\d+)',text,re.M)
                print(tc,f'{len(results)}/{planned[1] if planned else "?"}',dict(collections.Counter(results)))
        print('Current activity:')
        if (STATE/'console.log').exists():print('\n'.join((STATE/'console.log').read_text(errors='replace').splitlines()[-5:]))
        return
    preflight()
    if STATE.exists():raise RuntimeError(f'Runner state already exists: {STATE}; use status/stop first.')
    existing=[str(LOGDIR/f'Log_{tc}.log') for tc in TCS if (LOGDIR/f'Log_{tc}.log').exists()]
    if existing:raise RuntimeError('Will not overwrite existing TC logs: '+', '.join(existing))
    STATE.mkdir(mode=0o700)
    shell='source /home/kioannou/miniforge3/etc/profile.d/conda.sh && cd '+shlex.quote(str(CY))+' && source ./env.sh && exec python3 '+shlex.quote(str(SELF))+' worker --workers '+str(args.workers)
    with (STATE/'console.log').open('w') as out:
        proc=subprocess.Popen(['bash','-c',shell],stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
    (STATE/'pid').write_text(str(proc.pid))
    print('Started in the background. PID:',proc.pid)
    print('You can disconnect from SSH. Runs continue while this server stays powered on.')
    print('Status: python3 '+str(SELF)+' status')
    print('Results:',LOGDIR,'/ Log_TC03.log ... Log_TC11.log')
    print('On completion temporary files are deleted; the script and nine TC logs remain.')

if __name__=='__main__':
    try:main()
    except Exception as error:
        print(f'ERROR: {type(error).__name__}: {error}',file=sys.stderr)
        raise SystemExit(1)
