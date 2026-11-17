import os
os.environ.update(JAVA_TOOL_OPTIONS='-XX:ActiveProcessorCount=2',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:2])
import importlib.util,json,time,shutil,traceback
from pathlib import Path
base=Path('/home/kioannou/chipyard_1.13.0/generators/LUMAX/verification/TEST_GROUP_SCALING')
first=base/'runs/run_20261117_200641'
s=importlib.util.spec_from_file_location('group_rtl',base/'run_group_scaling.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
p=base/'runs/run_boundary_k511';p.mkdir(exist_ok=True)
m.save(p/'status.json',dict(status='WAITING',reason='Wait for main group-scaling checks; keep builds sequential',pid=os.getpid()))
while json.loads((first/'status.json').read_text())['status']=='RUNNING':time.sleep(15)
if json.loads((first/'status.json').read_text())['status']!='PASS':
 m.save(p/'status.json',dict(status='BLOCKED',reason='Initial group-scaling checks did not pass'));raise SystemExit(1)
try:
 w=Path('/tmp/lumax-group-scaling-boundary-k511');w.mkdir(exist_ok=True);m.l.CACHE=w/'cache'
 for mode in ('aligned','deferred'):
  m.save(p/'status.json',dict(status='RUNNING',mode=mode,pid=os.getpid()))
  out=p/mode;out.mkdir(exist_ok=True);work=w/mode;work.mkdir(exist_ok=True);src=work/'source';src.mkdir(exist_ok=True)
  shutil.copytree(m.GEN/'src/main/scala',src/'scala',dirs_exist_ok=True);(src/'software').mkdir(exist_ok=True)
  for n in ('Linear-sw.c','build.sh','rocc.h','compiler.h'):shutil.copyfile(m.GEN/'software/tests/src'/n,src/'software'/n)
  cfg=src/'scala/Config.scala';text=cfg.read_text()
  for k,v in dict(GROUP_SCALE_ALIGNED=mode=='aligned',GROUP_SCALE_DEFERRED=mode=='deferred',FLEXIBLE_BANK_GROUPING=False,SKIP_ZERO_WEIGHTS=False,WEIGHT_DMA_BURSTS=False,EARLY_WEIGHT_BUFFER_RELEASE=False).items():text=m.setting(text,k,v)
  cfg.write_text(text)
  config=(3,4,1,2)
  if not (work/'chipyard.jar').exists():(work/'chipyard.jar').symlink_to(m.F/'chipyard.jar')
  records=[]
  for wb in (2,4):
   for g in (32,64,128):
    r=m.l.record(config,(1,511,3),wb,'BOUNDARY',1,'fp16');r.update(case=f'G{g}_K511',group_mode=True,group_size=g);records.append(r)
  m.save(work/'options.json',dict(output=str(out),records=records,configurations=[config],jars=json.loads((m.F.parent/'options.json').read_text())['jars'],source_sha256={str(q.relative_to(src)):m.sha(q) for q in src.rglob('*') if q.is_file()},base_assembly_sha256=m.sha(m.F/'chipyard.jar'),capacity=(1,511,7)))
  run=m.Runner(work);sim,snapshot=run.build(config,'fp16');shutil.copyfile(snapshot,out/'Config.scala')
  for wb in (2,4):
   batch=[r for r in run.records if r['wb']==wb];run.execute(batch,sim,snapshot);m.save(out/'records.json',run.records)
   assert all(r['status']=='PASS' for r in batch),[(r['case'],r['status']) for r in batch]
  run.checkpoint('PASS')
 m.save(p/'status.json',dict(status='PASS',mode='complete',pid=os.getpid()))
except BaseException as ex:
 traceback.print_exc();m.save(p/'status.json',dict(status='FAILED',reason=str(ex),pid=os.getpid()));raise
