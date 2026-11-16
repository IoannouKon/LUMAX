#!/usr/bin/env python3
"""Optional runtime bank grouping: real Verilator + two routed ZCU106 builds.
Use Chipyard .conda-env/bin/python: start | status. SSH-safe, two CPU cores.
No source/configuration changes during runs; all sources are snapshotted.
"""
CONFIG = (2, 4, 4, 8)  # minimum B, RF, maximum YS, shared DMA IDs; 8 lanes
CAPACITY = (9, 129, 65)
SHAPES = [(1,32,16),(4,32,16),(5,33,9),(8,64,32),(3,129,17)]
MODES = (1,2,4)  # selected YS; B = CONFIG[0]*CONFIG[2]/YS
MAKE_JOBS = 2
VIVADO_THREADS = 2
CLOCK_MHZ = 50
import argparse,csv,fcntl,hashlib,importlib.util,json,os,re,shutil,subprocess,sys,time,traceback
from pathlib import Path
os.environ.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',JAVA_TOOL_OPTIONS='-XX:ActiveProcessorCount=2')
os.sched_setaffinity(0,sorted(os.sched_getaffinity(0))[:2])
sys.dont_write_bytecode=True
SELF=Path(__file__).resolve();ROOT=SELF.parent
CY=Path(os.environ.get('LUMAX_CHIPYARD','/home/kioannou/chipyard_1.13.0'))
GEN=CY/'generators/LUMAX';V=GEN/'verification';TESTED=ROOT/'tested_source'
F=Path(os.environ.get('LUMAX_FROZEN_BASELINE','/tmp/lumax-stable-baseline-20261114/frozen'))
NOTES=Path('/home/kioannou/LUMAX_Comparison_Notes/Flexible_Bank_Grouping_20261116')
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
sys.path.insert(0,str(V));l=load('grouping_rtl',V/'TEST_LLMS/run_lumax_weight_loader.py');l.MAKE_JOBS=MAKE_JOBS
model=load('grouping_model',TESTED/'Model.py')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,value):
 q=p.with_suffix('.tmp');q.write_text(json.dumps(value,indent=2)+'\n');q.replace(p)
def setting(text,key,value):
 text,n=re.subn(r'(?m)^(\s*'+key+r'\s*=\s*)(?:true|false|\d+)\b',lambda m:m[1]+str(value).lower(),text)
 assert n==1,(key,n);return text
class Runner(l.Runner):
 def report(self):pass
original_source=l.test_source
def test_source(base,items,values):
 text=original_source(base,items,values)
 for key,value in [('XS',CONFIG[0]),('YS',CONFIG[2])]:
  text,n=re.subn(r'(?m)^(#define\s+'+key+r'\s+)\d+',lambda m:m[1]+str(value),text);assert n==1
 for i,item in enumerate(items):
  # The same executable changes grouping between independent matmuls.
  text=text.replace(f' QuantizedTensor_input tx={{.q=(input_t*)x{i}.data}};',
    f' if(lumax_select_bank_grouping({item["y_slice"]})) return 20;\n QuantizedTensor_input tx={{.q=(input_t*)x{i}.data}};')
 # Each case may have a different split and K; use that case's dimensions.
 for i,item in enumerate(items):
  b,rf,wb=(items[0][k] for k in ('b','rf','wb'));block=b*rf*(1<<(8-wb));k=item['k']
  old=f' load_w(tw.q,{block},{(block+8//wb-1)//(8//wb)},{(k+block-1)//block});'
  new=f' int grouping_block=x_slice_max*RF*(1<<(8-W_BITS));\n load_w(tw.q,grouping_block,(grouping_block+8/W_BITS-1)/(8/W_BITS),({k}+grouping_block-1)/grouping_block);'
  assert old in text; text=text.replace(old,new,1)
 return text
l.test_source=test_source

