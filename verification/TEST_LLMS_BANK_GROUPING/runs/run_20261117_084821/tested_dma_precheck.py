from pathlib import Path
import importlib.util,json,traceback
p=Path('/home/kioannou/chipyard_1.13.0/generators/LUMAX/verification/TEST_LLMS_BANK_GROUPING/runs/run_20261117_084821')
s=importlib.util.spec_from_file_location('dma8_precheck',p.parent.parent/'run_comparison.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
b=m.Batch(p);b.report=lambda:None
old=m.gem_records;m.gem_records=lambda job:[r for r in old(job) if r['case'].startswith('GATE')]
checks={}
try:
 for wb in (8,2):
  job=dict(name=f'dma_precheck_gemmini_d8_w{wb}',kind='gemmini',dim=8,wb=wb)
  print('Starting',job['name'],flush=True);b.gemmini(job)
  checks[job['name']]={'status':'PASS','tests':2};m.save(p/'dma_precheck.json',checks)
  print('PASS',job['name'],flush=True)
except BaseException as ex:
 traceback.print_exc();checks['failure']=str(ex);m.save(p/'dma_precheck.json',checks)
 current=json.loads((p/'status.json').read_text()).get('current')
 if current:
  state=b.w/current;state.mkdir(exist_ok=True);(state/'stop.request').write_text('Gemmini 8-byte DMA precheck failed; inspect dma_precheck.log.\n')
 raise
