"""FP16 GUI reference, tolerance, format selection and tile orchestration."""
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gui_core as g
import fp_compare as f


def bits(number):
    return struct.unpack('<I', struct.pack('<f', number))[0]


class FP16DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lumax-gui-fp-unit-')
        self.root = Path(self.temp.name)
        self.store = g.Store(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_design_format_persistence_and_legacy_defaults(self):
        designs = self.store.designs()
        self.assertEqual(sum(g.activation(d) == 'fp16' for d in designs), 6)
        self.assertEqual(sum(g.activation(d) == 'int16' for d in designs), 6)
        for fmt in ('int16', 'fp16'):
            saved = self.store.save_design(dict(name=fmt, b=2, rf=4, max_k=32, max_m=32, activation=fmt))
            self.assertIn(saved, self.store.designs())
            suite = self.store.save_suite(dict(name=fmt, kind='custom', design_ids=[saved['id']], tests=[dict(k=4, m=4)],
                                               options=dict(fp_comparison='tolerance', atol='1e-7', rtol='1e-5')))
            self.assertEqual(suite['designs'][0]['activation'], fmt)
            self.assertEqual(suite['options']['atol'], 1e-7)
        self.assertEqual(g.activation({}), 'int16')
        with self.assertRaisesRegex(ValueError, 'standard INT16'):
            g.normalize_suite(dict(name='bad preset', kind='regression', design_ids=['b2_rf4_fp16']), designs)
        with self.assertRaises(ValueError):
            g.validate_design(dict(name='bad', b=2, rf=4, max_k=32, max_m=32, activation='fp32'))

    def test_invalid_tolerances_rejected(self):
        for val in (-1, True, None, 'NaN', 'inf', '-inf', 'x'):
            with self.assertRaises(ValueError): g.nonnegative_float(val, 'atol')

    def test_fp16_operands_and_reference_are_numeric_halves(self):
        test = dict(k=128, m=4, pattern='random')
        x, w, xs, ws = g.random_case(test, 8, 'same seed', 'fp16')
        self.assertNotEqual(xs, ws)
        self.assertGreater(len(set(x)), 100)
        self.assertTrue(all((v >> 10) & 31 != 31 for v in x))
        self.assertTrue(any(v & 0x8000 for v in x))
        self.assertTrue(any(not v & 0x8000 for v in x))
        self.assertEqual(g.reference([0x3c00, 0xc000], [3, -4], 1, 'fp16'), [bits(11.0)])
        x, _, _, _ = g.random_case(dict(test, pattern='onehot_x'), 2, 'seed', 'fp16')
        self.assertEqual(x, [0x3c00] + [0] * 127)

    def test_exact_and_tolerant_results_do_not_change_thresholds(self):
        reference = [bits(1.0), bits(-2.0)]
        actual = [bits(1.0) + 1, bits(-2.0)]
        exact = f.compare(actual, reference, g.DEFAULT_OPTIONS)
        self.assertFalse(exact['numeric_pass'])
        self.assertEqual(exact['exact_matches'], 1)
        required = exact['comparison']['required_atol']
        self.assertEqual(required, 2**-23)
        options = dict(g.DEFAULT_OPTIONS, fp_comparison='tolerance', atol=required)
        before = copy.deepcopy(options)
        tolerant = f.compare(actual, reference, options)
        self.assertTrue(tolerant['numeric_pass'])
        self.assertEqual(options, before)
        self.assertFalse(exact['numeric_pass'])
        rtol = exact['comparison']['required_rtol']
        self.assertTrue(f.compare(actual, reference, dict(options, atol=0, rtol=rtol))['numeric_pass'])

    def test_zero_and_nonfinite_outputs_have_honest_suggestions(self):
        zero = f.compare([bits(1.0)], [0], g.DEFAULT_OPTIONS)['comparison']
        self.assertEqual(zero['required_atol'], 1.0)
        self.assertIsNone(zero['required_rtol'])
        self.assertTrue(zero['relative_error_unbounded'])
        for actual, ref in [(0x7f800000, 0xff800000), (0x7fc00000, bits(1.0)), (0x7fc00001, 0x7fc00000)]:
            r = f.compare([actual], [ref], dict(g.DEFAULT_OPTIONS, fp_comparison='tolerance', atol=1e30))
            self.assertFalse(r['numeric_pass'])
            self.assertIsNone(r['comparison']['required_atol'])
            json.dumps(r, allow_nan=False)
        for word in (0x7fc00000, 0x7f800000, 0xff800000):
            self.assertTrue(f.compare([word], [word], g.DEFAULT_OPTIONS)['numeric_pass'])
        self.assertFalse(f.compare([], [bits(1.0)], g.DEFAULT_OPTIONS)['numeric_pass'])

    def test_fp32_partial_sum_avoids_double_rounding(self):
        self.assertEqual(f.sum_words([bits(2**31), bits(128)]), 0x4f000000)
        self.assertEqual(f.sum_words([bits(2**31), bits(128), 1]), 0x4f000001)
        self.assertEqual(f.sum_words([bits(1.0), bits(-1.0)]), 0)
        self.assertEqual(f.sum_words([1, 1]), 2)
        self.assertEqual(f.sum_words([0x7f7fffff] * 2), 0x7f800000)
        self.assertEqual(f.sum_words([0x7f800000, 0xff800000]), 0x7fc00000)

    def test_tolerance_cannot_hide_guards_incomplete_output_or_int_error(self):
        uart = f'CYCLES 1 2 3 4 5 6 CORE 20\nGUARDS PASS\nHW_OUTPUT {bits(1.0)+1}\nTEST_DONE\n'
        opts = dict(g.DEFAULT_OPTIONS, fp_comparison='tolerance', atol=1.0)
        self.assertEqual(g.parse_result(uart, [bits(1.0)], opts, 0, 1, 'fp16')['status'], 'PASS')
        for bad in (uart.replace('GUARDS PASS', 'GUARDS FAIL'), uart.replace('TEST_DONE', 'NO_END'), uart.replace(str(bits(1.0)+1), '-1')):
            self.assertEqual(g.parse_result(bad, [bits(1.0)], opts, 0, 1, 'fp16')['status'], 'FAIL')
        self.assertEqual(g.parse_result(uart, [bits(1.0)], opts, 0, 1, 'int16')['status'], 'FAIL')

    def test_fp16_c_source_calls_hw_and_does_not_embed_python_answers(self):
        base = (g.hw.SRC / 'Linear-sw.c').read_text()
        design = dict(b=2, rf=4, activation='fp16')
        for wb in (8, 4, 2):
            source = g.test_source(base, design, [0x3c00, 0xc000], [1, -1], wb, dict(g.DEFAULT_OPTIONS, integrity=True))
            main = source.split('int main(void)', 1)[1]
            self.assertIn('#define LUMAX_FP16_ACTIVATIONS 1', source)
            self.assertIn('preload_hw(2,1,1,', main)
            self.assertIn('matmul_hw_uniform(actual,&tx,&tw,2,1,', main)
            self.assertNotIn('C_REFERENCE', main)
            self.assertNotIn('memcpy', main)

    def test_fp16_build_selects_static_flag_and_restores_config_on_failure(self):
        config = self.root / 'Config.scala'
        original = g.hw.CONFIG.read_bytes();config.write_bytes(original)
        job = SimpleNamespace(data=dict(suite=dict(options=g.DEFAULT_OPTIONS)), log=lambda *_: None)
        def failed_command(args, cwd, output, *rest):
            self.assertIn('FP16_ACTIVATIONS = true', config.read_text())
            output.write_text('Intentional isolated failure');return 2
        job.command = failed_command
        design = dict(id='fp', name='fp', b=2, rf=4, max_k=32, max_m=32, activation='fp16')
        with patch.object(g.hw, 'CONFIG', config):
            with self.assertRaisesRegex(RuntimeError, 'Make failed'):
                g.build_design(job, design, original, self.root, {})
        self.assertEqual(config.read_bytes(), original)

    def test_k_tiling_compares_full_reference_and_reports_required_tolerance(self):
        design = dict(id='fp', name='FP16', b=2, rf=4, max_k=2, max_m=1, activation='fp16', builtin=False)
        for tolerant, missing in [(False, False), (True, False), (True, True), (True, 'guard')]:
            suite = g.normalize_suite(dict(name='FP tiles', kind='custom', design_ids=['fp'],
                tests=[dict(k=4, m=1, weights=[4])], options=dict(fp_comparison='tolerance' if tolerant else 'exact', atol=2**-23, stop_on_fail=bool(missing))), [design])
            folder = self.root / (str(tolerant) + str(missing));folder.mkdir()
            g.write_json(folder / 'job.json', dict(suite=suite, status='QUEUED', results=[], completed=0, operations_done=0))
            job = g.Job(folder);config = folder / 'config';config.write_bytes(g.hw.CONFIG.read_bytes())
            temporary = folder / 'work';temporary.mkdir()
            def build(job, design, original, temp, env):
                p = temp / 'fp';p.mkdir();sim = p / 'sim';sim.touch();return sim
            def operation(job, sim, design, base, options, x, w, wb, folder, env, expected):
                # Synthetic partial outputs only inside this orchestration test.
                return dict(status='FAIL' if missing else 'PASS', actual=None if missing is True else list(expected), expected=list(expected), cycles=11, core_cycles=12)
            with patch.object(g.hw, 'CONFIG', config), patch.object(g, 'fingerprint', lambda: {str(config): g.hw.sha(config)}), \
                 patch.object(g, 'build_design', build), patch.object(g, 'run_operation', operation), \
                 patch.object(g, 'random_case', lambda *a: ([0x3c00, 1, 1, 1], [1]*4, '1', '2')):
                g.run_custom(job, temporary, {})
            result = job.data['results'][0]
            self.assertEqual(result['cycles'], 11 if missing else 22)
            self.assertEqual(result['status'], 'PASS' if tolerant and not missing else 'FAIL')
            self.assertEqual(result['comparison']['required_atol'], None if missing else 2**-23)
            self.assertEqual(result['comparison']['outputs_complete'], not missing)
            self.assertEqual(result['comparison']['exact_matches'], 0)


if __name__ == '__main__':
    unittest.main()