def plan():
 records=[];predictions=[]
 for index,shape in enumerate(SHAPES,1):
  for wb in (8,4,2):
   candidates=[]
   for ys in MODES:
    b=CONFIG[0]*CONFIG[2]//ys
    c=model.compute_cycles(*shape,16,wb,b,ys,CONFIG[3],factor=CONFIG[1],activation='fp16')
    candidates.append(dict(b=b,ys=ys,cycles=c['total_cycles']))
   winner=min(candidates,key=lambda c:c['cycles'])
   predictions.append(dict(shape=shape,wb=wb,candidates=candidates,selected=winner))
   for ys in MODES:
    r=l.record((CONFIG[0]*CONFIG[2]//ys,CONFIG[1],ys,CONFIG[3]),shape,wb,'CASE',index,'fp16')
    r.update(model_cycles=next(c['cycles'] for c in candidates if c['ys']==ys),model_selected=ys==winner['ys'])
    records.append(r)
 return records,predictions

def verify(p,w):
 records,pred=plan();save(p/'model_schedule.json',dict(version=model.MODEL_VERSION,sha256=sha(p/'Model.py'),predictions=pred))
 # Export a tiny C lookup; inference never invokes Python or the cycle model.
 header=['/* Offline predictions: match the saved RF, IDs and lane count. */',
         '#ifndef LUMAX_GROUPING_SCHEDULE_H','#define LUMAX_GROUPING_SCHEDULE_H',
         'static inline unsigned lumax_grouping_for_shape(unsigned r, unsigned k, unsigned n, unsigned wb) {']
 for item in pred:
  r,k,n=item['shape'];header.append(f"    if(r=={r} && k=={k} && n=={n} && wb=={item['wb']}) return {item['selected']['ys']};")
 header += [f'    return {CONFIG[2]}; /* Unknown shape: fixed baseline. */','}','#endif']
 (p/'model_schedule.h').write_text('\n'.join(header)+'\n')

 l.CACHE=w/'cache'
 all_records={}
 for enabled in (False,True):
  name='flag_on' if enabled else 'flag_off';state=w/name;out=p/name;state.mkdir(exist_ok=True);out.mkdir(exist_ok=True)
  src=state/'source';src.mkdir(exist_ok=True);shutil.copytree(p/'source',src/'scala',dirs_exist_ok=True);shutil.copytree(p/'software',src/'software',dirs_exist_ok=True)
  q=src/'scala/Config.scala';q.write_text(setting(q.read_text(),'FLEXIBLE_BANK_GROUPING',enabled))
  if not (state/'chipyard.jar').exists():(state/'chipyard.jar').symlink_to(F/'chipyard.jar')
  use=[dict(r) for r in records if enabled or r['y_slice']==CONFIG[2]]
  # Alternate grouping on every case; includes row tails, K tails and all precisions.
  opts=json.loads((F.parent/'options.json').read_text())
  save(state/'options.json',dict(output=str(out),records=use,configurations=[CONFIG],jars=opts['jars'],source_sha256={str(q.relative_to(src)):sha(q) for q in src.rglob('*') if q.is_file()},base_assembly_sha256=sha(F/'chipyard.jar'),capacity=CAPACITY))
  run=Runner(state);sim,snapshot=run.build(CONFIG,'fp16')
  # Small sequential tests must pass before the longer cases and any Vivado.
  for wb in (8,4,2):
   batch=[r for r in run.records if r['wb']==wb];run.execute(batch,sim,snapshot)
   assert all(r['status']=='PASS' for r in batch),[(r['case'],r['wb'],r['b'],r['y_slice'],r['status']) for r in batch]
   save(out/'records.json',run.records)
  run.checkpoint('PASS');all_records[name]=run.records
 save(p/'verification.json',all_records)
 compare(p)

def compare(p):
 q=p/'verification.json'
 if not q.exists():return
 data=json.loads(q.read_text());rows=[]
 for off in data['flag_off']:
  group=[r for r in data['flag_on'] if (r['case'],r['wb'])==(off['case'],off['wb'])]
  chosen=next(r for r in group if r['model_selected']);best=min(group,key=lambda r:r['cycles']);same=next(r for r in group if r['y_slice']==off['y_slice'])
  rows.append(dict(case=off['case'],r=off['r'],k=off['k'],n=off['m'],wb=off['wb'],fixed_cycles=off['cycles'],same_split_cycles=same['cycles'],selected_b=chosen['b'],selected_ys=chosen['y_slice'],selected_cycles=chosen['cycles'],model_cycles=chosen['model_cycles'],measured_best_b=best['b'],measured_best_ys=best['y_slice'],measured_best_cycles=best['cycles'],speedup=off['cycles']/chosen['cycles'],selection_regret_percent=100*(chosen['cycles']/best['cycles']-1)))
 with (p/'comparison.csv').open('w') as f:
  c=csv.DictWriter(f,fieldnames=list(rows[0]));c.writeheader();c.writerows(rows)
 lines=['# Runtime bank grouping experiment','','Eight physical lanes; fixed baseline B2/YS4, RF4, ID8. Flexible modes B8/YS1, B4/YS2, B2/YS4. FP16 activations, W8/W4/W2; exact host reference, guards, sequential mode changes in the same RISC-V ELF. No expected outputs are placed in the ELF.','','Python controller-events-v7 chooses a split before measurements; comparison.csv includes measured best and selection regret. Standard per-bank table/weight capacity is retained; flexible weight storage supports maximum B. Prediction excludes any new physical clock-frequency cost.','','| Weights | Fixed active cycles | Model-selected active cycles | Speedup |','|---|---:|---:|---:|']
 for wb in (8,4,2):
  a=[r for r in rows if r['wb']==wb];base=sum(r['fixed_cycles'] for r in a);selected=sum(r['selected_cycles'] for r in a);lines.append(f'| W{wb} | {base:,} | {selected:,} | {base/selected:.3f}x |')
 lines+=['','## ZCU106 Vivado','','Target 50 MHz. Vectorless dynamic power estimate, not workload energy. Internal Fmax is timing headroom, not a verified higher-clock implementation.','','| Flag | Status | LUT | FF | BRAM | DSP | Dynamic W | Internal Fmax MHz |','|---|---|---:|---:|---:|---:|---:|---:|']
 for name in ('hardware_off','hardware_on'):
  f=p/name/'state.json'
  if f.exists():
   c=json.loads(f.read_text())['cases'][0];lines.append('| '+name+' | '+' | '.join(str(c.get(k,'pending')) for k in ('status','luts','registers','bram_tiles','dsps','dynamic_power_w','estimated_internal_fmax_mhz'))+' |')
 (p/'REPORT.md').write_text('\n'.join(lines)+'\n')

def hardware(p,w):
 f=load('grouping_hardware',V/'run_w2_id16_comparison.py');f.ppa.VIVADO_THREADS=VIVADO_THREADS
 jars=json.loads((F.parent/'options.json').read_text())['jars']
 class Hardware(f.HardwareWorker):
  def prepare(self):
   classes=self.work/'classes';classes.mkdir(exist_ok=True);base=F/'chipyard_fpga.jar';overlay=self.work/'chipyard-fpga-grouping.jar';java=Path(self.env['JAVA_HOME'])/'bin'
   cp=':'.join((jars['scala-compiler-2.13.12.jar'],jars['scala-reflect-2.13.12.jar'],str(base)))
   self.command([java/'java','-Xmx6G','-XX:ActiveProcessorCount=2','-cp',cp,'scala.tools.nsc.Main','-classpath',base,'-Xplugin:'+jars['chisel-plugin_2.13.12-6.5.0.jar'],'-language:reflectiveCalls','-d',classes,*sorted((self.folder/'sources').rglob('*.scala'))],self.work,self.folder/'compile.log',7200)
   shutil.copyfile(base,overlay);self.command([java/'jar','uf',overlay,'-C',classes,'.'],self.work,self.folder/'jar.log',600);return overlay
 for enabled in (False,True):
  name='hardware_on' if enabled else 'hardware_off';folder=p/name;folder.mkdir(exist_ok=True);src=folder/'sources';src.mkdir(exist_ok=True);shutil.copytree(p/'source',src/'lumax',dirs_exist_ok=True)
  q=src/'lumax/Config.scala';text=setting(q.read_text(),'FLEXIBLE_BANK_GROUPING',enabled);text=setting(text,'FP16_ACTIVATIONS',True);q.write_text(text)
  shutil.copyfile(p/'ZCU106Configs.scala',src/'ZCU106Configs.scala')
  b,rf,ys,ids=CONFIG;case=dict(name=f'lumax_b{b}_rf{rf}_ys{ys}_id{ids}',accelerator='lumax',b=b,rf=rf,ys=ys,ids=ids,status='PENDING')
  save(folder/'state.json',dict(status='PENDING',work=str(w/name),cases=[case],settings=dict(rin=CAPACITY[0],cin=CAPACITY[1],cout=CAPACITY[2],clock_mhz=CLOCK_MHZ,vivado_threads=VIVADO_THREADS)))
  Hardware(folder).run();compare(p)
 # Attempt both and retain failures for review; no fabricated PPA values.
 states=[json.loads((p/n/'state.json').read_text()) for n in ('hardware_off','hardware_on')]
 assert all(s['cases'][0]['status']=='PASS' for s in states),states

def worker(p):
 global model
 model=load('frozen_grouping_model',p/'Model.py')
 w=Path('/tmp')/('lumax-bank-grouping-'+p.name);w.mkdir(exist_ok=True)
 if not (p/'work').exists():(p/'work').symlink_to(w,target_is_directory=True)
 status=dict(status='RUNNING',phase='Verilator fixed/flexible',pid=os.getpid(),started=time.time());save(p/'status.json',status)
 try:
  with (p/'worker.lock').open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   verify(p,w);status['phase']='Vivado fixed/flexible';save(p/'status.json',status);hardware(p,w)
   status.update(status='PASS',phase='Complete',finished=time.time());save(p/'status.json',status)
 except BaseException as ex:
  traceback.print_exc();status.update(status='FAILED',reason=str(ex));save(p/'status.json',status);raise

def main():
 ap=argparse.ArgumentParser();ap.add_argument('command',choices=['start','status','worker']);ap.add_argument('--run',type=Path);args=ap.parse_args()
 if args.command=='worker':worker(args.run);return
 if args.command=='status':
  p=Path(json.loads((ROOT/'latest.json').read_text())['run']);print('Run:',p);print((p/'status.json').read_text() if (p/'status.json').exists() else 'Starting')
  for n in ('flag_off','flag_on','hardware_off','hardware_on'):
   q=p/('work/'+n+'/status.json' if n.startswith('flag') else n+'/state.json')
   if q.exists():print(n,q.read_text()[:1800])
  return
 with (ROOT/'start.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  if (ROOT/'latest.json').exists():
   old=Path(json.loads((ROOT/'latest.json').read_text())['run']);q=old/'status.json'
   if q.exists() and json.loads(q.read_text())['status']=='RUNNING':raise SystemExit('Already running: '+str(old))
  p=ROOT/'runs'/time.strftime('run_%Y%m%d_%H%M%S');p.mkdir(parents=True)
  shutil.copytree(TESTED/'scala',p/'source');(p/'software').mkdir()
  for n in ('Linear-sw.c','build.sh','rocc.h','compiler.h'):shutil.copyfile(TESTED/'software'/n,p/'software'/n)
  shutil.copyfile(TESTED/'ZCU106Configs.scala',p/'ZCU106Configs.scala');shutil.copyfile(SELF,p/'tested_runner.py');shutil.copyfile(TESTED/'Model.py',p/'Model.py')
  save(p/'manifest.json',{str(q.relative_to(p)):sha(q) for q in p.rglob('*') if q.is_file()})
  with (p/'runner.log').open('ab') as log:
   proc=subprocess.Popen([sys.executable,'-u','-B',SELF,'worker','--run',p],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  save(ROOT/'latest.json',dict(run=str(p),pid=proc.pid));print('Started',proc.pid,'Run:',p)
if __name__=='__main__':main()
