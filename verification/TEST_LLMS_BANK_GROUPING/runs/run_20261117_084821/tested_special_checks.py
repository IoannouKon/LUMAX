import importlib.util,json,time,hashlib,struct,sys,shutil,traceback
from pathlib import Path
p=Path('/home/kioannou/chipyard_1.13.0/generators/LUMAX/verification/TEST_LLMS_BANK_GROUPING/runs/run_20261117_084821')
s=importlib.util.spec_from_file_location('special_arbitrary',p.parent.parent/'run_comparison.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def operands(item):
 r,k,n=(item[t] for t in ('r','k','m'));wb=item['wb'];x=[0x3c00]*(r*k)
 for row,bits in enumerate((0x7c00,0x7e01,0x8001,0x7bff,0xfc00)):x[row*k]=bits
 w=[]
 for j in range(k):w.extend([0,int(j==0),-int(j==0),-(1<<(wb-1)),(1<<(wb-1))-1,0,1,-1])
 expected=[]
 for row in range(r):expected.extend(m.l.fp.reference(x[row*k:(row+1)*k],w,n,True))
 packed=bytearray((k*n*wb+7)//8)
 for col in range(n):
  for j in range(k):
   bit=(col*k+j)*wb;packed[bit//8]|=(w[j*n+col]&((1<<wb)-1))<<(bit%8)
 for label,a in [('x',x),('w',w)]:item[label+'_sha256']=hashlib.sha256(json.dumps(a).encode()).hexdigest()
 return x,packed,expected
m.l.operands=operands
summary={}
try:
 for b,ys in [(5,4),(14,8)]:
  job=f'lumax_b{b}_rf16_ys{ys}_id16_flex1';w=Path('/tmp')/('lumax-arbitrary-grouping-'+p.name);main=w/job
  # Reuse the real full-capacity make-built simulator after its build marker.
  while True:
   if (main/'options.json').exists():
    opts=json.loads((main/'options.json').read_text());hashes=opts['source_sha256']
    candidates=[]
    for marker in (w/'lumax_cache').glob('*/success.json'):
     info=json.loads(marker.read_text())
     cfg=marker.parent/'Config.scala'
     if info.get('source_sha256')==hashes and f'banks_per_row         = {b},' in cfg.read_text() and f'parallel_rows         = {ys},' in cfg.read_text():candidates.append(marker.parent)
    if candidates:break
   state=json.loads((p/'status.json').read_text())
   if state['status']=='FAILED':raise RuntimeError('Main batch failed before special tests: '+state.get('reason',''))
   time.sleep(15)
  cache=candidates[0];sim=cache/'simulator-LUMAXROcketConfig';state=w/('special_'+job);state.mkdir(exist_ok=True);out=p/('special_'+job);out.mkdir(exist_ok=True)
  if not (state/'source').exists():(state/'source').symlink_to(main/'source',target_is_directory=True)
  records=[m.l.record((b*ys//y,16,y,16),(5,8,8),wb,'FPEX',1,'fp16') for wb in (8,4,2) for y in [1<<i for i in range(ys.bit_length())]]
  opts.update(output=str(out),records=records);m.save(state/'options.json',opts)
  m.bank.CONFIG=(b,16,ys,16);r=m.LumaxRunner(state)
  for wb in (8,4,2):
   batch=[v for v in r.records if v['wb']==wb];r.execute(batch,sim,cache/'Config.scala');assert all(v['status']=='PASS' for v in batch),batch
  r.checkpoint('PASS');m.save(out/'records.json',r.records);summary[job]={'status':'PASS','passes':len(records)};m.save(p/'special_checks.json',summary)
  print(job,'special checks PASS',len(records),flush=True)
except BaseException as ex:
 traceback.print_exc();summary['failure']=str(ex);m.save(p/'special_checks.json',summary)
 status=json.loads((p/'status.json').read_text());current=status.get('current')
 if current:
  state=Path('/tmp')/('lumax-arbitrary-grouping-'+p.name)/current;state.mkdir(exist_ok=True);(state/'stop.request').write_text('Special FP16 grouping test failed; inspect special_runner.log.\n')
 raise
