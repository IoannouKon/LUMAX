#!/usr/bin/env python3
"""Actual Qwen coefficient slices: matched Verilator and ZCU106 flag off/on.
Run with Chipyard's .conda-env/bin/python: start | status. Detached and SSH-safe.
"""
CONFIG = (5, 16, 4, 16)  # B, RF, Y_SLICE, DMA IDs
MAKE_JOBS = 2
VIVADO_THREADS = 2
CLOCK_MHZ = 50
CAPACITY = (8, 1216, 1216)
import os
os.environ['OPENBLAS_NUM_THREADS']='1'
os.environ['OMP_NUM_THREADS']='1'
os.sched_setaffinity(0,sorted(os.sched_getaffinity(0))[:2])
import argparse,csv,fcntl,hashlib,importlib.util,json,random,re,shutil,struct,subprocess,sys,time,traceback
from pathlib import Path
import numpy as np
import requests
V=Path('/home/kioannou/chipyard_1.13.0/generators/LUMAX/verification'); ROOT=V/'TEST_QWEN_SPARSITY'
F=Path('/tmp/lumax-stable-baseline-20261114/frozen')
PREVIOUS=Path('/home/kioannou/LUMAX_Comparison_Notes/W8_Bursts_20261115')
REPOS={8:('Qwen/Qwen2.5-0.5B-Instruct-GPTQ-Int8','c68601e5424e69cdaa6e073673e3c94db27b4397'),4:('Qwen/Qwen2.5-0.5B-Instruct-GPTQ-Int4','c34a4a91629f09f73a285f32dbd26106b033c654'),2:('irish-quant/Qwen-Qwen2.5-0.5B-Instruct-2bit','0cd638ce6ece6dbcf3a26f704f08710c42615587')}
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
sys.path.insert(0,str(V));l=load('qwen_sparse_rtl',V/'TEST_LLMS/run_lumax_weight_loader.py')
l.MAKE_JOBS=MAKE_JOBS
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):
 q=p.with_suffix('.tmp');q.write_text(json.dumps(x,indent=2)+'\n');q.replace(p)
def fetch(url,a,b):
 for attempt in range(3):
  try:
   with requests.get(url+f'?sparse_range={a}-{b}',headers={'Range':f'bytes={a}-{b}'},timeout=(20,120),stream=True) as r:
    r.raise_for_status();assert r.status_code==206 and r.headers.get('Content-Range','').startswith(f'bytes {a}-{b}/')
    out=bytearray()
    for chunk in r.iter_content(1024*1024):
     out.extend(chunk);assert len(out)<=b-a+1
    assert len(out)==b-a+1;return bytes(out)
  except Exception:
   if attempt==2:raise
   time.sleep(2)
