"""Multirow planning, operand layout and native-operation contracts."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gui_core as g


class RowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lumax-gui-rows-')
        self.root = Path(self.temp.name)
        self.store = g.Store(self.root)
        self.design = self.store.save_design(dict(name='Two rows', b=2, rf=4,
            y_slice=2, max_r=4, max_k=4, max_m=3))
        self.payload = dict(name='Four rows', kind='custom', design_ids=[self.design['id']],
            tests=[dict(r=4, k=7, m=5, weights=[8], seed='7')])

    def tearDown(self):
        self.temp.cleanup()

    def test_persistence_legacy_defaults_and_capacity(self):
        saved = next(d for d in self.store.designs() if d['id'] == self.design['id'])
        self.assertEqual((saved['y_slice'], saved['max_r']), (2, 4))
        self.assertEqual(g.y_slices({}), 1)
        for changes in [dict(y_slice=3), dict(max_r=1)]:
            with self.assertRaises(ValueError):
                g.validate_design(dict(self.design, **changes))
        suite = g.normalize_suite(self.payload, [saved])
        self.assertEqual(suite['tests'][0]['r'], 4)
        self.assertEqual(g.make_plan(suite)['operations'], 4)
        with self.assertRaisesRegex(ValueError, 'row capacity'):
            g.normalize_suite(self.payload, [dict(saved, max_r=2)])

    def test_multirow_source_and_independent_reference(self):
        x = [1, 2, 3, 4]
        w = [1, 0, 0, 2]
        self.assertEqual(g.matrix_reference(x, w, 2, 2), [1, 4, 3, 8])
        options = dict(g.DEFAULT_OPTIONS, rows=2, reference='riscv', integrity=True)
        source = g.test_source((g.hw.SRC/'Linear-sw.c').read_text(), self.design, x, w, 4, options)
        self.assertIn('#define YS 2', source)
        self.assertIn('preload_hw(2,2,2,', source)
        self.assertIn('matmul_hw_uniform(actual,&tx,&tw,2,2,x_slice_max*RF,y_slice,2)', source)
        self.assertIn('xbuf.data[row*2+j]', source)
        self.assertIn('ref[row*2+col]', source)
        self.assertNotIn('expected[', source)
        random_x, weights, *_ = g.random_case(dict(r=4,k=7,m=5,pattern='random'), 8, '7')
        self.assertEqual((len(random_x), len(weights)), (28, 35))
        self.assertNotEqual(random_x[:7], random_x[7:14])

    def test_build_saves_static_row_parameters_and_restores(self):
        config = self.root/'Config.scala'
        original = g.hw.CONFIG.read_bytes()
        config.write_bytes(original)
        job = SimpleNamespace(data=dict(suite=dict(options=dict(g.DEFAULT_OPTIONS))), log=lambda *_:None)
        def fail(args, cwd, output, *rest):
            text = config.read_text()
            self.assertRegex(text, r'y_slice\s*=\s*2\s*,')
            self.assertRegex(text, r'Rin\s*=\s*4\s*,')
            output.write_text('Intentional build failure in orchestration unit test')
            return 2
        job.command = fail
        with patch.object(g.hw, 'CONFIG', config), self.assertRaisesRegex(RuntimeError, 'Make failed'):
            g.build_design(job, self.design, original, self.root, {})
        self.assertEqual(config.read_bytes(), original)

    def test_multirow_k_and_column_tiles_reassemble_every_row(self):
        suite = g.normalize_suite(self.payload, [self.design])
        folder = self.root/'job';folder.mkdir()
        g.write_json(folder/'job.json', dict(suite=suite,status='QUEUED',results=[],completed=0,operations_done=0))
        job = g.Job(folder)
        config = self.root/'config';config.write_bytes(g.hw.CONFIG.read_bytes())
        work = self.root/'work';work.mkdir()
        seen = []
        def build(job, design, original, temporary, env):
            path = temporary/design['id'];path.mkdir();return path/'simulator'
        def operation(job, sim, design, base, options, x, w, wb, folder, env, expected):
            # This is an orchestration test; hardware is checked separately in Verilator.
            r = options['rows'];k = len(x)//r;m = len(w)//k
            self.assertEqual(r, 4)
            self.assertEqual(expected, g.matrix_reference(x,w,k,m))
            seen.append((r,k,m))
            return dict(status='PASS',actual=list(expected),expected=list(expected),cycles=10,core_cycles=20)
        with patch.object(g.hw,'CONFIG',config), patch.object(g,'fingerprint',lambda:{str(config):g.hw.sha(config)}), patch.object(g,'build_design',build), patch.object(g,'run_operation',operation):
            g.run_custom(job,work,{})
        self.assertEqual(len(seen),4)
        result=job.data['results'][0]
        self.assertEqual((result['status'],result['matches'],result['elements']),('PASS',20,20))
        for op in result['operation_results']:
            self.assertEqual((op['model_parameters']['Rin'],op['model_parameters']['YS']),(4,2))
        self.assertEqual(result['cycles'],40)


if __name__ == '__main__':
    unittest.main()
