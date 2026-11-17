#!/usr/bin/env python3
"""Arbitrary-B flexible grouping: gated real TEST_LLMS and ZCU106 comparison.
start/status/resume/worker; detached, two shared-server CPU cores, no auto-commit.
"""
LUMAX_CONFIGS = [(5,16,4,16),(14,16,8,16)] # base B, RF, maximum YS, shared IDs
GEMMINI_DIMS = (4,8)
GEMMINI_IDS_PER_POOL = 8 # 8 read + 8 write = total16; architecture differs from shared16
GEMMINI_DMA_MAX_BYTES = 8
DMA_BEAT_BYTES = 8
CAPACITY = (8,1216,1216)
WEIGHTS = (8,4,2)
MAKE_JOBS = 2
VIVADO_THREADS = 2
CLOCK_MHZ = 50
GATE_SHAPES = [(5,17,9),(3,257,9)]
import argparse,contextlib,csv,fcntl,hashlib,importlib.util,json,os,re,shutil,subprocess,sys,time,traceback
from pathlib import Path
os.environ.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',JAVA_TOOL_OPTIONS='-XX:ActiveProcessorCount=2')
os.sched_setaffinity(0,sorted(os.sched_getaffinity(0))[:2]);sys.dont_write_bytecode=True
SELF=Path(__file__).resolve();ROOT=SELF.parent;CY=Path(os.environ.get('LUMAX_CHIPYARD','/home/kioannou/chipyard_1.13.0'));GEN=CY/'generators/LUMAX';V=GEN/'verification'
ASSETS=Path('/home/kioannou/LUMAX/Baseline_Comparison/Gemmini');F=Path('/tmp/lumax-stable-baseline-20261114/frozen')
REFERENCE=ASSETS/'verification/TEST_LLMS/d4_sp32_acc4_id8/cycles.csv'
sys.path.insert(0,str(V))
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
bank=load('arbitrary_grouping',V/'TEST_BANK_GROUPING/run_test_bank_grouping.py');l=bank.l;l.MAKE_JOBS=MAKE_JOBS
bank.model=load('arbitrary_model',GEN/'Performance Modeling/Model.py')
gem=load('arbitrary_gemmini',ASSETS/'verification/TEST_LLMS/run_test_llms.py')
# Use the same medium Rocket/no-monitor wrapper as LUMAX in Verilator too.
# The stock helper otherwise wraps Gemmini in RocketConfig (a huge core).
original_gem_scala=gem.scala_config
def matched_gem_scala(cfg):
 return original_gem_scala(cfg).replace('new chipyard.RocketConfig',
  'new freechips.rocketchip.subsystem.WithoutTLMonitors ++ new freechips.rocketchip.rocket.WithNMedCores(1) ++ new chipyard.config.AbstractConfig')
gem.scala_config=matched_gem_scala

f=load('arbitrary_ppa',V/'run_w2_id16_comparison.py');f.ppa.VIVADO_THREADS=VIVADO_THREADS
JARS=json.loads((F.parent/'options.json').read_text())['jars']
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):
 q=p.with_suffix('.tmp');q.write_text(json.dumps(x,indent=2)+'\n');q.replace(p)
def configure(s,values):
 for k,v in values.items():s=bank.setting(s,k,v)
 return s
def correct_gemmini_metadata(source):
 cfg=source/'gemmini/gemmini/GemminiConfigs.scala'
 text=cfg.read_text();text=text.replace('    val max_bytes = 64\n','    val max_bytes = dma_maxbytes\n');cfg.write_text(text)
 header=source/'gemmini_fpint.h';text=header.read_text()
 text=text.replace('    size_t tj=gemmini_fpint_min(nj, MAX_BYTES*8/(DIM*GEMMINI_WEIGHT_BITS));',
  '    /* Software tile budget stays at its previous 64 logical bytes; the RTL\n'
  '     * independently splits transfers at its configured DMA maximum. */\n'
  '    const size_t tile_weight_bytes=64;\n'
  '    size_t tj=gemmini_fpint_min(nj, tile_weight_bytes*8/(DIM*GEMMINI_WEIGHT_BITS));')
 header.write_text(text)

