#!/usr/bin/env python3
"""Measure four real Qwen projection shapes in Verilator; estimate a prompt.
python3 run_test_qwen.py 8         # detached run; S is the only required input
python3 run_test_qwen.py plan 8    # inspect shapes/configuration; no builds
python3 run_test_qwen.py status    # inspect detached run
python3 run_test_qwen.py stop      # stop this suite only
"""
# Change hardware here; these are elaboration settings, not prompt inputs.
CONFIG = (4, 4, 2, 8)  # B, RF, YS, shared DMA IDs
WEIGHTS = (8, 4, 2)
GROUP_SIZE = 128
MAKE_JOBS = 2
MAX_S = 256  # Shared-server guard; increase explicitly if larger prompts are needed.
# Full K/N dimensions; no external tiling or dimension reduction.
SHAPES = (('Q_O', 896, 896, 48, 'self_attn.q_proj'),
          ('K_V', 896, 128, 48, 'self_attn.k_proj'),
          ('GATE_UP', 896, 4864, 48, 'mlp.gate_proj'),
          ('DOWN', 4864, 896, 24, 'mlp.down_proj'))
CHECKPOINTS = {
 8: ('Qwen/Qwen2.5-0.5B-Instruct-GPTQ-Int8','c68601e5424e69cdaa6e073673e3c94db27b4397'),
 4: ('Qwen/Qwen2.5-0.5B-Instruct-GPTQ-Int4','c34a4a91629f09f73a285f32dbd26106b033c654'),
 2: ('irish-quant/Qwen-Qwen2.5-0.5B-Instruct-2bit','0cd638ce6ece6dbcf3a26f704f08710c42615587')}
import argparse,csv,datetime,fcntl,hashlib,importlib.util,json,os,re,shutil,subprocess,sys,time,traceback
from pathlib import Path
sys.dont_write_bytecode=True
os.environ.update(JAVA_TOOL_OPTIONS='-XX:ActiveProcessorCount=2',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
if hasattr(os,'sched_getaffinity'):os.sched_setaffinity(0,sorted(os.sched_getaffinity(0))[:2])
SELF=Path(__file__).resolve();CY=Path(os.environ.get('LUMAX_CHIPYARD','/home/kioannou/chipyard_1.13.0'));GEN=CY/'generators/LUMAX';ROOT=GEN/'verification/TEST_QWEN'
BASE=Path('/tmp/lumax-stable-baseline-20261114/frozen')
CACHE=Path('/tmp/lumax-test-qwen-checkpoints')
W8_GATE=GEN/'verification/TEST_GROUP_SCALING/runs/run_w8_deferred_gate/status.json'
STAGES=('load_x','generate','loadw_select','store_o')
def now():return datetime.datetime.now().astimezone().isoformat()
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for c in iter(lambda:f.read(1024*1024),b''):h.update(c)
 return h.hexdigest()
def digest(b):return hashlib.sha256(b).hexdigest()
def save(p,x):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(x,indent=2)+'\n');t.replace(p)
def load(n,p):
 s=importlib.util.spec_from_file_location(n,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def setting(s,k,v):
 s,n=re.subn(r'(?m)^(\s*'+k+r'\s*=\s*)(?:true|false|\d+)\b',lambda m:m[1]+str(v).lower(),s);assert n==1,k;return s
def locked(p):
 if not p.exists():return False
 with p.open('a') as f:
  try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);return False
  except BlockingIOError:return True
def plan(s):
 return dict(s=s,configuration=CONFIG,capacity=(max(s,CONFIG[2]),4864,4864),group_size=GROUP_SIZE,group_scale_deferred=True,weights=WEIGHTS,shapes=[dict(case=name,r=s,k=k,n=n,occurrences=count,tensor='model.layers.0.'+tensor) for name,k,n,count,tensor in SHAPES],matmuls_measured=4*len(WEIGHTS),projection_calls_estimated_per_precision=168,formula='48*C_Q_O + 48*C_K_V + 48*C_GATE_UP + 24*C_DOWN',excluded=['attention QK and AV','vocabulary head','normalization, RoPE, softmax, SiLU and bias','tokenization','CPU adapter, checkpoint downloads, build and Python reference time'],data='Real checkpoint coefficients/scales from layer 0; synthetic deterministic activations, not captured Qwen inference activations',timing='Measured controller-active cycles for four representative shapes, then weighted extrapolation; not an end-to-end inference measurement')
