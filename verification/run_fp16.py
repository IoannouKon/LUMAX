#!/usr/bin/env python3
"""Verify build-time INT16/FP16 variants using real make-built Verilator RTL.

One simulator per mode/design; reused for W8/W4/W2. Each ELF performs several
operations through the original Linear-sw.c helpers. Python reference outputs
stay on the host; both HW and independently calculated C outputs are checked.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import random
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import struct
import sys
import tempfile
import threading
import time

sys.dont_write_bytecode = True
import run_llama_shapes as hw

ROOT = Path(__file__).resolve().parent


def half_units(raw):
    """Exact finite half value in units of 2^-24; None denotes NaN/Inf."""
    exponent, fraction = (raw >> 10) & 31, raw & 1023
    if exponent == 31:
        return None
    magnitude = ((1024 + fraction) << (exponent - 1)) if exponent else fraction
    return -magnitude if raw & 0x8000 else magnitude


def round_fp32(units):
    if not units:
        return 0
    sign, magnitude = (0x80000000 if units < 0 else 0), abs(units)
    # Integer quotient/remainder avoid an intermediate host float64 rounding.
    exponent = magnitude.bit_length() - 1 - 24
    shift = magnitude.bit_length() - 24
    if shift > 0:
        q, r = divmod(magnitude, 1 << shift)
        q += (r * 2 > (1 << shift)) or (r * 2 == (1 << shift) and q & 1)
    else:
        q = magnitude << -shift
    if q == 1 << 24:
        q >>= 1
        exponent += 1
    return sign | ((exponent + 127) << 23) | (q & 0x7fffff)


def reference(x, weights, m, fp16):
    result = []
    for col in range(m):
        if not fp16:
            result.append(sum(v * weights[j * m + col] for j, v in enumerate(x)) & 0xffffffff)
            continue
        exact, nan, infinities = 0, False, set()
        for j, raw in enumerate(x):
            weight = weights[j * m + col]
            units = half_units(raw)
            if units is None:
                if raw & 1023 or weight == 0:
                    nan = True
                else:
                    infinities.add(bool(raw & 0x8000) ^ (weight < 0))
            else:
                exact += units * weight
        result.append(0x7fc00000 if nan or len(infinities) == 2 else
                      (0xff800000 if True in infinities else 0x7f800000) if infinities else round_fp32(exact))
    return result


def vectors(wb, fp16, seed, quick=False, shapes=None, edge_cases=True):
    rng = random.Random(seed)
    low, high = -(1 << (wb - 1)), (1 << (wb - 1)) - 1
    def activation():
        if not fp16:
            return rng.randint(-32768, 32767)
        bits = rng.randrange(65536)
        return bits ^ 0x400 if bits & 0x7c00 == 0x7c00 else bits
    cases = []
    def add(name, k, m, x=None, weights=None):
        x = x if x is not None else [activation() for _ in range(k)]
        weights = weights if weights is not None else [rng.randint(low, high) for _ in range(k*m)]
        cases.append(dict(name=name, k=k, m=m, x=x, weights=weights,
                          expected=reference(x, weights, m, fp16)))
    shapes = shapes or [(4,4),(12,20),(28,36),(60,12),(65,5),(129,7),(513,4),(1024,4)]
    for k,m in (shapes[:2] if quick else shapes):
        add(f'random_{k}x{m}',k,m)
    if not edge_cases:
        return cases
    add('zero_activations',12,5,x=[0]*12)
    add('zero_weights',12,5,weights=[0]*60)
    if fp16:
        add('finite_extremes',8,8,x=[0,0x8000,1,0x8001,0x400,0x7bff,0xfbff,0x3c01])
        add('exact_cancellation',5,4,x=[0x7bff,0xfbff,0x7bff,0xfbff,1],weights=[1,-1,low,high]*5)
        add('round_ties_even',4,8,x=[0x3c00,1,1,1],weights=[1,1,1,1,-1,-1,-1,-1]+[int(j<col) if col<4 else -int(j<col-4) for j in range(3) for col in range(8)])
        if wb == 8 and not quick:
            # Exact sums immediately above/below a FP32 midpoint; converting via
            # host float64 first would lose the final single subnormal unit.
            add('wide_round_no_double_rounding',514,2,x=[0xf800]*512+[0x5800,1],
                weights=[-128,-128]*512+[1,1,1,-1])
        if not quick:
            k=1<<wb
            add('every_weight_code',k,4,weights=[low+(j+col)%k for j in range(k) for col in range(4)])
            add('maximum_finite_sum',1024,4,x=[0x7bff]*1024,weights=[low,high,1,-1]*1024)
        for name,bits in [('positive_infinity',0x7c00),('negative_infinity',0xfc00),('quiet_nan',0x7e01),('signaling_nan',0x7c01)]:
            add(name,1,8,x=[bits],weights=[0,1,-1,low,high,1,-1,0])
        add('infinity_cancellation',2,8,x=[0x7c00,0xfc00],weights=[1,1,-1,0,1,-1,low,high,1,-1,1,1,0,-1,low,high])
        add('finite_after_specials',6,5)
    else:
        add('signed_extremes',4,8,x=[-32768,32767,0,-1],weights=[low,high,0,1,-1,low,high,1]*4)
    return cases


def source(base, cases, wb, b, rf, fp16):
    text=base.split('int main() {',1)[0]
    for key,value in dict(IN_BITS=16,W_BITS=wb,XS=b,YS=1,Mem_row_factor=rf).items():
        text,count=re.subn(r'(?m)^(#define\s+'+key+r'\s+)\d+',lambda m:m[1]+str(value),text)
        if count!=1:raise ValueError('Missing C setting '+key)
    text+='\n#undef printf\n'
    for i,case in enumerate(cases):
        packed=[0]*((case['k']*case['m']*wb+7)//8)
        for col in range(case['m']):
            for j in range(case['k']):
                index=col*case['k']+j
                packed[index*wb//8] |= (case['weights'][j*case['m']+col]&((1<<wb)-1)) << (index*wb%8)
        for ctype,name,values in [('input_t',f'x_{i}',case['x']),('uint8_t',f'w_{i}',packed)]:
            text+=f'static const {ctype} {name}[] __attribute__((aligned(8))) = {{'+','.join(map(str,values))+'};\n'
    maxk=max(c['k'] for c in cases);maxm=max(c['m'] for c in cases)
    maxw=max((c['k']*c['m']*wb+7)//8 for c in cases)
    text+=f'''
static uint8_t ax[{maxk*2+24}] __attribute__((aligned(8)));
static uint8_t aw[{maxw+24}] __attribute__((aligned(8)));
static uint8_t ao[{maxm*4+24}] __attribute__((aligned(8)));
static unsigned long previous_combined=0;
static int safe(const uint8_t *a,int size,int used) {{
  for(int i=0;i<8;i++) if(a[i]!=0xa5) return 0;
  for(int i=8+used;i<size;i++) if(a[i]!=0xa5) return 0;
  return 1;
}}
static int run_case(int id,int k,int m,const input_t *x,const uint8_t *w,int bytes) {{
  memset(ax,0xa5,sizeof(ax));memset(aw,0xa5,sizeof(aw));memset(ao,0xa5,sizeof(ao));
  memcpy(ax+8,x,k*2);memcpy(aw+8,w,bytes);memset(ao+8,0x5a,m*4);
  QuantizedTensor_input tx={{.q=(input_t*)(ax+8)}};
  QuantizedTensor_weight tw={{.q=(weight_t*)(aw+8)}};
  output_t *out=(output_t*)(ao+8),sw[{maxm}];
  matmul_sw_uniform(sw,&tx,&tw,k,m,1);
  preload_hw(k,1,m,x_slice_max*RF,y_slice,tx.q,tw.q,out);
  unsigned long core_start=rdcycle();
  matmul_hw_uniform(out,&tx,&tw,k,m,x_slice_max*RF,y_slice,1);
  unsigned long core=rdcycle()-core_start,pc[6];
  for(int i=0;i<6;i++) pc[i]=read_rd();
  for(int i=6;i<32;i++) (void)read_rd();
  unsigned long cumulative=pc[5];pc[5]-=previous_combined;previous_combined=cumulative;
  int guards=safe(ax,sizeof(ax),k*2)&&safe(aw,sizeof(aw),bytes)&&safe(ao,sizeof(ao),m*4)&&!memcmp(ax+8,x,k*2)&&!memcmp(aw+8,w,bytes);
  int same=!memcmp(out,sw,m*4);
  printf("RESULT %d GUARDS %d C_MATCH %d CORE %lu PC %lu %lu %lu %lu %lu %lu\\n",id,guards,same,core,pc[0],pc[1],pc[2],pc[3],pc[4],pc[5]);
  /* Lossless encoding of every actual DMA output byte; no output hashes or
     Python answers substitute for hardware values. Avoid costly printf/word. */
  static const char alphabet[]="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  char encoded[{((maxm*4+2)//3)*4+1}];int at=0;
  const uint8_t *raw=(const uint8_t*)out;
  for(int i=0;i<m*4;i+=3) {{
    int remain=m*4-i;
    uint32_t word=(uint32_t)raw[i]<<16;
    if(remain>1) word|=(uint32_t)raw[i+1]<<8;
    if(remain>2) word|=raw[i+2];
    encoded[at++]=alphabet[(word>>18)&63];encoded[at++]=alphabet[(word>>12)&63];
    encoded[at++]=remain>1?alphabet[(word>>6)&63]:'=';
    encoded[at++]=remain>2?alphabet[word&63]:'=';
  }}
  encoded[at]=0;
  printf("HW %d %s\\n",id,encoded);
  return !guards||!same;
}}
int main(void) {{int failed=0;
'''
    for i,c in enumerate(cases):
        text+=f'failed |= run_case({i},{c["k"]},{c["m"]},x_{i},w_{i},sizeof(w_{i}));\n'
    return text+'printf("TEST_DONE %d\\n",failed);return failed;}\n'


def build_key(configuration):
    digest=hashlib.sha256(configuration.encode())
    paths=[p for p in sorted((hw.GEN/'src/main/scala').glob('*.scala')) if p!=hw.CONFIG]
    paths += [hw.CY/'build.sbt',hw.CY/'generators/chipyard/src/main/scala/config/LUMAXConfigs.scala']
    for p in paths:
        digest.update(p.name.encode());digest.update(p.read_bytes())
    return digest.hexdigest()


def run(args):
    work=Path(tempfile.mkdtemp(prefix='lumax-fp16-check-'))
    logdir=ROOT/'logs'/'fp16';logdir.mkdir(parents=True,exist_ok=True)
    logfile=logdir/('run_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.log')
    mutex=threading.Lock()
    def log(message):
        with mutex:
            with logfile.open('a') as f:f.write(str(message)+'\n')
            print(message,flush=True)
    def command(cmd,folder,output,timeout,env):
        log('COMMAND: '+shlex.join(list(map(str,cmd))))
        with output.open('w') as out:
            proc=subprocess.Popen(cmd,cwd=folder,env=env,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
            try:return proc.wait(timeout=timeout)
            except BaseException:
                os.killpg(proc.pid,signal.SIGTERM)
                try:proc.wait(timeout=10)
                except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
                raise
    env=hw.environment();runtime=work/'runtime';runtime.mkdir();env['XDG_RUNTIME_DIR']=str(runtime)
    env['SBT_OPTS']=(env.get('SBT_OPTS') or f'-Dsbt.ivy.home={hw.CY}/.ivy2 -Dsbt.global.base={hw.CY}/.sbt -Dsbt.boot.directory={hw.CY}/.sbt/boot/')+' -Dsbt.server.forcestart=true -Dsbt.server.autostart=false -Dsbt.supershell=false'
    base=(hw.SRC/'Linear-sw.c').read_text()
    original=hw.CONFIG.read_bytes()
    modes=[True,False] if args.mode=='both' else [args.mode=='fp16']
    log('LUMAX BUILD-TIME FP16 VERIFICATION\nLOG: '+str(logfile)+'\nWORK: '+str(work))
    log('FP16: exact finite dot product, one FP32 round-nearest/ties-even; canonical NaN; no Python output arrays in ELF.')
    log('SOURCE SHA256: '+json.dumps({str(p):hw.sha(p) for p in sorted((hw.GEN/'src/main/scala').glob('*.scala'))}|{str(hw.SRC/'Linear-sw.c'):hw.sha(hw.SRC/'Linear-sw.c')},sort_keys=True))
    results=[]
    boot_count=[0]
    try:
        with hw.CONFIG.open('rb') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures=[]
                for fp16 in modes:
                    for design in args.design:
                        b,rf=map(int,re.fullmatch(r'b(\d+)_rf(\d+)',design).groups())
                        label=('fp16' if fp16 else 'int16')+'_'+design
                        folder=work/label;folder.mkdir()
                        text=original.decode()
                        for key,value in [('x_slice',b),('Mem_row_factor',rf),('FP16_ACTIVATIONS','true' if fp16 else 'false')]:
                            text,count=re.subn(r'(?m)^(\s*'+key+r'\s*=\s*)(?:\d+|true|false)',lambda m:m[1]+str(value),text)
                            if count!=1:raise ValueError('Missing hardware setting '+key)
                        snapshot=folder/'Config.scala';snapshot.write_text(text)
                        sim=folder/'simulator-LUMAXROcketConfig'
                        key=build_key(text)
                        cached=args.cache/key if args.cache else None
                        manifest=json.loads((cached/'manifest.json').read_text()) if cached and (cached/'manifest.json').is_file() else None
                        if manifest and manifest.get('key')==key and (cached/'simulator').is_file() and hw.sha(cached/'simulator')==manifest.get('sha256'):
                            shutil.copy2(cached/'simulator',sim)
                            log('REUSE verified build '+label+' key='+key)
                        else:
                            hw.CONFIG.write_text(text)
                            try:
                                code=command(['make','-j'+str(args.jobs),'CONFIG=LUMAXROcketConfig','sim='+str(sim),'gen_dir='+str(folder/'generated-src')],hw.CY/'sims/verilator',folder/'make.log',args.build_timeout,env)
                            finally:
                                if hw.CONFIG.read_bytes()==text.encode():hw.CONFIG.write_bytes(original)
                                elif hw.CONFIG.read_bytes()!=original:raise RuntimeError('Configuration changed externally; not overwritten')
                            if code or not sim.is_file():
                                log((folder/'make.log').read_text(errors='replace')[-8000:]);raise RuntimeError('Make failed: '+label)
                            if build_key(text)!=key:raise RuntimeError('Hardware sources changed during make')
                            if cached:
                                cached.mkdir(parents=True,exist_ok=True);shutil.copy2(sim,cached/'simulator')
                                (cached/'manifest.json').write_text(json.dumps(dict(key=key,sha256=hw.sha(sim))))
                        log('BUILD PASS '+label+' SIMULATOR_SHA256='+hw.sha(sim))
                        for wb in (8,4,2):
                            initial=secrets.randbits(64)
                            cases=vectors(wb,fp16,initial,True) if args.quick else vectors(wb,fp16,initial,shapes=[(n,n) for n in sorted(set(args.sizes))],edge_cases=False)
                            def execute(wb=wb,cases=cases,fp16=fp16,b=b,rf=rf,label=label,sim=sim,snapshot=snapshot,initial=initial):
                                records=[]
                                batches=[cases] if args.quick else [[c] for c in cases]
                                for stage,batch in enumerate(batches):
                                    casefolder=work/(label+'_w'+str(wb)+'_stage'+str(stage));casefolder.mkdir()
                                    text=source(base,batch,wb,b,rf,fp16)
                                    (casefolder/'Linear-sw.c').write_text(text)
                                    for filename in ('rocc.h','compiler.h','build.sh'):shutil.copy2(hw.SRC/filename,casefolder/filename)
                                    caseenv=dict(env,TEST_CFLAGS=f'-O2 -DLUMAX_FP16_ACTIVATIONS={int(fp16)}',LUMAX_CONFIG_PATH=str(snapshot))
                                    code=command(['bash','./build.sh','baremetal'],casefolder,casefolder/'build.log',600,caseenv)
                                    if code:
                                        log((casefolder/'build.log').read_text());raise RuntimeError('RISC-V compile failed')
                                    elf=casefolder/'Linear-sw.riscv'
                                    log(f'ELF {label} W{wb} stage={stage} seed={initial} SHA256={hw.sha(elf)}; operand arrays only; Python references stay on host')
                                    started=time.monotonic()
                                    with mutex:boot_count[0]+=1
                                    code=command([str(sim),'+permissive','+max-cycles='+str(args.max_cycles),'+loadmem='+str(elf),'+permissive-off',str(elf)],casefolder,casefolder/'uart.log',args.timeout,caseenv)
                                    uart=(casefolder/'uart.log').read_text(errors='replace')
                                    parsed={int(i):(int(safe),int(match),int(core),list(map(int,pc.split()))) for i,safe,match,core,pc in re.findall(r'^RESULT (\d+) GUARDS (\d) C_MATCH (\d) CORE (\d+) PC ((?:\d+ ){5}\d+)$',uart,re.M)}
                                    observed={}
                                    for i,data in re.findall(r'^HW (\d+) ([A-Za-z0-9+/=]+)$',uart,re.M):
                                        raw=base64.b64decode(data,validate=True)
                                        observed[int(i)]=list(struct.unpack('<'+'I'*(len(raw)//4),raw))
                                    complete=code==0 and uart.splitlines().count('TEST_DONE 0')==1 and len(parsed)==len(batch)
                                    passed_batch=True
                                    for i,c in enumerate(batch):
                                        guard,match,core,pc=parsed.get(i,(0,0,0,[0]*6))
                                        cycles=sum(pc[j] for j in (0,1,4,5))
                                        passed=complete and guard==match==1 and cycles>0 and observed.get(i)==c['expected']
                                        row=dict(mode='FP16' if fp16 else 'INT16',design=label.split('_',1)[1],wb=wb,case=c['name'],k=c['k'],m=c['m'],status='PASS' if passed else 'FAIL',cycles=cycles,pc=pc,core=core,hw=observed.get(i),c_reference_matches=match==1,python=c['expected'])
                                        log('CASE RESULT: '+json.dumps(row,sort_keys=True));records.append(row)
                                        passed_batch &= passed
                                    if not complete:log('INCOMPLETE UART: '+uart[-5000:])
                                    log(f'BOOT {label} W{wb} seconds={time.monotonic()-started:.3f} EXIT={code}')
                                    if not passed_batch:
                                        log(f'SKIP larger shapes after failed {label} W{wb} stage={stage}')
                                        break
                                return records
                            futures.append(pool.submit(execute))
                for f in as_completed(futures):results.extend(f.result())
        failed=sum(r['status']!='PASS' for r in results)
        log('TOTAL: '+json.dumps(dict(PASS=len(results)-failed,FAIL=failed,logical_tests=len(results),verilator_boots=boot_count[0])))
        log('CONFIG RESTORED: '+str(hw.CONFIG.read_bytes()==original))
        if failed:raise RuntimeError(f'{failed} failed cases; inspect '+str(logfile))
        shutil.rmtree(work)
        log('RUN COMPLETE. Temporary builds/ELFs/vectors removed. Only this log is retained.')
        return 0
    except BaseException:
        log('RUN ERROR; diagnostic work retained at '+str(work))
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--design',nargs='+',choices=hw.DESIGNS,default=hw.DESIGNS)
    p.add_argument('--mode',choices=['both','int16','fp16'],default='both')
    p.add_argument('--quick',action='store_true')
    p.add_argument('--sizes',nargs='+',type=int,default=[32,128],help='Square shapes, smallest first; larger shapes require the previous shape to pass')
    p.add_argument('--cache',type=Path,help='Optional verified simulator cache; invalidated by RTL/configuration changes')
    p.add_argument('--jobs',type=int,default=8)
    p.add_argument('--workers',type=int,default=3)
    p.add_argument('--timeout',type=int,default=3600)
    p.add_argument('--build-timeout',type=int,default=7200)
    p.add_argument('--max-cycles',type=int,default=100000000000)
    args=p.parse_args()
    if min(args.jobs,args.workers,args.timeout,args.build_timeout,args.max_cycles)<=0:p.error('limits must be positive')
    config=hw.CONFIG.read_text()
    maximum=min(int(re.search(r'(?m)^\s*'+key+r'\s*=\s*(\d+)',config)[1]) for key in ('Cin','Cout'))
    if any(n<1 or n>maximum for n in args.sizes):p.error('sizes must fit the configured Cin and Cout')
    return run(args)


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print(f'ERROR: {type(error).__name__}: {error}',file=sys.stderr)
        raise SystemExit(1)
