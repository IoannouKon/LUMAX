#!/usr/bin/env python3
"""Full routed ZCU106 implementation matching the S4 TEST_QWEN frozen source.
Detached, sequential, two CPU cores/two Vivado threads; no production edits.
"""
CONFIG = (4, 4, 2, 8)  # B, RF, YS, shared DMA IDs
CAPACITY = (4, 4864, 4864)  # Exact maximum R, K, N of the selected TEST_QWEN build
CLOCK_MHZ = 50
THREADS = 2
import argparse,contextlib,csv,datetime,fcntl,hashlib,importlib.util,json,os,re,shutil,subprocess,sys,time,traceback
from pathlib import Path
sys.dont_write_bytecode = True
os.environ.update(JAVA_TOOL_OPTIONS='-XX:ActiveProcessorCount=2',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:2])
SELF = Path(__file__).resolve(); CY = Path(os.environ.get('LUMAX_CHIPYARD','/home/kioannou/chipyard_1.13.0')); GEN = CY/'generators/LUMAX'; ROOT = GEN/'verification/TEST_QWEN/vivado'
BASE = Path('/tmp/lumax-stable-baseline-20261114/frozen')
PREDECESSOR = GEN/'verification/TEST_GROUP_SCALING/vivado/runs/run_20261117_214457'
TEST_QWEN_RUN = Path('/home/kioannou/chipyard_1.13.0/generators/LUMAX/verification/TEST_QWEN/runs/run_20261117_221604')
def now():return datetime.datetime.now().astimezone().isoformat()
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()
def save(p,x):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(x,indent=2)+'\n');t.replace(p)
def load(n,p):
 s=importlib.util.spec_from_file_location(n,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def setvalue(text,key,value):
 text,n=re.subn(r'(?m)^(\s*'+key+r'\s*=\s*)(?:true|false|\d+)\b',lambda m:m[1]+str(value).lower(),text)
 assert n==1,(key,n);return text
def held(p):
 if not p.exists():return False
 with p.open('a') as f:
  try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);return False
  except BlockingIOError:return True
def snapshot(p):
 frozen=p/'frozen';frozen.mkdir()
 tested=TEST_QWEN_RUN/'frozen';manifest=json.loads((tested/'manifest.json').read_text())
 for rel,digest in manifest.items():assert sha(tested/rel)==digest,rel
 shutil.copytree(tested/'scala',frozen/'lumax')
 shutil.copyfile(CY/'fpga/src/main/scala/zcu106/Configs.scala',frozen/'ZCU106Configs.scala')
 shutil.copyfile(CY/'fpga/scripts/lumax_zcu106_reports.tcl',frozen/'lumax_zcu106_reports.tcl')
 # Freeze the flow and explicitly resolve its original Chipyard location.
 helper=(GEN/'verification/HW_resources_with_vivado_zcu106.py').read_text()
 helper=helper.replace('CHIPYARD = Path(__file__).resolve().parents[3]',f'CHIPYARD = Path({str(CY)!r})')
 helper=helper.replace('REPORT_TCL = FPGA / "scripts/lumax_zcu106_reports.tcl"',f'REPORT_TCL = Path({str(frozen/"lumax_zcu106_reports.tcl")!r})')
 (frozen/'HW_resources_with_vivado_zcu106.py').write_text(helper)
 shutil.copyfile(GEN/'verification/run_vivado_comparison.py',frozen/'run_vivado_comparison.py')
 shutil.copyfile(SELF,p/'tested_runner.py')
 save(frozen/'jars.json',json.loads((BASE.parent/'options.json').read_text())['jars'])
 # No routing, optimization, or reporting step is removed from the Tcl flow.
 t=(frozen/'lumax_zcu106_reports.tcl').read_text()
 for step in ('synth_design','opt_design','place_design','phys_opt_design','route_design','report_power','report_timing_summary'):assert re.search(r'^\s*'+step+r'\b',t,re.M),step
 save(frozen/'manifest.json',{str(q.relative_to(frozen)):sha(q) for q in frozen.rglob('*') if q.is_file() and q.name!='manifest.json'})
 save(p/'settings.json',dict(config=CONFIG,capacity=CAPACITY,clock_mhz=CLOCK_MHZ,threads=THREADS,flow='synthesis + optimization + placement + physical optimization + routing + timing + power',predecessor=str(PREDECESSOR),test_qwen_run=str(TEST_QWEN_RUN),test_qwen_source_manifest_sha256=sha(TEST_QWEN_RUN/'frozen/manifest.json'),base_assembly=str(BASE/'chipyard_fpga.jar'),base_assembly_sha256=sha(BASE/'chipyard_fpga.jar')))
