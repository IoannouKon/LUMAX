"""Saved timing comparison, source reloading and exact tile geometry checks."""
import csv
from email.message import Message
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cycle_compare as c
import gui
import gui_core as g


class CycleComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lumax-model-test-')
        self.root = Path(self.temp.name)
        self.logs = self.root / 'logs'
        self.folder = self.logs / 'regression' / 'run_test'
        self.folder.mkdir(parents=True)
        self.source = 'regression/run_test/total_results.csv'
        self.model = self.root / 'Model.py'
        self.model.write_text('OFFSET=0\ndef compute_cycles(**p):\n    return {"total_cycles": p["Cin"]*p["Cout"]+OFFSET}\n')
        self.patch = patch.object(c, 'MODEL_PATH', self.model)
        self.patch.start()
        with (self.folder / 'total_results.csv').open('w') as f:
            writer = csv.DictWriter(f, fieldnames=['b','RF','CIN','COUT','IN_BITS','W_BITS','status','hw_cycles'])
            writer.writeheader()
            writer.writerows([
                dict(b=2,RF=4,CIN=4,COUT=4,IN_BITS=16,W_BITS=8,status='PASS',hw_cycles=20),
                dict(b=8,RF=8,CIN=4,COUT=8,IN_BITS=16,W_BITS=2,status='PASS',hw_cycles=40),
                dict(b=8,RF=8,CIN=4,COUT=8,IN_BITS=16,W_BITS=4,status='TIMEOUT',hw_cycles=''),
            ])

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def compare(self, **extra):
        return c.compare(self.logs, dict(sources=[self.source], **extra))

    def test_errors_and_missing_stages(self):
        result = self.compare(stages=True)
        s = result['summary']
        self.assertEqual((s['n'],s['hw'],s['predicted']), (2,60,48))
        self.assertEqual((s['mae'],s['min_absolute_error'],s['max_absolute_error']), (6,4,8))
        self.assertEqual((s['mape'],s['min_ape'],s['max_ape'],s['wape']), (20,20,20,20))
        self.assertEqual(s['within_10_pct'], 0)
        self.assertEqual(len(result['excluded']), 1)
        self.assertTrue(all(st['n']==0 and st['mae'] is None for st in result['stages']))
        self.assertTrue(all(v['hw'] is None for v in result['results'][0]['stages'].values()))

    def test_reload_picks_up_global_and_function_edits_without_restart(self):
        first = self.compare()
        self.model.write_text(self.model.read_text().replace('OFFSET=0','OFFSET=4'))
        second = self.compare()
        self.assertNotEqual(first['model']['sha256'], second['model']['sha256'])
        self.assertEqual(second['summary']['predicted'], 56)
        self.model.write_text('def compute_cycles(**p):\n    return {"total_cycles": p["XS"]*p["factor"]}\n')
        self.assertEqual(self.compare()['summary']['predicted'], 72)

    def test_select_subset_and_reject_unknown_sources_or_tests(self):
        row = c.cases(self.logs, [self.source])[0]
        result = self.compare(cases=[row['id']])
        self.assertEqual(result['summary']['n'], 1)
        self.assertEqual(result['results'][0]['parameters']['XS'], 2)
        self.assertEqual(result['results'][0]['parameters']['factor'], 4)
        for source in ['../../secret', '/etc/passwd']:
            with self.assertRaises(ValueError): c.cases(self.logs, [source])
        with self.assertRaises(ValueError): self.compare(cases=['missing'])
        with self.assertRaises(ValueError): self.compare(cases=[])
        with self.assertRaises(ValueError): self.compare(stages='yes')

    def test_model_error_is_reported_without_fallback(self):
        self.model.write_text('def compute_cycles(**p):\n    raise RuntimeError("edited model broken")\n')
        with self.assertRaisesRegex(ValueError, 'edited model broken'): self.compare()
        self.model.write_text('def compute_cycles(**p):\n    return {"total_cycles": float("nan")}\n')
        with self.assertRaisesRegex(ValueError, 'invalid total_cycles'): self.compare()

    def test_skips_notebook_imports_but_preserves_original_calculation(self):
        self.model.write_text('import matplotlib.pyplot as plt\nfrom ipywidgets import interact\nimport seaborn as sns\nimport math\nSCALE=3\ndef compute_cycles(**p):\n    return {"total_cycles": math.ceil(p["Cin"]*SCALE)}\n')
        self.assertEqual(self.compare()['summary']['predicted'], 24)

    def test_gui_tiled_tail_uses_actual_recorded_dimensions(self):
        folder=self.logs/'gui'/'run_20260101_000000_12345678';folder.mkdir(parents=True)
        d=dict(id='design_123456abcdef',name='my design',b=4,rf=8,max_k=4,max_m=3)
        options=dict(g.DEFAULT_OPTIONS)
        operations=[dict(status='PASS',cycles=20) for _ in range(4)]
        data=dict(name='Tiled test',status='PASS',suite=dict(kind='custom',designs=[d],options=options),
                  results=[dict(name='Tail',design=d['name'],k=7,m=5,wb=4,operation_results=operations)])
        (folder/'job.json').write_text(json.dumps(data))
        source=str((folder/'job.json').relative_to(self.logs))
        result=c.compare(self.logs,dict(sources=[source]))
        self.assertEqual([(r['k'],r['m']) for r in result['results']],[(4,3),(3,3),(4,2),(3,2)])
        self.assertEqual(result['summary']['predicted'],35)
        self.assertTrue(all(r['parameters']['XS']==4 and r['parameters']['factor']==8 for r in result['results']))
        # A later snapshot is authoritative, even if capacity settings are edited.
        operations[0].update(k=2,m=2,model_parameters=c.parameters(2,2,4,4,8,physical=dict(c.physical_parameters(),ID=3)))
        (folder/'job.json').write_text(json.dumps(data))
        new=c.compare(self.logs,dict(sources=[source]))
        self.assertEqual(new['results'][0]['parameters']['ID'],3)
        self.assertEqual(new['results'][0]['predicted'],4)

    def test_saved_physical_settings_and_stage_sum_validation(self):
        p=c.physical_parameters('  y_slice = 1,\n  Dma_Ids = 4,\n  DMA_Bytes = 16,\n')
        self.assertEqual((p['ID'],p['DMA_WIDTH']), (4,128))
        row=c.record('x','case','b2_rf4',4,4,8,20,dict(load_x=1,generate=2,loadw_select=3,store_o=4))
        self.assertIn('do not sum',row['reason'])
        row=c.record('x','case','b2_rf4',4,4,8,20,b=8,rf=16)
        self.assertEqual((row['parameters']['XS'],row['parameters']['factor']),(8,16))

    def test_http_comparison_auth_and_validation(self):
        store=g.Store(self.root)
        def request(path, data=None, authenticated=True):
            handler=gui.Handler.__new__(gui.Handler)
            handler.server=SimpleNamespace(token='test-key',store=store)
            handler.path=path;handler.headers=Message();handler.headers['Host']='localhost'
            if authenticated:handler.headers['X-Lumax-Token']='test-key'
            body=json.dumps(data).encode() if data is not None else b''
            handler.headers['Content-Length']=str(len(body));handler.rfile=io.BytesIO(body)
            response={};handler.send=lambda code,value,*a:response.update(code=code,value=value)
            handler.do_GET() if data is None else handler.do_POST()
            return response
        with patch.object(g,'LOGS',self.logs):
            self.assertEqual(request('/api/model/sources',authenticated=False)['code'],401)
            self.assertEqual(request('/api/model/compare',{'sources':[self.source]},False)['code'],401)
            self.assertEqual(len(request('/api/model/sources')['value']['sources']),1)
            self.assertEqual(len(request('/api/model/cases',{'sources':[self.source]})['value']['cases']),3)
            self.assertEqual(request('/api/model/compare',{'sources':[self.source]})['value']['summary']['n'],2)
            self.assertEqual(request('/api/model/compare',{'sources':['../../secret']})['code'],400)


if __name__=='__main__':
    unittest.main()