def references():
 rs=[r for r in csv.DictReader(REFERENCE.open()) if r['case'].startswith('LLM')]
 assert len(rs)==36 and all(r['status']=='PASS' for r in rs)
 return rs

def test_source(base,items,values):
 text=bank.test_source(base,items,values)
 # Keep data at a common cache-line/subbank offset across all mode variants.
 text=text.replace('packed,aligned(8)','packed,aligned(256)').replace('uint8_t pre[8];','uint8_t pre[64];')
 text=text.replace('{{'+','.join(['165']*8)+'},{','{{'+','.join(['165']*64)+'},{').replace('j<8;j++','j<64;j++')
 for item in items:
  k=item['k'];old=f'int grouping_block=x_slice_max*RF*(1<<(8-W_BITS));\n load_w(tw.q,grouping_block,(grouping_block+8/W_BITS-1)/(8/W_BITS),({k}+grouping_block-1)/grouping_block);'
  new=f'int grouping_block=x_slice_max*RF*(1<<(8-W_BITS)); if(grouping_block>{k})grouping_block={k}; if(grouping_block>65535)grouping_block=65535;\n load_w(tw.q,grouping_block,(grouping_block+8/W_BITS-1)/(8/W_BITS),({k}+grouping_block-1)/grouping_block);'
  assert old in text; text=text.replace(old,new,1)
 return text
l.test_source=test_source

class LumaxRunner(l.Runner):
 def report(self):pass
 def checkpoint(self,*args,**kwargs):
  super().checkpoint(*args,**kwargs)
  self.batch_report()
 def batch_report(self):pass

