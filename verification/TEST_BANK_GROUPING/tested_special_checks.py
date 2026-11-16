import importlib.util,json,random,hashlib,sys,struct,os
from pathlib import Path
p=Path('/home/kioannou/chipyard_1.13.0/generators/LUMAX/verification/TEST_BANK_GROUPING/runs/run_20261116_213934')
s=importlib.util.spec_from_file_location('special_grouping',p.parents[1]/'run_test_bank_grouping.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def operands(item):
 r,k,n=(item[t] for t in ('r','k','m'));wb=item['wb'];lo=-(1<<(wb-1));hi=(1<<(wb-1))-1
 x=[0x7c00,0xfc00,0x3c00,0,0x8000,1,0x7bff,0x400]*r
 x[8:16]=[0x7e01,0x7c01,0x3c00,1,0,0,0,0]
 x[16:24]=[0x3c00,0xbc00,0,0x8000,1,0x8001,0x7bff,0xfbff]
 x[24:32]=[1,0x8001,0x400,0x8400,1,0x8001,0,0]
 x[32:40]=[0x7bff,0xfbff,0x3c00,0xbc00,0,1,1,0]
 w=([0,1,-1,lo,hi,1,-1,0]*k)
 for label,vals in [('x',x),('w',w)]:item[label+'_sha256']=hashlib.sha256(json.dumps(vals).encode()).hexdigest()
 expected=[]
 for row in range(r):expected.extend(m.l.fp.reference(x[row*k:(row+1)*k],w,n,True))
 packed=bytearray((k*n*wb+7)//8)
 for col in range(n):
  for j in range(k):
   bit=(col*k+j)*wb;packed[bit//8]|=(w[j*n+col]&((1<<wb)-1))<<(bit%8)
 return x,packed,expected
m.l.operands=operands
summary={}
for enabled in (False,True):
 name='flag_on' if enabled else 'flag_off';srcstate=p/'work'/name;state=p/'work'/('special_'+name);state.mkdir(exist_ok=True);out=p/('special_'+name);out.mkdir(exist_ok=True)
 (state/'source').symlink_to(srcstate/'source',target_is_directory=True) if not (state/'source').exists() else None
 options=json.loads((srcstate/'options.json').read_text());records=[]
 for wb in (8,4,2):
  for ys in ((1,2,4) if enabled else (4,)):
   records.append(m.l.record((8//ys,4,ys,8),(5,8,8),wb,'FPEX',1,'fp16'))
 options.update(output=str(out),records=records);m.save(state/'options.json',options)
 sim=next(q for q in (p/'work/cache').glob('*/simulator-LUMAXROcketConfig') if ('FLEXIBLE_BANK_GROUPING = '+str(enabled).lower()) in (q.parent/'Config.scala').read_text())
 run=m.Runner(state)
 for wb in (8,4,2):
  batch=[r for r in run.records if r['wb']==wb];run.execute(batch,sim,sim.parent/'Config.scala');assert all(r['status']=='PASS' for r in batch),batch
 run.checkpoint('PASS');m.save(out/'records.json',run.records);summary[name]=dict(passes=len(run.records),status='PASS')
m.save(p/'special_checks.json',summary)
print(summary,flush=True)