class Checkpoint:
 def __init__(self,wb,log):
  import requests
  self.requests=requests;self.wb=wb;self.repo,self.rev=CHECKPOINTS[wb];self.url=f'https://huggingface.co/{self.repo}/resolve/{self.rev}/model.safetensors';self.root=CACHE/self.rev;self.root.mkdir(parents=True,exist_ok=True);self.log=log
  raw=self.range(0,7);import struct
  self.length=struct.unpack('<Q',raw)[0];assert 0<self.length<2_000_000
  self.header=json.loads(self.range(8,self.length+7));self.base=8+self.length
  cfg=self.root/'config.json'
  if not cfg.exists():
   r=requests.get(f'https://huggingface.co/{self.repo}/resolve/{self.rev}/config.json',timeout=(15,60));r.raise_for_status();save(cfg,r.json())
  self.config=json.loads(cfg.read_text());q=self.config['quantization_config']
  assert q['bits']==wb and q['group_size']==GROUP_SIZE and q['sym'] is True
 def range(self,a,b):
  path=self.root/f'bytes_{a}_{b}.bin'
  if path.exists():
   raw=path.read_bytes();assert len(raw)==b-a+1;return raw
  for attempt in range(3):
   try:
    self.log(f'CHECKPOINT HTTP RANGE {self.repo}@{self.rev} bytes={a}:{b}')
    with self.requests.get(self.url+f'?test_qwen_range={a}-{b}',headers={'Range':f'bytes={a}-{b}'},timeout=(15,60),stream=True) as r:
     r.raise_for_status();assert r.status_code==206,'Server ignored Range request'
     assert r.headers.get('Content-Range','').startswith(f'bytes {a}-{b}/')
     raw=bytearray()
     for c in r.iter_content(1024*1024):
      raw.extend(c);assert len(raw)<=b-a+1
    assert len(raw)==b-a+1
    temp=path.with_suffix('.tmp');temp.write_bytes(raw);temp.replace(path);return bytes(raw)
   except Exception:
    if attempt==2:raise
    time.sleep(2)
 def tensor(self,key):
  import numpy as np
  m=self.header[key];a,b=m['data_offsets'];raw=self.range(self.base+a,self.base+b-1)
  if m['dtype']=='I32':return np.frombuffer(raw,dtype='<i4').reshape(m['shape'])
  if m['dtype']=='F16':return np.frombuffer(raw,dtype='<f2').reshape(m['shape'])
  if m['dtype']=='BF16':return (np.frombuffer(raw,dtype='<u2').astype(np.uint32)<<16).view(np.float32).reshape(m['shape'])
  raise ValueError('Unsupported checkpoint tensor dtype: '+str(m))
 def linear(self,name,k,n):
  import numpy as np
  qw=self.tensor(name+'.qweight').view(np.uint32);z=self.tensor(name+'.qzeros').view(np.uint32);gi=self.tensor(name+'.g_idx').astype(np.int64);sc=self.tensor(name+'.scales')
  pack=32//self.wb;mask=(1<<self.wb)-1;assert qw.shape==(k//pack,n) and gi.shape==(k,)
  zero=np.stack([(z>>(i*self.wb))&mask for i in range(pack)],axis=-1).reshape(z.shape[0],-1).astype(np.int16)+1 # GPTQ v1 stores zero-1
  assert sc.shape==(k//GROUP_SIZE,n) and zero.shape==sc.shape
  assert np.all(np.isfinite(sc))
  half=sc.astype(np.float16)
  # BF16 scales must be EXACTLY representable in FP16. Never silently round them.
  if not np.array_equal(half.astype(np.float32),sc.astype(np.float32)):raise ValueError('Checkpoint scales require lossy BF16/FP32 -> FP16 conversion; this exact test is blocked')
  idx=np.arange(k);codes=((qw[idx//pack,:]>>((idx%pack)*self.wb).astype(np.uint32)[:,None])&mask).astype(np.int16)
  signed=codes-zero[gi,:]
  assert signed.min()>=-(1<<(self.wb-1)) and signed.max()<(1<<(self.wb-1))
  perm=np.argsort(gi,kind='stable');assert np.array_equal(gi[perm],np.arange(k)//GROUP_SIZE),'Group membership cannot be made regular G128 by K permutation'
  metadata=dict(checkpoint=self.repo,revision=self.rev,tensor=name,declared_dtype=self.config.get('torch_dtype'),scale_dtype=self.header[name+'.scales']['dtype'],scales_exact_fp16=True,desc_act=self.config['quantization_config'].get('desc_act'),k_permuted=not np.array_equal(perm,idx),q_sha256=digest(signed.astype(np.int8).tobytes()),scale_sha256=digest(half.astype('<f2').tobytes()),group_index_sha256=digest(gi.astype('<i4').tobytes()),permutation_sha256=digest(perm.astype('<i4').tobytes()),zero_percent=100*float((signed==0).mean()),scale_min=float(half.min()),scale_max=float(half.max()))
  return signed[perm,:],half,perm,metadata

def report(p,records,state):
 result=[]
 for wb in WEIGHTS:
  rows=[r for r in records if r['wb']==wb];done=len(rows)==4 and all(r['status']=='PASS' for r in rows)
  row=dict(wb=wb,status='PASS' if done else 'INCOMPLETE',completed=sum(r['status']=='PASS' for r in rows),tests=4)
  if done:
   for key in (*STAGES,'cycles'):row[key]=sum(r['occurrences']*int(r[key]) for r in rows)
   row['projection_matmuls']=168
  result.append(row)
 save(p/'prompt_estimate.json',dict(s=state['s'],estimate='projection controller-active cycles only',weights=result,formula=plan(state['s'])['formula']))
 lines=['# TEST_QWEN: measured shapes and prompt projection estimate','',f'S={state["s"]}. Status: {state["status"]}. Updated {now()}.','', 'Real make-built Verilator runs compiled RISC-V ELFs. Real layer-0 Qwen checkpoint coefficients, scales and decoded GPTQ-v1 zero points; deterministic synthetic activations in the common exact FP16/BF16 value range. This is not model inference. Activation K entries are permuted with the weights when g_idx requires it. Scale conversion must be exact; otherwise the affected test is blocked. LUMAX rounds its exact scaled sum once to FP32, which does not guarantee bit identity with native GPU intermediate rounding.', '', '| W bits | Shape | X | W | G128 groups | Measured active cycles | Result |','|---:|---|---|---|---:|---:|---|']
 for r in records:lines.append(f'| {r["wb"]} | {r["case"]} | {r["r"]}x{r["k"]} | {r["k"]}x{r["m"]} | {r["k"]//128} | {r.get("cycles","—")} | {r["status"]} |')
 lines+=['','## Estimated prompt projection cycles','','`C_prompt = 48*C_Q_O + 48*C_K_V + 48*C_GATE_UP + 24*C_DOWN`','','| W bits | Passed measured shapes | Estimated cycles for 168 projections |','|---:|---:|---:|']
 for r in result:lines.append(f'| {r["wb"]} | {r["completed"]}/4 | {r.get("cycles","withheld until all four pass")} |')
 lines+=['','Only four shapes per precision are measured. This assumes other layers/projections of the same shape have the same active-cycle cost as the chosen representative; different cache/address behavior is not measured. All optional data-dependent zero skipping is disabled. Metadata preloading is included in LoadX for every measured matmul. Weight-loading and selection overlap: use the combined counter, not their sum. The scale/group pipeline drain is included in the combined stage.', '', 'The estimate excludes attention QK/AV, the vocabulary projection, normalization/RoPE/softmax/SiLU/bias, CPU adapter and preload-command overhead, builds, downloads, tokenization and Python reference time. Layer-to-layer FP32-to-FP16 conversion is not simulated. It is not an end-to-end prompt latency estimate. Native W2 declares BF16; this test runs an explicitly labelled FP16 execution variant using its exact representable scales and group membership.', '', 'Builds and datasets are private/frozen. Larger K/N capacities change the physical design relative to the prior 1024-capacity FPGA comparison. No Vivado or Python cycle-model predictions are produced here. Synthetic activations avoid NaN/Inf and are exactly representable in FP16 and BF16; checkpoint scales must also be finite. See individual hardware output/reference bits, guards, simulator/ELF hashes and commands in verilator.log; source provenance, pinned revisions and coefficient hashes are recorded separately.', '']
 (p/'report.md').write_text('\n'.join(lines))
def worker(p):
 with (p/'worker.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  options=json.loads((p/'plan.json').read_text());s=options['s'];state=dict(status='RUNNING',phase='preflight',s=s,pid=os.getpid());records=[]
  def update(phase=None):
   if phase:state['phase']=phase
   state['updated']=now();save(p/'status.json',state);save(p/'records.json',records);report(p,records,state)
  try:
   import numpy as np
   frozen=p/'frozen'
   for rel,h in json.loads((frozen/'manifest.json').read_text()).items():assert sha(frozen/rel)==h,rel
   if 8 in WEIGHTS:
    update('Waiting for W8 group-scaling RTL gates')
    while W8_GATE.exists() and json.loads(W8_GATE.read_text())['status']=='RUNNING':
     if (p/'stop.request').exists():raise InterruptedError('Stop requested')
     time.sleep(15)
    if not W8_GATE.exists() or json.loads(W8_GATE.read_text())['status']!='PASS':raise RuntimeError('W8 group-scaling gates have not passed; W8 full-shape tests blocked')
   sys.path.insert(0,str(GEN/'verification'))
   l=load('qwen_actual_rtl',frozen/'run_lumax_weight_loader.py');l.MAKE_JOBS=MAKE_JOBS;l.SIM_TIMEOUT=24*3600
   ref=load('qwen_exact_reference',frozen/'group_scale_reference.py')
   work=Path('/tmp')/('lumax-test-qwen-'+p.name);work.mkdir(exist_ok=True);l.CACHE=Path('/tmp/lumax-test-qwen-build-cache')
   src=work/'source';src.mkdir(exist_ok=True);shutil.copytree(frozen/'scala',src/'scala',dirs_exist_ok=True);shutil.copytree(frozen/'software',src/'software',dirs_exist_ok=True)
   cfg=src/'scala/Config.scala';text=cfg.read_text()
   for k,v in dict(GROUP_SCALE_ALIGNED=False,GROUP_SCALE_DEFERRED=True,FP16_ACTIVATIONS=True,BRAM_OUTPUTS=True,SCALE=False,FLEXIBLE_BANK_GROUPING=False,SKIP_ZERO_WEIGHTS=False,WEIGHT_DMA_BURSTS=False,EARLY_WEIGHT_BUFFER_RELEASE=False,DMA_Bytes=8).items():text=setting(text,k,v)
   cfg.write_text(text)
   for wb in WEIGHTS:
    for i,(name,k,n,count,tensor) in enumerate(SHAPES,1):
     r=l.record(CONFIG,(s,k,n),wb,'QWEN',i,'fp16');r.update(case=name,occurrences=count,group_size=GROUP_SIZE,group_mode='deferred');records.append(r)
   save(work/'options.json',dict(output=str(p),records=records,configurations=[CONFIG],capacity=options['capacity'],jars=json.loads((BASE.parent/'options.json').read_text())['jars'],source_sha256={str(q.relative_to(src)):sha(q) for q in src.rglob('*') if q.is_file()},base_assembly_sha256=sha(BASE/'chipyard.jar')))
   if not (work/'chipyard.jar').exists():(work/'chipyard.jar').symlink_to(BASE/'chipyard.jar')
   scales_for_case={};dataset={};original_source=l.test_source
   def operands(item):
    run.phase='Checkpoint download / exact host reference';run.current=f'W{item["wb"]} {item["case"]}';run.checkpoint();update(f'Checkpoint/reference W{item["wb"]} {item["case"]}')
    model=dataset.setdefault(item['wb'],None)
    if model is None:model=Checkpoint(item['wb'],run.log);dataset[item['wb']]=model
    shape=next(t for t in SHAPES if t[0]==item['case']);_,k,n,_,tensor=shape
    q,scales,perm,meta=model.linear('model.layers.0.'+tensor,k,n);item.update(meta)
    seed=int.from_bytes(hashlib.sha256(f'qwen-common-input:{s}:{item["case"]}'.encode()).digest()[:8],'little');item['seed_x']=seed
    rng=np.random.default_rng(seed);xf=(rng.integers(-128,129,size=(s,k)).astype(np.float32)/64).astype(np.float16)[:,perm];bits=xf.astype('<f2').view('<u2')
    assert np.any(xf!=0) and np.any(q!=0)
    fixed=(xf.astype(np.float64)*(1<<24)).astype(np.int64);acc=[[0]*n for _ in range(s)];scale_bits=scales.astype('<f2').view('<u2')
    for g in range(k//GROUP_SIZE):
     part=fixed[:,g*GROUP_SIZE:(g+1)*GROUP_SIZE]@q[g*GROUP_SIZE:(g+1)*GROUP_SIZE,:].astype(np.int64)
     # These bounded activations keep local partials well inside int64.
     for j in range(n):
      sf=ref.fp16_fixed24(int(scale_bits[g,j]))
      for row in range(s):acc[row][j]+=int(part[row,j])*sf
    expected=[ref.fixed_to_fp32_bits(v,48) for row in acc for v in row]
    flat=(q.T.reshape(-1)&((1<<item['wb'])-1)).astype(np.uint8);per_byte=8//item['wb']
    assert flat.size%per_byte==0
    packed=np.zeros(flat.size//per_byte,dtype=np.uint8)
    for i in range(per_byte):packed|=flat[i::per_byte]<<(i*item['wb'])
    x=bits.reshape(-1).tolist();item['x_sha256']=digest(bits.tobytes());item['w_sha256']=digest(q.astype(np.int8).tobytes());item['packed_sha256']=digest(packed.tobytes());item['scale_sha256']=digest(scale_bits.tobytes());scales_for_case[(item['wb'],item['case'])]=scale_bits.reshape(-1).tolist()
    save(p/'checkpoint_provenance.json',[{k:v for k,v in r.items() if k not in ('reason',)} for r in records if 'checkpoint' in r])
    return x,packed.tobytes(),expected
   def source(base,items,values):
    text=original_source(base,items,values);before,body=text.split('int main(void)',1)
    for i,item in enumerate(items):
     scales=scales_for_case[(item['wb'],item['case'])];scales+=([0]*((-len(scales))%4))
     before+='static const uint16_t scale'+str(i)+'[] __attribute__((aligned(64)))={'+','.join(map(str,scales))+'};\n'
     marker=f' QuantizedTensor_input tx={{.q=(input_t*)x{i}.data}};';assert marker in body
     body=body.replace(marker,f' if(lumax_set_group_scales(scale{i},128)) return 20;\n'+marker)
    return before+'int main(void)'+body
   l.operands=operands;l.test_source=source
   class Runner(l.Runner):
    def report(self):
     if hasattr(self,'records'):report(p,self.records,state)
    def guard(self):
     if (p/'stop.request').exists():raise InterruptedError('Stop requested for TEST_QWEN')
   run=Runner(work);records=run.records;update('Building real Verilator');sim,snapshot=run.build(CONFIG,'fp16');shutil.copyfile(snapshot,p/'Config.scala')
   for item in records:
    run.guard();update(f'Running W{item["wb"]} {item["case"]} in Verilator');run.execute([item],sim,snapshot);update()
    if item['status']!='PASS':raise RuntimeError('Hardware/reference mismatch: '+item['case'])
    scales_for_case.clear()
   run.checkpoint('PASS');state['status']='PASS';update('Complete')
  except BaseException as e:
   traceback.print_exc();state.update(status='STOPPED' if isinstance(e,InterruptedError) else 'FAILED',reason=str(e));update('Stopped');raise

def selftest():
 import numpy as np
 ref=load('qwen_adapter_ref',GEN/'verification/TEST_GROUP_SCALING/group_scale_reference.py')
 checks=0
 for wb in (8,4,2):
  k,n,r=256,16,3;pack=32//wb;mask=(1<<wb)-1;zero=1<<(wb-1)
  rng=np.random.default_rng(wb);codes=rng.integers(0,1<<wb,size=(k,n),dtype=np.uint32)
  qw=np.zeros((k//pack,n),dtype=np.uint32)
  for i in range(pack):qw|=codes[i::pack]<<(i*wb)
  z=np.zeros((2,n//pack),dtype=np.uint32)
  for i in range(pack):z|=np.uint32(zero-1)<<np.uint32(i*wb)
  gi=np.arange(k,dtype=np.int32)%2;scale=np.tile(np.array([[.25],[.5]],dtype=np.float16),(1,n))
  name='test';data={name+'.qweight':qw.view(np.int32),name+'.qzeros':z.view(np.int32),name+'.g_idx':gi,name+'.scales':scale}
  model=Checkpoint.__new__(Checkpoint);model.wb=wb;model.repo='selftest';model.rev='selftest';model.config={'torch_dtype':'float16','quantization_config':{'desc_act':True}};model.header={name+'.scales':{'dtype':'F16'}};model.tensor=lambda key:data[key]
  q,sc,perm,meta=model.linear(name,k,n);assert meta['k_permuted'];assert np.array_equal(q,(codes.astype(np.int16)-zero)[perm])
  x=(rng.integers(-128,129,size=(r,k)).astype(np.float32)/64).astype(np.float16)
  expected=x.astype(np.float64)@((codes.astype(np.int16)-zero).astype(np.float64)*scale[gi].astype(np.float64))
  bits=x[:,perm].view(np.uint16).tolist();actual=ref.reference(bits,q.T.tolist(),sc.view(np.uint16).tolist(),128)
  target=expected.astype(np.float32).view(np.uint32).tolist();assert actual==target
  checks+=r*n
 assert sum(x[3] for x in SHAPES)==168
 # Never allow an inexact scale conversion merely to make a test run.
 data[name+'.scales']=np.full((2,n),2.0**-30,dtype=np.float32)
 try:model.linear(name,k,n)
 except ValueError:pass
 else:raise AssertionError('Inexact FP16 scale conversion was accepted')
 print(f'PASS: {checks} exact adapter outputs (W8/W4/W2, reordered groups), GPTQ-v1 zero decoding, lossy-scale rejection, 168-call formula')

def main():
 a=argparse.ArgumentParser(description=__doc__);a.add_argument('command',help='S, start, plan, status, stop or worker');a.add_argument('s',nargs='?',type=int);a.add_argument('--run',type=Path);args=a.parse_args();ROOT.mkdir(exist_ok=True)
 if args.command=='worker':worker(args.run);return
 if args.command=='selftest':selftest();return
 if args.command in ('status','stop'):
  p=args.run or Path(json.loads((ROOT/'latest.json').read_text())['run'])
  if args.command=='stop':(p/'stop.request').touch();print('Stop requested for this suite only:',p);return
  print(p);print('Worker active:',locked(p/'worker.lock'));print((p/'status.json').read_text());print((p/'prompt_estimate.json').read_text())
  q=Path('/tmp')/('lumax-test-qwen-'+p.name)/'status.json'
  if q.exists():print('CURRENT VERILATOR:',q.read_text())
  return
 s=int(args.command) if args.command.isdigit() else args.s
 if not s or not 1<=s<=MAX_S:a.error(f'Supply S between 1 and {MAX_S}, e.g. run_test_qwen.py 8; MAX_S is editable at the top')
 if args.command=='plan':print(json.dumps(plan(s),indent=2));return
 if not args.command.isdigit() and args.command!='start':a.error('Unknown command')
 with (ROOT/'start.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  if (ROOT/'latest.json').exists():
   old=Path(json.loads((ROOT/'latest.json').read_text())['run'])
   if locked(old/'worker.lock'):raise SystemExit('Existing TEST_QWEN worker active: '+str(old))
  p=ROOT/'runs'/time.strftime('run_%Y%m%d_%H%M%S');p.mkdir(parents=True);frozen=p/'frozen';frozen.mkdir();shutil.copytree(GEN/'src/main/scala',frozen/'scala');(frozen/'software').mkdir()
  for n in ('Linear-sw.c','build.sh','rocc.h','compiler.h'):shutil.copyfile(GEN/'software/tests/src'/n,frozen/'software'/n)
  shutil.copyfile(GEN/'verification/TEST_LLMS/run_lumax_weight_loader.py',frozen/'run_lumax_weight_loader.py');shutil.copyfile(GEN/'verification/TEST_GROUP_SCALING/group_scale_reference.py',frozen/'group_scale_reference.py');shutil.copyfile(SELF,p/'tested_runner.py')
  save(frozen/'manifest.json',{str(q.relative_to(frozen)):sha(q) for q in frozen.rglob('*') if q.is_file()});save(p/'plan.json',plan(s));save(p/'status.json',dict(status='QUEUED',phase='Starting',s=s));report(p,[],dict(status='QUEUED',s=s))
  with (p/'runner.log').open('ab') as log:proc=subprocess.Popen([sys.executable,'-u','-B',p/'tested_runner.py','worker','--run',p],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  save(ROOT/'latest.json',dict(run=str(p),pid=proc.pid));print('Started detached worker',proc.pid);print('Run:',p)
if __name__=='__main__':main()
