import time,json,importlib.util
from pathlib import Path
p=Path('/home/kioannou/chipyard_1.13.0/generators/LUMAX/verification/TEST_LLMS_BANK_GROUPING/runs/run_20261117_084821')
while True:
 state=json.loads((p/'status.json').read_text())
 if state['status'] not in ('RUNNING','QUEUED'):break
 time.sleep(15)
s=importlib.util.spec_from_file_location('final_grouping_report',p.parent.parent/'run_comparison.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
b=m.Batch(p);b.report()
with (p/'REPORT.md').open('a') as out:
 for name,title in [('special_checks.json','Additional FP16 special-value checks'),('dma_precheck.json','Additional 8-byte DMA prechecks')]:
  q=p/name
  if q.exists():out.write('\n## '+title+'\n\n```json\n'+q.read_text()+'```\n')
 out.write('\nFinal worker status: '+state['status']+'.\n')
print('Final comparison regenerated:',state['status'],flush=True)