def dataset(p):
 records=[];arrays={};stats=[]
 families=[('self_attn.','q_proj',224,224),('self_attn.','k_proj',224,32),('mlp.','gate_proj',224,1216),('mlp.','down_proj',1216,224)]
 for wb,(repo,rev) in REPOS.items():
  url=f'https://huggingface.co/{repo}/resolve/{rev}/model.safetensors'
  size=struct.unpack('<Q',fetch(url,0,7))[0];header=json.loads(fetch(url,8,7+size));base=8+size;pack=32//wb;mask=(1<<wb)-1
  for group,(r,layer) in enumerate([(4,0),(5,12),(8,23)]):
   names=[]
   for i,(prefix,proj,k,n) in enumerate(families):
    if group==1:proj={'q_proj':'o_proj','k_proj':'v_proj','gate_proj':'up_proj'}.get(proj,proj)
    if group==2 and proj=='k_proj':proj='v_proj'
    names.append((f'model.layers.{layer}.{prefix}{proj}',k,n))
   keys=[name+suffix for name,_,_ in names for suffix in ('.qweight','.qzeros','.g_idx')]
   lo=min(header[k]['data_offsets'][0] for k in keys);hi=max(header[k]['data_offsets'][1] for k in keys)
   raw=fetch(url,base+lo,base+hi-1)
   def tensor(key):
    m=header[key];a,b=m['data_offsets'];assert m['dtype']=='I32'
    return np.frombuffer(raw,dtype='<i4',count=(b-a)//4,offset=a-lo).reshape(m['shape'])
   for idx,(name,k,n) in enumerate(names):
    qw=tensor(name+'.qweight').view(np.uint32);qz=tensor(name+'.qzeros').view(np.uint32);gi=tensor(name+'.g_idx')
    zp=np.stack([(qz>>(j*wb))&mask for j in range(pack)],axis=-1).reshape(qz.shape[0],-1).astype(np.int16)+1
    rng=random.Random(f'qwen-slice-v1:{layer}:{name}:{k}:{n}')
    k0=rng.randrange(len(gi)-k+1);n0=rng.randrange(qw.shape[1]-n+1)
    indices=np.arange(k0,k0+k)
    codes=(qw[indices//pack,n0:n0+n]>>((indices%pack)*wb).astype(np.uint32)[:,None])&mask
    w=codes.astype(np.int16)-zp[gi[indices],n0:n0+n]
    assert w.min()>=-(1<<(wb-1)) and w.max()<(1<<(wb-1))
    key=f'w{wb}_{group}_{idx}';arrays[key]=w.astype(np.int8)
    rec=l.record(CONFIG,(r,k,n),wb,'QWEN',group*4+idx+1,'fp16');rec.update(data_key=key,checkpoint=repo,revision=rev,tensor=name,k_start=k0,n_start=n0,zero_weights=int((w==0).sum()),weights=int(w.size),zero_percent=100*float((w==0).mean()))
    records.append(rec);stats.append({a:rec[a] for a in ('case','wb','r','k','m','checkpoint','revision','tensor','k_start','n_start','zero_weights','weights','zero_percent')})
    print('DATA',wb,rec['case'],name,f'{k}x{n}',f'zeros={rec["zero_percent"]:.3f}%',flush=True)
 np.savez(p/'weights.npz',**arrays);save(p/'dataset.json',stats);return records
class Runner(l.Runner):
 def report(self):pass
def verify(p,w,records):
 data=np.load(p/'weights.npz')
 def operands(item):
  r,k,n=(item[t] for t in ('r','k','m'));rng=random.Random(item['seed_x'])
  x=[rng.randrange(65536) for _ in range(r*k)];x=[a^0x400 if a&0x7c00==0x7c00 else a for a in x]
  weights=data[item['data_key']].astype(np.int64).reshape(-1).tolist();expected=[]
  for row in range(r):expected.extend(l.fp.reference(x[row*k:(row+1)*k],weights,n,True))
  wb=item['wb'];packed=bytearray((k*n*wb+7)//8)
  for col in range(n):
   for j in range(k):
    bit=(col*k+j)*wb;packed[bit//8]|=(weights[j*n+col]&((1<<wb)-1))<<(bit%8)
  for label,values in [('x',x),('w',weights)]:item[label+'_sha256']=hashlib.sha256(json.dumps(values).encode()).hexdigest()
  return x,packed,expected
 l.operands=operands;l.CACHE=w/'cache'
 for enabled in (False,True):
  name='flag_on' if enabled else 'flag_off';state=w/name;out=p/name;state.mkdir();out.mkdir();src=state/'source';src.mkdir()
  shutil.copytree(p/'source',src/'scala');shutil.copytree(F/'software',src/'software')
  q=src/'scala/Config.scala';q.write_text(q.read_text().replace('SKIP_ZERO_WEIGHTS = false',f'SKIP_ZERO_WEIGHTS = {str(enabled).lower()}'))
  (state/'chipyard.jar').symlink_to(F/'chipyard.jar');opts=json.loads(Path('/tmp/lumax-stable-baseline-20261114/options.json').read_text())
  save(state/'options.json',dict(output=str(out),records=json.loads(json.dumps(records)),configurations=[CONFIG],jars=opts['jars'],source_sha256={str(q.relative_to(src)):sha(q) for q in src.rglob('*') if q.is_file()},base_assembly_sha256=sha(F/'chipyard.jar'),capacity=CAPACITY))
  run=Runner(state);sim,snapshot=run.build(CONFIG,'fp16')
  for rec in run.records:
   run.execute([rec],sim,snapshot);assert rec['status']=='PASS',rec
  run.checkpoint('PASS');save(out/'records.json',run.records)
 a=json.loads((p/'flag_off/records.json').read_text());b=json.loads((p/'flag_on/records.json').read_text());pairs=[]
 for x,y in zip(a,b):
  assert all(x[k]==y[k] for k in ('case','wb','source_sha256','x_sha256','w_sha256'))
  pairs.append(dict(case=x['case'],wb=x['wb'],r=x['r'],k=x['k'],n=x['m'],zero_percent=x['zero_percent'],off_cycles=x['cycles'],on_cycles=y['cycles'],reduction_percent=100*(1-y['cycles']/x['cycles']),off_select=x['select'],on_select=y['select'],off_loadw_select=x['loadw_select'],on_loadw_select=y['loadw_select']))
 with (p/'cycles_comparison.csv').open('w') as f:
  out=csv.DictWriter(f,fieldnames=list(pairs[0]));out.writeheader();out.writerows(pairs)
def hardware(p,w):
 f=load('qwen_sparse_hw',V/'run_w2_id16_comparison.py');f.ppa.VIVADO_THREADS=VIVADO_THREADS
 jars=json.loads(Path('/tmp/lumax-stable-baseline-20261114/options.json').read_text())['jars']
 class Hardware(f.HardwareWorker):
  def prepare(self):
   classes=self.work/'classes';classes.mkdir(exist_ok=True);base=F/'chipyard_fpga.jar';overlay=self.work/'chipyard-fpga-qwen.jar';java=Path(self.env['JAVA_HOME'])/'bin'
   cp=':'.join((jars['scala-compiler-2.13.12.jar'],jars['scala-reflect-2.13.12.jar'],str(base)))
   self.command([java/'java','-Xmx6G','-XX:ActiveProcessorCount=2','-cp',cp,'scala.tools.nsc.Main','-classpath',base,'-Xplugin:'+jars['chisel-plugin_2.13.12-6.5.0.jar'],'-language:reflectiveCalls','-d',classes,*sorted((self.folder/'sources').rglob('*.scala'))],self.work,self.folder/'compile.log',7200)
   shutil.copyfile(base,overlay);self.command([java/'jar','uf',overlay,'-C',classes,'.'],self.work,self.folder/'jar.log',600);return overlay
 for enabled in (False,True):
  name='hardware_on' if enabled else 'hardware_off';folder=p/name;folder.mkdir();src=folder/'sources';src.mkdir();shutil.copytree(p/'source',src/'lumax')
  q=src/'lumax/Config.scala';q.write_text(q.read_text().replace('SKIP_ZERO_WEIGHTS = false',f'SKIP_ZERO_WEIGHTS = {str(enabled).lower()}'))
  shutil.copyfile(p/'ZCU106Configs.scala',src/'ZCU106Configs.scala')
  b,rf,ys,ids=CONFIG;case=dict(name=f'lumax_b{b}_rf{rf}_ys{ys}_id{ids}',accelerator='lumax',b=b,rf=rf,ys=ys,ids=ids,status='PENDING')
  save(folder/'state.json',dict(status='PENDING',work=str(w/name),cases=[case],settings=dict(rin=CAPACITY[0],cin=CAPACITY[1],cout=CAPACITY[2],clock_mhz=CLOCK_MHZ,vivado_threads=VIVADO_THREADS)))
  Hardware(folder).run()
  result=json.loads((folder/'state.json').read_text());assert result['status']=='COMPLETE' and result['cases'][0]['status']=='PASS',result
def report(p):
 rows=list(csv.DictReader((p/'cycles_comparison.csv').open()));lines=['# TEST_QWEN_SPARSITY: actual checkpoint slices','','B5/RF16/YS4/ID16; FP16 activations; real Verilator measurements. Raw centered integer coefficients from Qwen GPTQ checkpoints; group scales/bias are not applied. Activations are deterministic random finite FP16, not captured inference activations. No zero balancing, reshuffling, pruning or rate adjustment. Sample rates may differ from whole-checkpoint averages.','','| Weights | Sample zero rate | Off cycles | On cycles | Reduction |','|---|---:|---:|---:|---:|']
 for wb in (8,4,2):
  rs=[r for r in rows if int(r['wb'])==wb];a=sum(int(r['off_cycles']) for r in rs);b=sum(int(r['on_cycles']) for r in rs)
  ds=[d for d in json.loads((p/'dataset.json').read_text()) if d['wb']==wb];rate=100*sum(d['zero_weights'] for d in ds)/sum(d['weights'] for d in ds)
  lines.append(f'| W{wb} | {rate:.3f}% | {a:,} | {b:,} | {100*(1-b/a):+.3f}% |')
 lines+=['','72/72 exact numerical/guard checks passed. Full per-case stage results: flag_off/cycles.csv and flag_on/cycles.csv. Paired results: cycles_comparison.csv. Provenance, natural zero counts and slice coordinates: dataset.json. Packed checkpoint slices: weights.npz. Actual simulator/ELF commands, hashes and every HW/reference output word: each flag folder/verilator.log.','','## Vivado ZCU106','', 'Two routed implementations of the same multi-precision accelerator at 50 MHz; vectorless power estimates, not workload-specific power. Internal Fmax is an estimate, not a verified maximum system clock. Full-system and accelerator reports are retained under hardware_off/on.','', '| Flag | LUT | FF | BRAM | DSP | Dynamic W | Internal Fmax MHz | Routed/timing |','|---|---:|---:|---:|---:|---:|---:|---|']
 for name in ('hardware_off','hardware_on'):
  q=p/name/'state.json'
  if q.exists():
   c=json.loads(q.read_text())['cases'][0];lines.append('| '+name+' | '+' | '.join(str(c.get(k,'pending')) for k in ('luts','registers','bram_tiles','dsps','dynamic_power_w','estimated_internal_fmax_mhz'))+' | '+c['status']+' |')
 (p/'REPORT.md').write_text('\n'.join(lines)+'\n')
def worker(p):
 w=Path('/tmp')/('qwen-sparsity-'+p.name);w.mkdir();state=dict(status='RUNNING',phase='dataset',pid=os.getpid(),config=CONFIG,work=str(w));save(p/'status.json',state)
 try:
  records=dataset(p);save(p/'plan.json',records);state['phase']='Verilator off/on';save(p/'status.json',state);verify(p,w,records);report(p)
  state['phase']='Waiting for previous Vivado queue';save(p/'status.json',state)
  while not (PREVIOUS/'FINAL_STATUS.json').exists():time.sleep(30)
  state['phase']='Vivado off/on';save(p/'status.json',state);hardware(p,w);report(p)
  state.update(status='PASS',phase='Complete',finished=time.time());save(p/'status.json',state)
 except BaseException as ex:
  traceback.print_exc();state.update(status='FAILED',reason=str(ex));save(p/'status.json',state)
  if (p/'cycles_comparison.csv').exists():report(p)
  raise
def main():
 ap=argparse.ArgumentParser();ap.add_argument('command',choices=['start','status','worker']);ap.add_argument('--run',type=Path);args=ap.parse_args()
 if args.command=='worker':worker(args.run);return
 if args.command=='status':
  p=Path(json.loads((ROOT/'latest.json').read_text())['run']);print((p/'status.json').read_text())
  for name in ('flag_off','flag_on','hardware_off','hardware_on'):
   for q in (Path(json.loads((p/'status.json').read_text())['work'])/name/'status.json',p/name/'state.json'):
    if q.exists():print(name,q.read_text()[:1300])
  return
 with (ROOT/'start.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  if (ROOT/'latest.json').exists():
   old=Path(json.loads((ROOT/'latest.json').read_text())['run'])
   if (old/'status.json').exists() and json.loads((old/'status.json').read_text())['status']=='RUNNING':raise SystemExit('Already running: '+str(old))
  p=ROOT/'runs'/time.strftime('run_%Y%m%d_%H%M%S');p.mkdir(parents=True)
  shutil.copytree(V.parent/'src/main/scala',p/'source');shutil.copyfile(V.parents[2]/'fpga/src/main/scala/zcu106/Configs.scala',p/'ZCU106Configs.scala');shutil.copyfile(__file__,p/'runner.py')
  save(p/'source_manifest.json',{str(q.relative_to(p)):sha(q) for q in p.rglob('*') if q.is_file()});save(ROOT/'latest.json',dict(run=str(p)))
  log=(p/'worker.log').open('ab');proc=subprocess.Popen(['nice','-n','10',sys.executable,'-u','-B',p/'runner.py','worker','--run',p],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  save(p/'status.json',dict(status='RUNNING',phase='Starting',pid=proc.pid,work='/tmp/qwen-sparsity-'+p.name,config=CONFIG));print('Started',proc.pid,'Logs:',p)
if __name__=='__main__':main()
