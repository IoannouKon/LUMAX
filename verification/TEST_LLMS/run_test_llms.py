#!/usr/bin/env python3
"""Actual LUMAX weight-loader RTL verification and TEST_LLMS sweep.

python3 run_test_llms.py start | status | resume | stop
Recorded Cheetara run, 2026-11-10 to 2026-11-12: 204/204 PASS,
including 144 LLM cases and 60 bootstrap/configuration gates; recorded worker
elapsed time 44.13 hours. The archived run predates optional resource flags.
Reruns snapshot the current Chipyard sources and may have different timing.
Uses existing OLD_VERIFICATION helpers and the editable production C helpers.
The checked-in cycles.csv also carries the saved edited-Gemmini reference
cycles and operand identities; no separate baseline file is needed.
Small INT16/FP16 sequential matmuls gate the entire sweep. Every full-capacity
configuration also runs its own small gate before the 36 TEST_LLMS matmuls.
All numerical references run only on host Python. The RISC-V test calls the
production Linear-sw.c RoCC helpers, exports actual FP32/INT32 words in binary,
and contains no expected output array or software matrix multiplication call.
No overall deadline; per-command timeouts detect hangs. Builds use private
source/configuration snapshots and real make/Verilator; live Config.scala is
never edited. Completed builds are reused by content hash.
"""
# -------------------- EDIT HARDWARE CONFIGURATIONS HERE -------------------
CONFIGURATIONS = [(6,4,8,8),(8,4,8,8),(16,4,4,8),(16,4,8,8)] # B,RF,YS,IDs
WEIGHTS = (8,4,2)
MAKE_JOBS = 4
BUILD_TIMEOUT = 14400
SIM_TIMEOUT = 14400
SMALL_SHAPES = [(2,16,9),(5,33,17),(3,65,9),(2,129,33)]
DESIGN_GATE_SHAPES = [(2,17,9),(5,33,17),(3,65,9)]
# -------------------------------------------------------------------------
import argparse,csv,fcntl,hashlib,importlib.util,json,os,random,re,shlex,shutil,signal,struct,subprocess,sys,tempfile,time,traceback
from collections import Counter
from datetime import datetime
from pathlib import Path
sys.dont_write_bytecode=True
SELF=Path(__file__).resolve();ROOT=SELF.parent;VERIFY=ROOT.parent
sys.path.insert(0,str(VERIFY/'OLD_VERIFICATION'))
import run_fp16 as fp
import run_llama_shapes as hw
CY=hw.CY;GEN=hw.GEN
GEM=ROOT/'cycles.csv'
STATE=Path('/tmp')/f'lumax-weight-loader-rtl-{os.getuid()}'
CACHE=Path.home()/'.cache/lumax-weight-loader-rtl'
STOP=False

def now():return datetime.now().astimezone().isoformat()
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def atomic(path,obj):
 temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(obj,indent=2)+'\n');temp.replace(path)
def active(state):
 with (state/'worker.lock').open('a+') as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);return False
  except BlockingIOError:return True

def environment():
 e=os.environ.copy()
 e['PATH']=':'.join(str(CY/p) for p in ['.conda-env/bin','.conda-env/riscv-tools/bin','.conda-env/lib/jvm/bin'])+':'+e['PATH']
 e.update(RISCV=str(CY/'.conda-env/riscv-tools'),JAVA_HOME=str(CY/'.conda-env/lib/jvm'),CHISEL_FIRTOOL_PATH=str(CY/'.conda-env/riscv-tools/bin'),VERILATOR_BIN=str(CY/'.conda-env/bin/verilator_bin'),PYTHONDONTWRITEBYTECODE='1')
 e.pop('VERILATOR_ROOT',None);return e