def report(p,state):
 rows=[]
 for job in state['jobs']:
  r=dict(job);q=p/job['name']/'state.json'
  if q.exists():r.update(json.loads(q.read_text())['cases'][0])
  rows.append(r)
 fields=['name','deferred','status','resources_stage','luts','registers','bram_tiles','dsps','dynamic_power_w','estimated_internal_fmax_mhz','timing_met','routed','elapsed_seconds','reason']
 with (p/'comparison.csv.tmp').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)
 (p/'comparison.csv.tmp').replace(p/'comparison.csv')
 def val(r,k):
  x=r.get(k);return '—' if x is None else (f'{x:,.3f}'.rstrip('0').rstrip('.') if isinstance(x,(int,float)) else str(x))
 text=['# TEST_QWEN: full routed ZCU106 hardware results','',f'Updated: {now()}. Batch status: {state["status"]}.', '', 'B4/RF4/YS2/ID8, FP16, capacity R4/K4864/N4864, 8-byte DMA, 50 MHz target. GROUP_SCALE_DEFERRED=true; GROUP_SCALE_ALIGNED, SKIP_ZERO_WEIGHTS and FLEXIBLE_BANK_GROUPING=false. This implementation matches the frozen source of the S4 TEST_QWEN run. Two threads and two CPU cores. No implementation stage is skipped.', '', '| Deferred | Status | Stage | LUT | FF | BRAM | DSP | Dynamic W | Internal Fmax MHz |','|---|---|---|---:|---:|---:|---:|---:|---:|']
 for r in rows:text.append('| '+('ON' if r['deferred'] else 'OFF')+' | '+' | '.join(val(r,k) for k in ('status','resources_stage','luts','registers','bram_tiles','dsps','dynamic_power_w','estimated_internal_fmax_mhz'))+' |')
 if len(rows)==2 and all(r.get('resources_stage')=='post_route' for r in rows):
  text+=['','ON relative to OFF:','']
  for key in ('luts','registers','bram_tiles','dsps','dynamic_power_w','estimated_internal_fmax_mhz'):
   a,b=rows[0].get(key),rows[1].get(key)
   if a is not None and b is not None and a!=0:text.append(f'- {key}: {(b/a-1)*100:+.2f}%.')
 text+=['', 'Resources are accelerator-only; full-system reports are also saved. Power uses vectorless activity at the same target clock, not measured workload energy. Internal Fmax is derived from routed internal timing, not a separately proven whole-system operating frequency. A capacity failure retains synthesis utilization without invented timing or power. G32/G64/G128 are runtime settings in this design; TEST_QWEN selects G128. Scale SRAM capacity still supports G32. This is a single matched ON implementation, not a new ON/OFF comparison.', '', 'No new Verilator workload is launched by this hardware runner. Separate small Verilator gates passed W8/W4/W2 group scaling; the full-size TEST_QWEN run is independent and may still be running. FPGA resource/power/timing reports do not provide Verilator workload cycles.', '']
 (p/'comparison.md').write_text('\n'.join(text))