def lumax_records(job,p):
 config=(job['b'],job['rf'],job['ys'],job['ids']);b,rf,maxys,ids=config
 modes=[1<<i for i in range(maxys.bit_length())] if job['flex'] else [maxys]
 records=[];schedule=[]
 for wb in WEIGHTS:
  for index,shape in enumerate(GATE_SHAPES,1):
   for ys in modes:
    r=l.record((b*maxys//ys,rf,ys,ids),shape,wb,'GATE',index,'fp16');r.update(base_b=b,max_ys=maxys,flexible=job['flex']);records.append(r)
  for ref in [r for r in references() if int(r['wb'])==wb]:
   shape=tuple(int(ref[k]) for k in ('r','k','n'));candidates=[]
   for ys in modes:
    bs=b*maxys//ys;window=min(bs*rf*(1<<(8-wb)),65535)
    cycles=bank.model.compute_cycles(*shape,16,wb,bs,ys,ids,factor=rf,block_size=window,activation='fp16')['total_cycles']
    candidates.append(dict(b=bs,ys=ys,cycles=cycles))
   best=min(candidates,key=lambda r:r['cycles']);schedule.append(dict(case=ref['case'],shape=shape,wb=wb,candidates=candidates,selected=best))
   for phase,ys in [('LLM',best['ys'])]+([('CONTROL',maxys)] if job['flex'] and best['ys']!=maxys else []):
    r=l.record((b*maxys//ys,rf,ys,ids),shape,wb,phase,0,'fp16',ref);r.pop('gemmini_active',None)
    if phase=='CONTROL':r['case']='CONTROL_'+ref['case']
    r.update(base_b=b,max_ys=maxys,flexible=job['flex'],model_cycles=next(c['cycles'] for c in candidates if c['ys']==ys) if ys in modes else None)
    records.append(r)
 save(p/'model_schedule.json',dict(version=bank.model.MODEL_VERSION,predictions=schedule))
 return records

def gem_config(job):
 cfg=gem.settings();d=job['dim'];cfg.update(chipyard=str(CY),dim=d,tile_rows=1,tile_columns=1,mesh_rows=d,mesh_columns=d,sp_kib=32,acc_kib=4,sp_banks=4,dma_ids=GEMMINI_IDS_PER_POOL,dma_bits=64,dma_maxbytes=GEMMINI_DMA_MAX_BYTES,weights=[job['wb']],jobs=MAKE_JOBS,replay=False,suite_hours=None)
 return cfg

def gem_records(job):
 wb=job['wb'];records=[]
 for i,shape in enumerate(GATE_SHAPES,1):
  r=l.record((4,16,4,16),shape,wb,'GATE',i,'fp16');records.append(dict(case=r['case'],wb=wb,r=r['r'],k=r['k'],n=r['m'],seed_x=r['seed_x'],seed_w=r['seed_w'],status='PENDING'))
 for r in references():
  if int(r['wb'])!=wb:continue
  records.append(dict(case=r['case'],wb=wb,r=int(r['r']),k=int(r['k']),n=int(r['n']),seed_x=int(r['seed_x']),seed_w=int(r['seed_w']),expected_x_hash=r['x_sha256'],expected_w_hash=r['w_sha256'],status='PENDING'))
 return records

class Batch:
 def __init__(self,p):
  self.p=p;self.w=Path('/tmp')/('lumax-arbitrary-grouping-'+p.name);self.w.mkdir(exist_ok=True)
  self.status=json.loads((p/'status.json').read_text());self.status.update(status='RUNNING',pid=os.getpid());self.source=p/'frozen'
  global REFERENCE, GEMMINI_IDS_PER_POOL, GEMMINI_DMA_MAX_BYTES
  REFERENCE=p/'reference_cycles.csv'
  policy=json.loads((p/'settings.json').read_text())
  GEMMINI_IDS_PER_POOL=policy['gemmini_ids_per_pool'];GEMMINI_DMA_MAX_BYTES=policy['gemmini_max_bytes']
  for rel,digest in json.loads((self.source/'manifest.json').read_text()).items():
   assert sha(self.source/rel)==digest,'Frozen source changed: '+rel

  l.CACHE=self.w/'lumax_cache';gem.CACHE_ROOT=self.w/'gemmini_cache'
 def save(self,phase=None):
  if phase:self.status['phase']=phase
  self.status['updated']=time.time();save(self.p/'status.json',self.status);self.report()
 def report(self):
  results=[]
  for job in self.status['jobs']:
   row=dict(job);folder=self.p/job['name'];path=folder/'cycles.csv'
   if path.exists():
    records=list(csv.DictReader(path.open()));llms=[r for r in records if r['case'].startswith('LLM')]
    row['passes']=sum(r['status']=='PASS' for r in records);row['tests']=len(records)
    for wb in WEIGHTS:
     rs=[r for r in llms if int(r['wb'])==wb];key='cycles' if job['kind']=='lumax' else 'controller_active_cycles'
     if len(rs)==12 and all(r['status']=='PASS' for r in rs):row['cycles_w'+str(wb)]=sum(int(r[key]) for r in rs)
   hw=folder/'hardware/state.json'
   if hw.exists():
    c=json.loads(hw.read_text())['cases'][0]
    for key in ('status','luts','registers','bram_tiles','dsps','dynamic_power_w','estimated_internal_fmax_mhz','resources_stage','timing_met'):
     row['hw_'+key]=c.get(key)
   results.append(row)
  save(self.p/'results.json',results)
  text=['# TEST_LLMS: arbitrary-B flexible grouping and edited Gemmini','','FP16 activations; W8/W4/W2. LUMAX has 16 shared DMA slots, Gemmini has 8 read + 8 write slots. Both interfaces use 8-byte beats and maximum 8-byte transactions. Slot partitioning remains different. Both Verilator and FPGA wrappers use the same medium Rocket core and no TileLink monitors. All new cycle numbers below come from actual make-built Verilator runs; model-selected splits are predictions checked numerically, not claims of globally measured optimality. No historical ID16-per-pool/64-byte Gemmini cycles are mixed into this batch.','','Common twelve TEST_LLMS shapes: R=4/5/8, K/N=(224,224),(224,32),(224,1216),(1216,224). Same saved operands/hashes across all accelerators. LUMAX uses exact FP32 output-word checks; Gemmini retains its predeclared FP32 forward-error bound. Active cycles exclude software model/lookup/preload overhead.','','| Design | Numerical status | W8 cycles | W4 cycles | W2 cycles |','|---|---|---:|---:|---:|']
  for r in results:text.append('| '+' | '.join(str(r.get(k,'n/a' if r['kind']=='gemmini' and k.startswith('cycles_w') and k!='cycles_w'+str(r['wb']) else 'pending')) for k in ('name','test_status','cycles_w8','cycles_w4','cycles_w2'))+' |')
  text+=['','Speedup = new Gemmini cycles / LUMAX cycles, for the same precision and twelve shapes.','','| LUMAX | Gemmini array | W8 speedup | W4 speedup | W2 speedup |','|---|---|---:|---:|---:|']
  for r in results:
   if r['kind']!='lumax':continue
   for dim in GEMMINI_DIMS:
    cells=[]
    for wb in WEIGHTS:
     g=next((q for q in results if q['kind']=='gemmini' and q['dim']==dim and q['wb']==wb),{})
     a=r.get('cycles_w'+str(wb));b=g.get('cycles_w'+str(wb));cells.append(f'{b/a:.3f}x' if a and b else 'pending')
    text.append('| '+r['name']+' | '+str(dim)+'x'+str(dim)+' | '+' | '.join(cells)+' |')
  text+=['','Flag-on versus flag-off active cycles (off/on; >1 means flexible is faster).','','| Base configuration | W8 speedup | W4 speedup | W2 speedup |','|---|---:|---:|---:|']
  for b,rf,ys,ids in LUMAX_CONFIGS:
   off=next((r for r in results if r['kind']=='lumax' and r['b']==b and not r['flex']),{})
   on=next((r for r in results if r['kind']=='lumax' and r['b']==b and r['flex']),{})
   cells=[]
   for wb in WEIGHTS:
    a=off.get('cycles_w'+str(wb));c=on.get('cycles_w'+str(wb));cells.append(f'{a/c:.3f}x' if a and c else 'pending')
   text.append(f'| B{b}/RF{rf}/maxYS{ys}/ID{ids} | '+' | '.join(cells)+' |')
  text+=['','## ZCU106 Vivado','','50 MHz target, two threads, full system and accelerator reports. Internal Fmax is routed path headroom, not a verified higher clock. Vectorless dynamic power is not workload energy. A synthesis-only resource result does not establish placeability, timing or power.','','| Design | Status | Stage | LUT | FF | BRAM | DSP | Dynamic W | Internal Fmax MHz |','|---|---|---|---:|---:|---:|---:|---:|---:|']
  for r in results:text.append('| '+' | '.join(str(r.get(k,'pending')) for k in ('name','hw_status','hw_resources_stage','hw_luts','hw_registers','hw_bram_tiles','hw_dsps','hw_dynamic_power_w','hw_estimated_internal_fmax_mhz'))+' |')
  (self.p/'REPORT.md').write_text('\n'.join(text)+'\n')
 def lumax(self,job):
  out=self.p/job['name'];out.mkdir(exist_ok=True);state=self.w/job['name'];state.mkdir(exist_ok=True);src=state/'source';src.mkdir(exist_ok=True)
  shutil.copytree(self.source/'lumax',src/'scala',dirs_exist_ok=True);shutil.copytree(self.source/'software',src/'software',dirs_exist_ok=True)
  c=src/'scala/Config.scala';c.write_text(configure(c.read_text(),dict(FLEXIBLE_BANK_GROUPING=job['flex'],FP16_ACTIVATIONS=True,BRAM_OUTPUTS=True,SKIP_ZERO_WEIGHTS=False,EARLY_WEIGHT_BUFFER_RELEASE=False,WEIGHT_DMA_BURSTS=False,DMA_Bytes=DMA_BEAT_BYTES)))
  config=(job['b'],job['rf'],job['ys'],job['ids']);bank.CONFIG=config;bank.CAPACITY=CAPACITY
  if not (state/'chipyard.jar').exists():(state/'chipyard.jar').symlink_to(F/'chipyard.jar')
  records=lumax_records(job,out)
  restored=[]
  if (out/'records.json').exists():
   prior=json.loads((out/'records.json').read_text());oldopts=json.loads((state/'options.json').read_text())
   assert oldopts['source_sha256']=={str(q.relative_to(src)):sha(q) for q in src.rglob('*') if q.is_file()},'Cannot mix changed source with saved passes'
   identity=lambda r:tuple(r[k] for k in ('case','phase','wb','r','k','m','b','y_slice','seed_x','seed_w'))
   saved={identity(r):r for r in prior if r['status']=='PASS'}
   for r in records:
    if identity(r) in saved:r.update(saved[identity(r)]);restored.append(r)
  save(state/'options.json',dict(output=str(out),records=records,configurations=[config],jars=JARS,source_sha256={str(q.relative_to(src)):sha(q) for q in src.rglob('*') if q.is_file()},base_assembly_sha256=sha(F/'chipyard.jar'),capacity=CAPACITY))
  runner=LumaxRunner(state);runner.batch_report=self.report;sim,cfg=runner.build(config,'fp16')
  shutil.copyfile(cfg,out/'Config.scala')
  assert all(r['simulator_sha256']==sha(sim) for r in restored),'Saved simulator differs'
  for wb in WEIGHTS:
   gates=[r for r in runner.records if r['wb']==wb and r['phase']=='GATE' and r['status']!='PASS']
   if gates:runner.execute(gates,sim,cfg)
   save(out/'records.json',runner.records)
   assert all(r['status']=='PASS' for r in gates),'LUMAX small numerical gate failed'
  for r in [r for r in runner.records if r['phase']!='GATE' and r['status']!='PASS']:
   runner.execute([r],sim,cfg);assert r['status']=='PASS',r
   save(out/'records.json',runner.records)
  runner.checkpoint('PASS');save(out/'records.json',runner.records)
 def gemmini(self,job):
  out=self.p/job['name'];out.mkdir(exist_ok=True);state=self.w/job['name'];state.mkdir(exist_ok=True)
  shutil.copytree(self.source/'gemmini',state/'source',dirs_exist_ok=True)
  for name in ('gemmini_fpint.h','xcustom.h'):shutil.copyfile(self.source/name,state/name)
  cfg=gem_config(job);save(state/'options.json',dict(config=cfg,plan=gem_records(job),compiler_jars=JARS,log=str(out/'verilator.log')))
  with (out/'verilator.log').open('a',buffering=1) as log,contextlib.redirect_stdout(log):
   r=gem.Runner(state)
   try:
    r.build_overlay();sim,params=r.build(job['wb']);shutil.copyfile(params,out/'gemmini_params.h')
    for item in r.records:
     r.execute(item,sim,params);self.report();assert item['status']=='PASS',item
    r.phase='finished';r.checkpoint('PASS');save(out/'records.json',r.records)
   finally:
    if hasattr(r,'cache_lock'):r.cache_lock.close()
 def hardware(self,job):
  folder=self.p/job['name']/'hardware';folder.mkdir(exist_ok=True);src=folder/'sources';src.mkdir(exist_ok=True)
  if job['kind']=='lumax':
   shutil.copytree(self.source/'lumax',src/'lumax',dirs_exist_ok=True);q=src/'lumax/Config.scala'
   q.write_text(configure(q.read_text(),dict(FLEXIBLE_BANK_GROUPING=job['flex'],FP16_ACTIVATIONS=True,BRAM_OUTPUTS=True,SKIP_ZERO_WEIGHTS=False,EARLY_WEIGHT_BUFFER_RELEASE=False,WEIGHT_DMA_BURSTS=False,DMA_Bytes=DMA_BEAT_BYTES)))
  else:
   shutil.copytree(self.source/'gemmini',src/'gemmini',dirs_exist_ok=True)
   if not (self.source/'gemmini/LUMAX_compat').exists():
    shutil.copytree(self.source/'lumax',src/'lumax',dirs_exist_ok=True)
   (src/'GemminiTestSafeConfig.scala').write_text(gem.scala_config(gem_config(job)))
   wb=job['wb'];(src/'GemminiZCU106Configs.scala').write_text('package chipyard.fpga.zcu106\nimport org.chipsalliance.cde.config.Config\n'+f'class GemminiW{wb}zcu106 extends Config(new WithZCU106Platform ++ new gemmini.testsafe.WithTestSafeGemmini({wb}) ++ new freechips.rocketchip.subsystem.WithoutTLMonitors ++ new freechips.rocketchip.rocket.WithNMedCores(1) ++ new chipyard.config.AbstractConfig)\n')
  shutil.copyfile(self.source/'ZCU106Configs.scala',src/'ZCU106Configs.scala')
  save(src/'manifest.json',{str(q.relative_to(src)):sha(q) for q in src.rglob('*') if q.is_file()})
  case=dict(name=job['name'],accelerator=job['kind'],status='PENDING')
  if job['kind']=='lumax':case.update(b=job['b'],rf=job['rf'],ys=job['ys'],ids=job['ids'])
  else:case['wb']=job['wb']
  save(folder/'state.json',dict(status='PENDING',work=str(self.w/'vivado'/job['name']),cases=[case],settings=dict(rin=CAPACITY[0],cin=CAPACITY[1],cout=CAPACITY[2],clock_mhz=CLOCK_MHZ,vivado_threads=VIVADO_THREADS)))
  class Hardware(f.HardwareWorker):
   def prepare(worker):
    classes=worker.work/'classes';classes.mkdir(exist_ok=True);base=F/'chipyard_fpga.jar';overlay=worker.work/'comparison.jar';java=Path(worker.env['JAVA_HOME'])/'bin'
    cp=':'.join((JARS['scala-compiler-2.13.12.jar'],JARS['scala-reflect-2.13.12.jar'],str(base)))
    worker.command([java/'java','-Xmx6G','-XX:ActiveProcessorCount=2','-cp',cp,'scala.tools.nsc.Main','-classpath',base,'-Xplugin:'+JARS['chisel-plugin_2.13.12-6.5.0.jar'],'-language:reflectiveCalls','-d',classes,*sorted(src.rglob('*.scala'))],worker.work,folder/'compile.log',7200)
    shutil.copyfile(base,overlay);worker.command([java/'jar','uf',overlay,'-C',classes,'.'],worker.work,folder/'jar.log',600);worker.state['generator_sha256']=sha(overlay);worker.save();return overlay
  with (folder/'runner.log').open('a',buffering=1) as log,contextlib.redirect_stdout(log):Hardware(folder).run()
  result=json.loads((folder/'state.json').read_text());return result['cases'][0]['status']
 def run(self):
  self.save('Verilator: gates then TEST_LLMS')
  for job in self.status['jobs']:
   if job['test_status']=='PASS':continue
   self.status['current']=job['name'];job['test_status']='RUNNING';self.save()
   try:
    (self.lumax if job['kind']=='lumax' else self.gemmini)(job);job['test_status']='PASS';self.save()
   except BaseException as ex:
    job.update(test_status='FAILED',reason=str(ex),hardware_status='BLOCKED');self.save();raise
  self.save('Vivado: sequential implementations')
  for job in self.status['jobs']:
   if job['hardware_status'] not in ('PENDING','RUNNING'):continue
   self.status['current']=job['name'];job['hardware_status']='RUNNING';self.save()
   try:job['hardware_status']=self.hardware(job)
   except Exception as ex:traceback.print_exc();job.update(hardware_status='FAILED',hardware_reason=str(ex))
   self.save()
  self.status['status']='PASS' if all(j['hardware_status']=='PASS' for j in self.status['jobs']) else 'COMPLETE_WITH_HARDWARE_FAILURES';self.save('Complete')

def jobs():
 result=[]
 for flex in (True,False):
  for b,rf,ys,ids in LUMAX_CONFIGS:result.append(dict(name=f'lumax_b{b}_rf{rf}_ys{ys}_id{ids}_flex{int(flex)}',kind='lumax',b=b,rf=rf,ys=ys,ids=ids,flex=flex,test_status='PENDING',hardware_status='PENDING'))
 for d in GEMMINI_DIMS:
  for wb in WEIGHTS:result.append(dict(name=f'gemmini_d{d}_sp32_acc4_id{GEMMINI_IDS_PER_POOL}p_req{GEMMINI_DMA_MAX_BYTES}_w{wb}',kind='gemmini',dim=d,wb=wb,test_status='PENDING',hardware_status='PENDING'))
 return result

def worker(p):
 with (p/'worker.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  batch=Batch(p);bank.model=load('frozen_arbitrary_model',p/'frozen/Model.py')
  try:batch.run()
  except BaseException as ex:traceback.print_exc();batch.status.update(status='FAILED',reason=str(ex));batch.save('Stopped on error');raise

def main():
 ap=argparse.ArgumentParser();ap.add_argument('command',choices=['start','status','resume','worker']);ap.add_argument('--run',type=Path);a=ap.parse_args()
 if a.command=='worker':worker(a.run);return
 if a.command=='status':
  p=a.run or Path(json.loads((ROOT/'latest.json').read_text())['run']);s=json.loads((p/'status.json').read_text());print('Run:',p);print(json.dumps(s,indent=2))
  q=Path('/tmp')/('lumax-arbitrary-grouping-'+p.name)/s.get('current','')/'status.json'
  if q.exists():print('CURRENT TEST:',q.read_text()[:1300])
  return
 with (ROOT/'start.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  if a.command=='start':
   if (ROOT/'latest.json').exists():
    old=Path(json.loads((ROOT/'latest.json').read_text())['run']);s=json.loads((old/'status.json').read_text())
    if s['status']=='RUNNING':raise SystemExit('Existing batch active: '+str(old))
   p=ROOT/'runs'/time.strftime('run_%Y%m%d_%H%M%S');p.mkdir(parents=True);source=p/'frozen';source.mkdir()
   shutil.copytree(GEN/'src/main/scala',source/'lumax');(source/'software').mkdir()
   for n in ('Linear-sw.c','build.sh','rocc.h','compiler.h'):shutil.copyfile(GEN/'software/tests/src'/n,source/'software'/n)
   shutil.copytree(ASSETS/'fpint/src/main/scala',source/'gemmini');shutil.copyfile(ASSETS/'software/gemmini_fpint.h',source/'gemmini_fpint.h');shutil.copyfile(ASSETS/'software/rocc-software/src/xcustom.h',source/'xcustom.h')
   correct_gemmini_metadata(source)
   shutil.copyfile(CY/'fpga/src/main/scala/zcu106/Configs.scala',source/'ZCU106Configs.scala');shutil.copyfile(GEN/'Performance Modeling/Model.py',source/'Model.py');shutil.copyfile(SELF,p/'tested_runner.py');shutil.copyfile(REFERENCE,p/'reference_cycles.csv')
   save(source/'manifest.json',{str(q.relative_to(source)):sha(q) for q in source.rglob('*') if q.is_file()})
   save(p/'settings.json',dict(lumax_configs=LUMAX_CONFIGS,gemmini_dimensions=GEMMINI_DIMS,gemmini_ids_per_pool=GEMMINI_IDS_PER_POOL,gemmini_max_bytes=GEMMINI_DMA_MAX_BYTES,beat_bytes=DMA_BEAT_BYTES,capacity=CAPACITY,clock_mhz=CLOCK_MHZ))
   save(p/'status.json',dict(status='QUEUED',phase='Starting',jobs=jobs(),created=time.time(),current=''))
  else:
   p=a.run or Path(json.loads((ROOT/'latest.json').read_text())['run'])
   with (p/'worker.lock').open('a') as check:fcntl.flock(check,fcntl.LOCK_EX|fcntl.LOCK_NB)
  with (p/'runner.log').open('ab') as log:proc=subprocess.Popen([sys.executable,'-u','-B',SELF,'worker','--run',p],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  save(ROOT/'latest.json',dict(run=str(p),pid=proc.pid));print('Started PID',proc.pid,'Run:',p)
if __name__=='__main__':main()