def record(config,shape,wb,phase,index,mode='fp16',original=None):
 b,rf,ys,ids=config;r,k,m=shape
 name=f'b{b}_rf{rf}_ys{ys}_id{ids}_{mode}'
 item=dict(design=name,b=b,rf=rf,y_slice=ys,dma_ids=ids,mode=mode,phase=phase,case=f'{phase}{index:02d}',r=r,k=k,m=m,wb=wb,status='PENDING')
 for label in ['x','w']:
  item['seed_'+label]=int(original['seed_'+label]) if original else int.from_bytes(hashlib.sha256(f'weight-loader-gate:{wb}:{r}:{k}:{m}:{mode}:{label}'.encode()).digest()[:8],'little')
  if original:item[label+'_expected_sha256']=original[label+'_sha256']
 if original:item['case']=original['case'];item['gemmini_active']=int(original['controller_active_cycles'])
 return item

def operands(item):
 rx=random.Random(item['seed_x']);rw=random.Random(item['seed_w']);floating=item['mode']=='fp16'
 x=[rx.randrange(65536) if floating else rx.randint(-32768,32767) for _ in range(item['r']*item['k'])]
 if floating:x=[v^0x400 if v&0x7c00==0x7c00 else v for v in x]
 w=[rw.randint(-(1<<(item['wb']-1)),(1<<(item['wb']-1))-1) for _ in range(item['k']*item['m'])]
 if not any(v&0x7fff if floating else v for v in x):x[0]=0x3c00 if floating else 1
 if not any(w):w[0]=-1
 for label,data in [('x',x),('w',w)]:
  digest=hashlib.sha256(json.dumps(data).encode()).hexdigest()
  if item.get(label+'_expected_sha256',digest)!=digest:raise RuntimeError('Gemmini operand replay hash mismatch')
  item[label+'_sha256']=digest
 expected=[]
 for row in range(item['r']):expected.extend(fp.reference(x[row*item['k']:(row+1)*item['k']],w,item['m'],floating))
 packed=bytearray((item['k']*item['m']*item['wb']+7)//8)
 for col in range(item['m']):
  for j in range(item['k']):
   bit=(col*item['k']+j)*item['wb'];packed[bit//8]|=(w[j*item['m']+col]&((1<<item['wb'])-1))<<(bit%8)
 return x,packed,expected

def test_source(base,items,values):
 b,rf,ys=(items[0][k] for k in ['b','rf','y_slice']);wb=items[0]['wb']
 text=base.split('int main() {',1)[0]
 for key,value in dict(IN_BITS=16,W_BITS=wb,XS=b,YS=ys,Mem_row_factor=rf,Debug=0).items():
  text,count=re.subn(r'(?m)^(#define\s+'+key+r'\s+)\d+',lambda m:m[1]+str(value),text)
  if count!=1:raise ValueError('Missing C define '+key)
 text+='\n#undef printf\n#include <unistd.h>\n'
 for i,(item,(x,w,expected)) in enumerate(zip(items,values)):
  for name,ctype,data,const in [('x','input_t',x,True),('w','uint8_t',w,True),('o','output_t',[0x5a5a5a5a]*len(expected),False)]:
   text+=f'static {"const " if const else ""}struct __attribute__((packed,aligned(8))) {{uint8_t pre[8];{ctype} data[{len(data)}];uint8_t post[16];}} {name}{i}='+'{{'+','.join(['165']*8)+'},{'+','.join(map(str,data))+'},{'+','.join(['165']*16)+'}};\n'
 text+='''
static int boundary(const uint8_t *p,const uint8_t *q){for(int j=0;j<8;j++)if(p[j]!=165)return 0;for(int j=0;j<16;j++)if(q[j]!=165)return 0;return 1;}
int main(void){int failed=0;unsigned long previous_combined=0;
'''
 for i,item in enumerate(items):
  r,k,m=(item[t] for t in ['r','k','m']);block=b*rf*(1<<(8-wb));n=r*m
  text+=f'''{{
 QuantizedTensor_input tx={{.q=(input_t*)x{i}.data}};
 QuantizedTensor_weight tw={{.q=(weight_t*)w{i}.data}};
 output_t *out=o{i}.data;
 preload_hw({k},{r},{m},x_slice_max*RF,y_slice,tx.q,tw.q,out);
 load_w(tw.q,{block},{(block+8//wb-1)//(8//wb)},{(k+block-1)//block});
 unsigned long begin=rdcycle();
 matmul_hw_uniform(out,&tx,&tw,{k},{m},x_slice_max*RF,y_slice,{r});
 unsigned long core=rdcycle()-begin,pc[6];for(int j=0;j<6;j++)pc[j]=read_rd();
 for(int j=6;j<32;j++)(void)read_rd();
 unsigned long combined=pc[5];pc[5]-=previous_combined;previous_combined=combined;
 int safe=boundary(x{i}.pre,x{i}.post)&&boundary(w{i}.pre,w{i}.post)&&boundary(o{i}.pre,o{i}.post);
 printf("RESULT {i} GUARDS %d CORE %lu PC %lu %lu %lu %lu %lu %lu\\n",safe,core,pc[0],pc[1],pc[2],pc[3],pc[4],pc[5]);
 printf("OUTPUT_BITS {i} {n}\\n");fflush(stdout);
 write(1,out,{n}*4);write(1,"\\n",1);
 failed|=!safe;
}}
'''
 return text+'printf("TEST_DONE %d\\n",failed);return failed;}\n'

class Runner:
 def __init__(self,state):
  self.state=state;self.options=json.loads((state/'options.json').read_text());self.output=Path(self.options['output']);self.records=self.options['records']
  self.env=environment();self.started=time.monotonic();self.phase='starting';self.current='';self.child=None
  self.elapsed_before=0
  self.source=state/'source';self.base=(self.source/'software/Linear-sw.c').read_text()
 def restore(self):
  checkpoint=json.loads((self.state/'status.json').read_text())
  if checkpoint['status']!='RUNNING' or (self.state/'stop.request').exists():
   raise RuntimeError('Resume requires an interrupted run without a stop request')
  for name,digest in self.options['source_sha256'].items():
   if sha(self.source/name)!=digest:
    raise RuntimeError(f'Frozen source changed: {name}')
  if sha(self.state/'chipyard.jar')!=self.options['base_assembly_sha256']:
   raise RuntimeError('Frozen Chipyard assembly changed')

  identity=('design','phase','case','mode','wb','r','k','m','seed_x','seed_w')
  records={tuple(item[field] for field in identity):item for item in self.records}
  with (self.output/'verilator.log').open() as log:
   for line in log:
    _,marker,payload=line.partition('] RESULT ')
    if not marker or not line.startswith('[') or not line.endswith('\n'):
     continue
    saved=json.loads(payload)
    item=records.get(tuple(saved[field] for field in identity))
    if item is None or saved['status'] not in ('PASS','FAIL'):
     raise RuntimeError('Result log does not match the frozen test plan')
    if item.get('gemmini_active')!=saved.get('gemmini_active'):
     raise RuntimeError('Saved Gemmini cycle count differs from the frozen test plan')
    for label in ('x','w'):
     expected=item.get(label+'_expected_sha256')
     if expected and saved[label+'_sha256']!=expected:
      raise RuntimeError(f'Saved {label} operand hash differs from the frozen test plan')
    item.update(saved)

  restored=Counter(item['status'] for item in self.records)
  for outcome in ('PASS','FAIL'):
   if restored[outcome]<checkpoint['counts'].get(outcome,0):
    raise RuntimeError(f'Result log is missing checkpointed {outcome} cases')
  self.elapsed_before=checkpoint['elapsed_seconds']
  last_command=self.state/'command.tmp'
  if last_command.exists():
   last_command.rename(self.state/f'interrupted-command-{time.time_ns()}.log')
  self.log('RESUME '+json.dumps(dict(counts=dict(restored),previous=checkpoint['current'])))
 def log(self,message):
  with (self.output/'verilator.log').open('a') as out:out.write(f'[{now()}] {message}\n')
 def checkpoint(self,status='RUNNING',reason=''):
  atomic(self.state/'status.json',dict(status=status,phase=self.phase,current=self.current,counts=dict(Counter(r['status'] for r in self.records)),elapsed_seconds=round(self.elapsed_before+time.monotonic()-self.started,1),output=str(self.output),reason=reason,pid=os.getpid(),child=self.child))
  fields=['design','phase','case','mode','b','rf','y_slice','dma_ids','wb','r','k','m','status','reason','load_x','generate','load_w','select','loadw_select','store_o','cycles','core','gemmini_active','seed_x','seed_w','x_sha256','w_sha256','simulator_sha256','elf_sha256','source_sha256','seconds']
  temp=self.output/'cycles.csv.tmp'
  with temp.open('w',newline='') as stream:
   writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(self.records)
  temp.replace(self.output/'cycles.csv');self.report()
 def report(self):
  lines=['# LUMAX pipelined weight loader: actual RTL run','',f'Updated {now()}.','',
   'Actual make-built Verilator + RISC-V binaries; independent exact host Python references. No cycle-model predictions are presented as measurements. '
   'Gemmini baseline is the existing edited-Gemmini TEST_LLMS Verilator run.','',
   '| Design | W8 active / speedup | W4 active / speedup | W2 active / speedup | PASS |','|---|---:|---:|---:|---:|']
  for config in self.options['configurations']:
   b,rf,ys,ids=config;name=f'b{b}_rf{rf}_ys{ys}_id{ids}_fp16';cells=[]
   group=[r for r in self.records if r['design']==name and r['phase']=='LLM']
   for wb in WEIGHTS:
    rows=[r for r in group if r['wb']==wb]
    if len(rows)==12 and all(r['status']=='PASS' for r in rows):
     total=sum(r['cycles'] for r in rows);gem=sum(r['gemmini_active'] for r in rows);cells.append(f'{total:,} / {gem/total:.3f}x')
    else:cells.append(f'{sum(r["status"]=="PASS" for r in rows)}/12 PASS (pending/failed)')
   lines.append(f'| B{b}/RF{rf}/YS{ys}/ID{ids} | '+' | '.join(cells)+f' | {sum(r["status"]=="PASS" for r in group)}/36 |')
  lines+=['','Cycle-model accuracy is not calculated by this runner. This report compares measured Verilator cycles with the saved edited-Gemmini measurements; numerical Python correctness is a separate check.','', 'Speedup = summed Gemmini controller-active cycles / summed LUMAX active cycles over the same 12 shapes per precision. '
   'Ratios are withheld until a precision group fully passes. LUMAX active=Load X + generation + combined LoadW/Select + Store O; overlapping diagnostics are not added twice. '
   'Gemmini uses separate 8 read + 8 write IDs; LUMAX uses 8 shared IDs. Counter coverage and hardware resources differ.','',
   'Builds use private source/configuration snapshots; this runner does not modify live hardware, software or the Python cycle model.','',
   'Logic example: after all requests for column A are accepted, B requests may start in the other free existing buffer while A responses are outstanding. '
   'A separate ordered response cursor writes each response to its own reserved buffer. The final response commits SRAM data and ready together; '
   'selection starts next cycle. A buffer is released only when its final selection read is captured.','',
   'Small directed gates include unaligned packed columns, partial K/output tails, multiple K windows, multiple row groups and sequential matmuls in one ELF. '
   'Full-run numerical references execute only in host Python; expected output arrays are never placed in the RISC-V ELF.','']
  (self.output/'model_accuracy.md').write_text('\n'.join(lines))
 def guard(self):
  if STOP or (self.state/'stop.request').exists():raise RuntimeError('Stop requested')
 def command(self,args,cwd,timeout,phase,binary=False):
  self.guard();self.phase=phase;self.checkpoint();self.log('COMMAND '+shlex.join(list(map(str,args))))
  path=self.state/'command.tmp';start=time.monotonic()
  with path.open('wb') as stream:
   proc=subprocess.Popen(list(map(str,args)),cwd=cwd,env=self.env,stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True);self.child=proc.pid
   heartbeat=time.monotonic()
   try:
    while proc.poll() is None:
     self.guard()
     if time.monotonic()-start>timeout:raise RuntimeError(phase+' timed out')
     if time.monotonic()-heartbeat>=15:self.checkpoint();heartbeat=time.monotonic()
     time.sleep(.25)
   finally:
    if proc.poll() is None:
     os.killpg(proc.pid,signal.SIGTERM)
     try:proc.wait(5)
     except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
    self.child=None;stream.flush();raw=path.read_bytes()
    if not binary:self.log('ACTUAL OUTPUT\n'+raw.decode(errors='replace'))
    self.log(f'COMMAND EXIT={proc.returncode}; seconds={time.monotonic()-start:.3f}')
  if proc.returncode:raise RuntimeError(phase+f' exit={proc.returncode}; '+repr(raw[-2000:]))
  return raw
 def build(self,config,mode,small=False):
  b,rf,ys,ids=config
  maxr,maxk,maxm=self.options.get('capacity', (max(ys,5),129,33) if small else (max(ys,8),1216,1216))
  original=(self.source/'scala/Config.scala').read_text();values=dict(Rin=maxr,Cin=maxk,Cout=maxm,x_slice=b,Mem_row_factor=rf,y_slice=ys,Dma_Ids=ids,DEBUG='true',SCALE='false',FP16_ACTIVATIONS=str(mode=='fp16').lower())
  text=original
  for key,value in values.items():
   text,count=re.subn(r'(?m)^(\s*'+key+r'\s*=\s*)(?:\d+|true|false)\b',lambda m:m[1]+str(value),text)
   if count!=1:raise RuntimeError('Config key '+key)
  fingerprint=hashlib.sha256((json.dumps(self.options['source_sha256'],sort_keys=True)+text+self.options['base_assembly_sha256']).encode()).hexdigest()
  cache=CACHE/fingerprint;cache.mkdir(parents=True,exist_ok=True)
  self.current=f'BUILD B{b}/RF{rf}/YS{ys}/ID{ids} {mode}'
  lock=(cache/'build.lock').open('a+');fcntl.flock(lock,fcntl.LOCK_EX)
  try:
   sim=cache/'simulator-LUMAXROcketConfig';snapshot=cache/'Config.scala';snapshot.write_text(text)
   marker=cache/'success.json'
   if marker.exists() and sim.exists() and json.loads(marker.read_text()).get('simulator_sha256')==sha(sim):return sim,snapshot
   scala=cache/'scala';scala.mkdir(exist_ok=True)
   for p in (self.source/'scala').rglob('*.scala'):
    target=scala/p.relative_to(self.source/'scala');target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
   (scala/'Config.scala').write_text(text)
   classes=cache/'classes';classes.mkdir(exist_ok=True)
   j=self.options['jars'];assembly=self.state/'chipyard.jar';overlay=cache/'chipyard-lumax.jar'
   cp=':'.join([j['scala-compiler-2.13.12.jar'],j['scala-reflect-2.13.12.jar'],str(assembly)])
   self.command([self.env['JAVA_HOME']+'/bin/java','-Xmx8G','-cp',cp,'scala.tools.nsc.Main','-classpath',assembly,'-Xplugin:'+j['chisel-plugin_2.13.12-6.5.0.jar'],'-language:reflectiveCalls','-d',classes,*sorted(scala.rglob('*.scala'))],cache,BUILD_TIMEOUT,'Scala compilation')
   shutil.copyfile(assembly,overlay)
   self.command([self.env['JAVA_HOME']+'/bin/jar','uf',overlay,'-C',classes,'.'],cache,300,'private overlay')
   long='chipyard.harness.TestHarness.LUMAXROcketConfig';gen=cache/'generated-src'/long;gen.mkdir(parents=True,exist_ok=True)
   for arch in [32,64]:shutil.copyfile(CY/f'generators/testchipip/src/main/resources/testchipip/bootrom/bootrom.rv{arch}.img',gen/f'bootrom.rv{arch}.img')
   self.command([self.env['JAVA_HOME']+'/bin/java','-Xmx12G','-Xss8M','-cp',overlay,'chipyard.Generator','--target-dir',gen,'--name',long,'--top-module','chipyard.harness.TestHarness','--legacy-configs','chipyard:LUMAXROcketConfig'],gen,BUILD_TIMEOUT,'Chisel elaboration')
   (gen/(long+'.chisel.log')).touch()
   self.command(['make','-C',CY/'sims/verilator','CONFIG=LUMAXROcketConfig','CONFIG_PACKAGE=chipyard','sim='+str(sim),'sim_dir='+str(cache),'build_dir='+str(gen),'GENERATOR_CLASSPATH='+str(overlay),'-o',overlay,'-o',CY/'.classpath_cache/tapeout.jar','-o',gen/(long+'.fir'),'-o',gen/(long+'.anno.json'),'-o',gen/(long+'.chisel.log'),'-j'+str(MAKE_JOBS)],cache,BUILD_TIMEOUT,'make Verilator')
   if not sim.exists():raise RuntimeError('make did not produce simulator')
   atomic(marker,dict(simulator_sha256=sha(sim),config_sha256=sha(snapshot),source_sha256=self.options['source_sha256']))
   return sim,snapshot
  finally:lock.close()
 def execute(self,items,sim,snapshot):
  start=time.monotonic();self.current=items[0]['design']+' '+','.join(r['case'] for r in items);self.checkpoint()
  with tempfile.TemporaryDirectory(prefix='case-',dir=self.state) as directory:
   work=Path(directory);values=[operands(r) for r in items]
   for r,(x,w,expected) in zip(items,values):
    self.log('CASE '+json.dumps(r));self.log('RANDOM '+json.dumps(dict(x_nonzero=sum(bool(v&0x7fff) if r['mode']=='fp16' else bool(v) for v in x),weight_bytes=len(w),output_elements=len(expected))))
   source=work/'Linear-sw.c';source.write_text(test_source(self.base,items,values))
   for name in ['build.sh','rocc.h','compiler.h']:shutil.copyfile(self.source/'software'/name,work/name)
   oldenv=self.env;self.env=dict(oldenv,LUMAX_CONFIG_PATH=str(snapshot),TEST_CFLAGS='-O2 -DLUMAX_FP16_ACTIVATIONS='+str(int(items[0]['mode']=='fp16')))
   try:self.command(['bash','./build.sh','baremetal'],work,600,'RISC-V build')
   finally:self.env=oldenv
   elf=work/'Linear-sw.riscv';raw=self.command([sim,'+permissive','+max-cycles=2000000000','+loadmem='+str(elf),'+permissive-off',elf],work,SIM_TIMEOUT,'ACTUAL VERILATOR',binary=True)
   tail=raw
   for i,(r,(_,_,expected)) in enumerate(zip(items,values)):
    marker=re.search(rb'(?m)^OUTPUT_BITS '+str(i).encode()+rb' (\d+)\n',tail)
    if not marker or int(marker[1])!=len(expected):raise RuntimeError('Missing actual output '+r['case']+': '+repr(tail[-1000:]))
    header=tail[:marker.start()].decode(errors='replace');n=len(expected);end=marker.end()+n*4
    if len(tail)<end:raise RuntimeError('Truncated HW output')
    actual=list(struct.unpack('<'+'I'*n,tail[marker.end():end]));tail=tail[end:]
    result=re.findall(r'RESULT '+str(i)+r' GUARDS (\d) CORE (\d+) PC ((?:\d+ ){5}\d+)',header)
    if len(result)!=1:raise RuntimeError('Missing cycle/guard record')
    guard,core,pc=result[0];pc=list(map(int,pc.split()));matched=sum(a==b for a,b in zip(actual,expected))
    for j,(a,e) in enumerate(zip(actual,expected)):
     error=abs(struct.unpack('<f',struct.pack('<I',a))[0]-struct.unpack('<f',struct.pack('<I',e))[0]) if r['mode']=='fp16' else abs((a if a<2**31 else a-2**32)-(e if e<2**31 else e-2**32))
     self.log(f'VALUE {r["design"]} {r["case"]} W{r["wb"]} row={j//r["m"]} col={j%r["m"]} HW={a:08x} PYTHON={e:08x} ABS_ERROR={error}')
    r.update(status='PASS' if guard=='1' and actual==expected else 'FAIL',reason='' if matched==n and guard=='1' else f'guards={guard}; exact={matched}/{n}',core=int(core),cycles=pc[0]+pc[1]+pc[4]+pc[5],**dict(zip(['load_x','generate','load_w','select','store_o','loadw_select'],pc)),simulator_sha256=sha(sim),elf_sha256=sha(elf),source_sha256=sha(source),seconds=round(time.monotonic()-start,3))
    if r['cycles']<=0:r.update(status='FAIL',reason='No positive measured cycle count')
    self.log('RESULT '+json.dumps(r));self.checkpoint()
   if not re.search(rb'(?m)^TEST_DONE 0\r?$',tail):raise RuntimeError('Missing successful TEST_DONE')
 def run(self):
  failure=''
  try:
   self.log('START '+json.dumps(self.options['configurations']));self.log('SOURCE SHA256 '+json.dumps(self.options['source_sha256']))
   self.checkpoint()
   for mode in ['fp16','int16']:
    bootstrap=[r for r in self.records if r['phase']=='BOOT' and r['mode']==mode]
    if all(r['status']=='PASS' for r in bootstrap):continue
    config=(8,4,2,8);sim,snapshot=self.build(config,mode,small=True)
    for wb in WEIGHTS:
     group=[r for r in bootstrap if r['wb']==wb]
     pending=[r for r in group if r['status']=='PENDING']
     if pending:self.execute(pending,sim,snapshot)
     if any(r['status']!='PASS' for r in group):raise RuntimeError('Small bootstrap failed; full suite blocked')
   for config in self.options['configurations']:
    b,rf,ys,ids=config;name=f'b{b}_rf{rf}_ys{ys}_id{ids}_fp16'
    cases=[r for r in self.records if r['design']==name]
    if all(r['status']!='PENDING' for r in cases):continue
    try:
     sim,snapshot=self.build(config,'fp16')
     for wb in WEIGHTS:
      group=[r for r in cases if r['phase']=='GATE' and r['wb']==wb]
      pending=[r for r in group if r['status']=='PENDING']
      if pending:self.execute(pending,sim,snapshot)
      if any(r['status']!='PASS' for r in group):raise RuntimeError('Configuration smoke gate failed')
     for r in [r for r in cases if r['phase']=='LLM' and r['status']=='PENDING']:
      self.execute([r],sim,snapshot)
    except Exception as exc:
     if STOP or (self.state/'stop.request').exists():raise
     self.log(traceback.format_exc())
     for r in self.records:
      if r['design']==name and r['status']=='PENDING':r.update(status='BLOCKED',reason=str(exc))
     self.checkpoint()
  except Exception as exc:
   failure=str(exc);self.log(traceback.format_exc())
  finally:
   for r in self.records:
    if r['status']=='PENDING':r.update(status='BLOCKED',reason=failure or 'Not executed')
   self.phase='finished';status='PASS' if all(r['status']=='PASS' for r in self.records) else 'FAIL'
   self.log('FINAL '+json.dumps(dict(status=status,counts=dict(Counter(r['status'] for r in self.records)),reason=failure)))
   self.checkpoint(status,failure)
  return 0 if status=='PASS' else 1

def launch(state,output,worker):
 status=state/'status.json'
 previous=status.stat().st_mtime_ns if status.exists() else None
 with (output/'verilator.log').open('ab') as log:
  proc=subprocess.Popen([sys.executable,'-u','-B',SELF,worker,'--state',state],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 for _ in range(50):
  if proc.poll() is not None:
   raise RuntimeError(f'Worker exited during startup. See {output / "verilator.log"}')
  if status.exists() and status.stat().st_mtime_ns!=previous:break
  time.sleep(.1)
 print(f'Started PID {proc.pid}.\nResults: {output}\nStatus: python3 {SELF} status')
 return 0

def main():
 global STOP
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['start','status','resume','stop','worker','resume-worker']);p.add_argument('--state',type=Path);args=p.parse_args()
 STATE.mkdir(mode=0o700,exist_ok=True);pointer=STATE/'latest.json'
 if args.command in ('worker','resume-worker'):
  signal.signal(signal.SIGHUP,signal.SIG_IGN)
  def stop(*_):
   global STOP
   STOP=True
  signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
  with (args.state/'worker.lock').open('a+') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   runner=Runner(args.state)
   if args.command=='resume-worker':runner.restore()
   return runner.run()
 if args.command in ['status','stop']:
  if not pointer.exists():print('No run yet.');return 0
  state=Path(json.loads(pointer.read_text())['state']);running=active(state)
  if args.command=='stop' and running:(state/'stop.request').touch()
  print('Runner active.' if running else 'Runner inactive.')
  if (state/'status.json').exists():
   status=json.loads((state/'status.json').read_text())
   if not running and status['status']=='RUNNING':
    status.update(status='INTERRUPTED',reason='Worker exited before writing a final checkpoint')
   print(json.dumps(status,indent=2))
  return 0
 with (STATE/'start.lock').open('a+') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  if pointer.exists() and active(Path(json.loads(pointer.read_text())['state'])):raise RuntimeError('Already running')
  if args.command=='resume':
   saved=json.loads(pointer.read_text())
   return launch(Path(saved['state']),Path(saved['output']),'resume-worker')
  name='run_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f');state=STATE/name;state.mkdir()
  output=ROOT/'runs'/name;output.mkdir(parents=True,exist_ok=False)
  source=state/'source';(source/'scala').mkdir(parents=True);(source/'software').mkdir()
  for path in (GEN/'src/main/scala').rglob('*.scala'):
   dest=source/'scala'/path.relative_to(GEN/'src/main/scala');dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,dest)
  for file in ['Linear-sw.c','build.sh','rocc.h','compiler.h']:shutil.copyfile(hw.SRC/file,source/'software'/file)
  manifest={str(p.relative_to(source)):sha(p) for p in source.rglob('*') if p.is_file()}
  shutil.copyfile(CY/'.classpath_cache/chipyard.jar',state/'chipyard.jar')
  jars={}
  for j in ['scala-compiler-2.13.12.jar','scala-reflect-2.13.12.jar','chisel-plugin_2.13.12-6.5.0.jar']:
   found=sorted((Path.home()/'.cache/coursier').rglob(j))
   if not found:raise RuntimeError('Missing cached compiler '+j)
   jars[j]=str(found[0])
  # The archived LUMAX CSV repeats each Gemmini reference for each design.
  # Deduplicate by precision/case and require identical seeds, hashes and cycles.
  reference={}
  with GEM.open() as stream:
   for row in csv.DictReader(stream):
    if row['phase']!='LLM':continue
    if row['status']!='PASS':raise RuntimeError('Archived reference is not PASS')
    saved={key:row[key] for key in ('case','mode','wb','r','k','seed_x','seed_w','x_sha256','w_sha256')}
    saved.update(n=row['m'],status='PASS',controller_active_cycles=row['gemmini_active'])
    key=(saved['wb'],saved['case'])
    if key in reference and reference[key]!=saved:raise RuntimeError('Inconsistent saved Gemmini reference')
    reference[key]=saved
  gem=list(reference.values())
  if len(gem)!=36:raise RuntimeError('Expected 36 saved Gemmini reference cases')
  records=[]
  for mode in ['fp16','int16']:
   for wb in WEIGHTS:
    for i,shape in enumerate(SMALL_SHAPES,1):records.append(record((8,4,2,8),shape,wb,'BOOT',i,mode))
  for config in CONFIGURATIONS:
   for wb in WEIGHTS:
    for i,shape in enumerate(DESIGN_GATE_SHAPES,1):records.append(record(config,shape,wb,'GATE',i))
   for r in gem:records.append(record(config,(int(r['r']),int(r['k']),int(r['n'])),int(r['wb']),'LLM',0,original=r))
  atomic(state/'options.json',dict(output=str(output),configurations=CONFIGURATIONS,records=records,jars=jars,source_sha256=manifest,base_assembly_sha256=sha(state/'chipyard.jar'),gemmini_csv=str(GEM),gemmini_csv_sha256=sha(GEM),runner_sha256=sha(SELF)))
  atomic(pointer,dict(state=str(state),output=str(output)))
  return launch(state,output,'worker')
 return 0
if __name__=='__main__':raise SystemExit(main())