def worker(p):
 with (p/'worker.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  state=json.loads((p/'status.json').read_text());state.update(pid=os.getpid(),status='RUNNING',phase='Checking frozen sources')
  def update(phase=None):
   if phase:state['phase']=phase
   state['updated']=now();save(p/'status.json',state);report(p,state)
  try:
   frozen=p/'frozen'
   for rel,digest in json.loads((frozen/'manifest.json').read_text()).items():assert sha(frozen/rel)==digest,rel
   settings=json.loads((p/'settings.json').read_text());assert sha(BASE/'chipyard_fpga.jar')==settings['base_assembly_sha256']
   update('Queued behind existing full Vivado comparisons')
   while held(PREDECESSOR/'worker.lock'):
    if (p/'stop.request').exists():raise InterruptedError('Stop requested while queued')
    time.sleep(15)
   update('Starting full implementations')
   flow=load('group_scale_full_vivado',frozen/'run_vivado_comparison.py');flow.VIVADO_THREADS=THREADS;flow.GEMMINI_WEIGHT_BITS=()
   jars=json.loads((frozen/'jars.json').read_text());b,rf,ys,ids=CONFIG
   for job in state['jobs']:
    if job['status'] not in ('PENDING','RUNNING'):continue
    state['current']=job['name'];job['status']='RUNNING';update('Preparing '+job['name'])
    folder=p/job['name'];folder.mkdir(exist_ok=True);src=folder/'sources';src.mkdir(exist_ok=True)
    shutil.copytree(frozen/'lumax',src/'lumax',dirs_exist_ok=True);shutil.copyfile(frozen/'ZCU106Configs.scala',src/'ZCU106Configs.scala')
    cfg=src/'lumax/Config.scala';s=cfg.read_text()
    values=dict(banks_per_row=b,table_capacity_factor=rf,parallel_rows=ys,Dma_Ids=ids,Rin=CAPACITY[0],Cin=CAPACITY[1],Cout=CAPACITY[2],FP16_ACTIVATIONS=True,BRAM_OUTPUTS=True,SCALE=False,DEBUG=True,DMA_Bytes=8,GROUP_SCALE_ALIGNED=False,GROUP_SCALE_DEFERRED=job['deferred'],FLEXIBLE_BANK_GROUPING=False,SKIP_ZERO_WEIGHTS=False,WEIGHT_DMA_BURSTS=False,EARLY_WEIGHT_BUFFER_RELEASE=False)
    for k,v in values.items():s=setvalue(s,k,v)
    cfg.write_text(s);save(folder/'configuration.json',values)
    save(src/'manifest.json',{str(q.relative_to(src)):sha(q) for q in src.rglob('*') if q.is_file() and q.name!='manifest.json'})
    save(folder/'state.json',dict(status='PENDING',work=str(Path('/tmp')/('lumax-qwen-ppa-'+p.name)/job['name']),cases=[dict(name=job['name'],accelerator='lumax',status='PENDING',b=b,rf=rf,ys=ys,ids=ids,deferred=job['deferred'])],settings=dict(rin=CAPACITY[0],cin=CAPACITY[1],cout=CAPACITY[2],clock_mhz=CLOCK_MHZ,vivado_threads=THREADS)))
    class Hardware(flow.Worker):
     def write_reports(self):
      fields=('name','status','resources_stage','luts','registers','bram_tiles','dsps','dynamic_power_w','estimated_internal_fmax_mhz','timing_met','routed','reason')
      with (self.folder/'summary.csv').open('w',newline='') as f:
       w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(self.state['cases'])
      report(p,state)
     def prepare(self):
      self.save('Compiling private FPGA source assembly')
      classes=self.work/'classes';classes.mkdir(exist_ok=True);base=BASE/'chipyard_fpga.jar';overlay=self.work/'comparison.jar';java=Path(self.env['JAVA_HOME'])/'bin'
      cp=':'.join((jars['scala-compiler-2.13.12.jar'],jars['scala-reflect-2.13.12.jar'],str(base)))
      self.command([java/'java','-Xmx6G','-XX:ActiveProcessorCount=2','-cp',cp,'scala.tools.nsc.Main','-classpath',base,'-Xplugin:'+jars['chisel-plugin_2.13.12-6.5.0.jar'],'-language:reflectiveCalls','-d',classes,*sorted(src.rglob('*.scala'))],self.work,folder/'compile.log',7200)
      shutil.copyfile(base,overlay);self.command([java/'jar','uf',overlay,'-C',classes,'.'],self.work,folder/'jar.log',600)
      self.state['generator_sha256']=sha(overlay);self.save();return overlay
    update('Full synthesis/place/route: '+job['name'])
    with (folder/'runner.log').open('a',buffering=1) as log,contextlib.redirect_stdout(log):Hardware(folder).run()
    result=json.loads((folder/'state.json').read_text());job['status']=result['cases'][0]['status'];job['reason']=result.get('reason',result['cases'][0].get('reason',''));update()
   state['status']='PASS' if all(j['status']=='PASS' for j in state['jobs']) else 'COMPLETE_WITH_FAILURES';update('Complete')
  except BaseException as e:
   traceback.print_exc();state.update(status='FAILED',reason=str(e));update('Stopped on error');raise

def main():
 a=argparse.ArgumentParser(description=__doc__);a.add_argument('command',choices=('start','status','worker'));a.add_argument('--run',type=Path);args=a.parse_args();ROOT.mkdir(exist_ok=True)
 if args.command=='worker':worker(args.run.resolve());return
 if args.command=='status':
  p=args.run or Path(json.loads((ROOT/'latest.json').read_text())['run']);state=json.loads((p/'status.json').read_text());print(p);print('Worker lock held:',held(p/'worker.lock'));print(json.dumps(state,indent=2))
  current=state.get('current');q=p/current/'state.json' if current else None
  if q and q.exists():print('CURRENT IMPLEMENTATION:',q.read_text())
  return
 with (ROOT/'start.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  if (ROOT/'latest.json').exists():
   old=Path(json.loads((ROOT/'latest.json').read_text())['run'])
   if held(old/'worker.lock'):raise SystemExit('Existing comparison active: '+str(old))
  p=ROOT/'runs'/time.strftime('run_%Y%m%d_%H%M%S');p.mkdir(parents=True);snapshot(p)
  state=dict(status='QUEUED',phase='Starting detached worker',created=now(),jobs=[dict(name='qwen_s4_b4_rf4_ys2_id8_deferred_on',deferred=True,status='PENDING')]);save(p/'status.json',state);report(p,state)
  with (p/'runner.log').open('ab') as log:
   proc=subprocess.Popen([sys.executable,'-u','-B',p/'tested_runner.py','worker','--run',p],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  save(ROOT/'latest.json',dict(run=str(p),pid=proc.pid));print('Started PID',proc.pid,'Run:',p)
if __name__=='__main__':main()
