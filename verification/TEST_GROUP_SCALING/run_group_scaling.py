#!/usr/bin/env python3
"""Small real RoCC/Verilator tests: disabled, aligned and deferred group scaling."""
CONFIG=(4,4,2,8)
CAPACITY=(5,257,17)
SHAPES=[(2,17,5),(5,65,9),(3,129,7),(4,257,9)]
MODES=('off','aligned','deferred')
import os
os.environ.update(JAVA_TOOL_OPTIONS='-XX:ActiveProcessorCount=2',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
os.sched_setaffinity(0,sorted(os.sched_getaffinity(0))[:2])
import argparse,importlib.util,json,shutil,hashlib,random,re,subprocess,sys,time,traceback
from pathlib import Path
sys.dont_write_bytecode=True
SELF=Path(__file__).resolve();ROOT=SELF.parent;CY=Path(os.environ.get('LUMAX_CHIPYARD','/home/kioannou/chipyard_1.13.0'));GEN=CY/'generators/LUMAX';V=GEN/'verification'
sys.path.insert(0,str(V))
def load(n,p):
 s=importlib.util.spec_from_file_location(n,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
l=load('scale_rtl',V/'TEST_LLMS/run_lumax_weight_loader.py');l.MAKE_JOBS=2
F=Path('/tmp/lumax-stable-baseline-20261114/frozen');REF=ROOT/'group_scale_reference.py'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):
 q=p.with_suffix('.tmp');q.write_text(json.dumps(x,indent=2)+'\n');q.replace(p)
def setting(s,k,v):
 s,n=re.subn(r'(?m)^(\s*'+k+r'\s*=\s*)(?:true|false|\d+)\b',lambda m:m[1]+str(v).lower(),s);assert n==1,k;return s
original_operands=l.operands;original_source=l.test_source
reference=load('group_exact_reference',REF)
def operands(item):
 x,packed,expected=original_operands(item)
 if not item['group_mode']:return x,packed,expected
 r,k,n,wb=(item[t] for t in ('r','k','m','wb'));g=item['group_size'];rng=random.Random(item['seed_x']^g)
 scales=[rng.choice([0,1,1023,1024,0x3c00,0x3800,0xbc00,0x7bff,0x8001]) for _ in range(((k+g-1)//g)*n)]
 item['scale_bits']=scales;item['scale_sha256']=hashlib.sha256(bytes().join(v.to_bytes(2,'little') for v in scales)).hexdigest()
 q=[]
 for col in range(n):
  a=[]
  for j in range(k):
   bit=(col*k+j)*wb;v=(packed[bit//8]>>(bit%8))&((1<<wb)-1);a.append(v-(1<<wb) if v&(1<<(wb-1)) else v)
  q.append(a)
 ss=[scales[i:i+n] for i in range(0,len(scales),n)]
 expected=sum(reference.reference([x[i*k:(i+1)*k] for i in range(r)],q,ss,g),[])
 return x,packed,expected
l.operands=operands
def test_source(base,items,values):
 text=original_source(base,items,values)
 if not items[0]['group_mode']:return text
 before,body=text.split('int main(void)',1)
 for i,item in enumerate(items):
  padded=item['scale_bits']+[0]*((-len(item['scale_bits']))%4)
  before+='static const uint16_t scales'+str(i)+'[] __attribute__((aligned(64)))={'+','.join(map(str,padded))+'};\n'
  marker=f' QuantizedTensor_input tx={{.q=(input_t*)x{i}.data}};'
  assert marker in body
  body=body.replace(marker,f' if(lumax_set_group_scales(scales{i},{item["group_size"]})) return 20;\n'+marker)
 return before+'int main(void)'+body
l.test_source=test_source
class Runner(l.Runner):
 def report(self):pass

def worker(p):
 w=Path('/tmp')/('lumax-group-scaling-'+p.name);w.mkdir(exist_ok=True);l.CACHE=w/'cache'
 state=dict(status='RUNNING',mode='starting',pid=os.getpid());save(p/'status.json',state)
 try:
  for mode in MODES:
   state['mode']=mode;save(p/'status.json',state)
   out=p/mode;out.mkdir(exist_ok=True);work=w/mode;work.mkdir(exist_ok=True);src=work/'source';src.mkdir(exist_ok=True)
   shutil.copytree(p/'source',src/'scala',dirs_exist_ok=True);shutil.copytree(p/'software',src/'software',dirs_exist_ok=True)
   cfg=src/'scala/Config.scala';s=cfg.read_text()
   for k,v in dict(GROUP_SCALE_ALIGNED=mode=='aligned',GROUP_SCALE_DEFERRED=mode=='deferred',FLEXIBLE_BANK_GROUPING=False,SKIP_ZERO_WEIGHTS=False,WEIGHT_DMA_BURSTS=False,EARLY_WEIGHT_BUFFER_RELEASE=False).items():s=setting(s,k,v)
   cfg.write_text(s)
   if not (work/'chipyard.jar').exists():(work/'chipyard.jar').symlink_to(F/'chipyard.jar')
   records=[]
   for wb in (2,4):
    for g in ((32,64,128) if mode!='off' else (128,)):
     for i,shape in enumerate(SHAPES,1):
      r=l.record(CONFIG,shape,wb,'GROUP',i,'fp16');r.update(case=f'G{g}_{i}',group_mode=mode!='off',group_size=g);records.append(r)
   jars=json.loads((F.parent/'options.json').read_text())['jars']
   save(work/'options.json',dict(output=str(out),records=records,configurations=[CONFIG],jars=jars,source_sha256={str(q.relative_to(src)):sha(q) for q in src.rglob('*') if q.is_file()},base_assembly_sha256=sha(F/'chipyard.jar'),capacity=CAPACITY))
   run=Runner(work);sim,snapshot=run.build(CONFIG,'fp16');shutil.copyfile(snapshot,out/'Config.scala')
   for wb in (2,4):
    batch=[r for r in run.records if r['wb']==wb];run.execute(batch,sim,snapshot);save(out/'records.json',run.records)
    assert all(r['status']=='PASS' for r in batch),[(r['case'],r['status']) for r in batch]
   run.checkpoint('PASS')
  state.update(status='PASS',mode='complete');save(p/'status.json',state)
 except BaseException as ex:
  traceback.print_exc();state.update(status='FAILED',reason=str(ex));save(p/'status.json',state);raise

def main():
 global CONFIG,CAPACITY,SHAPES,MODES
 a=argparse.ArgumentParser();a.add_argument('command',choices=['start','status','worker']);a.add_argument('--run',type=Path);a.add_argument('--boundary',action='store_true',help='B3/RF4/YS1/ID2, K511 counter/bank boundary checks');args=a.parse_args()
 if args.boundary:CONFIG=(3,4,1,2);CAPACITY=(1,511,7);SHAPES=[(1,511,3)];MODES=('aligned','deferred')
 if args.command=='worker':worker(args.run);return
 if args.command=='status':
  p=Path(json.loads((ROOT/'latest.json').read_text())['run']);print(p);print((p/'status.json').read_text());return
 p=ROOT/'runs'/time.strftime(('run_boundary_' if args.boundary else 'run_')+'%Y%m%d_%H%M%S');p.mkdir(parents=True);shutil.copytree(GEN/'src/main/scala',p/'source');(p/'software').mkdir()
 for n in ('Linear-sw.c','build.sh','rocc.h','compiler.h'):shutil.copyfile(GEN/'software/tests/src'/n,p/'software'/n)
 shutil.copyfile(SELF,p/'tested_runner.py');shutil.copyfile(REF,p/'group_scale_reference.py')
 with (p/'runner.log').open('ab') as log:proc=subprocess.Popen([sys.executable,'-u','-B',SELF,'worker','--run',p]+(['--boundary'] if args.boundary else []),stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 save(ROOT/'latest.json',dict(run=str(p),pid=proc.pid));print('Started',proc.pid,p)
if __name__=='__main__':main()
