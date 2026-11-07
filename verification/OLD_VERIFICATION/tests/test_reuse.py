"""Verify that the state-reuse harness really defers all checks until after HW."""
from pathlib import Path
import sys
import unittest
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import run_y_slice as s


class ConsecutiveTests(unittest.TestCase):
    def test_no_reference_or_io_between_hardware_calls(self):
        base=(s.hw.SRC/'Linear-sw.c').read_text()
        for floating in [False,True]:
            for ys in [1,2]:
                tests=s.sequence_cases(8,floating,ys)
                code=s.source(base,tests,8,floating,ys,2,4,True)
                main=code.split('int main(void)',1)[1]
                calls,checks=main.split('/* All hardware calls completed.',1)
                self.assertEqual(calls.count('matmul_hw_uniform(out,'),8)
                self.assertEqual(calls.count('preload_hw('),8)
                for forbidden in ['printf(', 'matmul_sw_uniform(', 'read_rd(', 'memcpy(']:
                    self.assertNotIn(forbidden,calls)
                self.assertEqual(checks.count('matmul_sw_uniform('),8)
                self.assertEqual(checks.count('boundary(o'),8)
                self.assertIn('TEST_DONE',checks)

    def test_changed_shapes_repeats_and_zero_after_poison(self):
        for floating in [False,True]:
            for ys in [1,2]:
                for wb in [8,4,2]:
                    tests=s.sequence_cases(wb,floating,ys)
                    self.assertEqual(tests[0]['expected'],tests[2]['expected'])
                    self.assertEqual(tests[0]['expected'],tests[-1]['expected'])
                    self.assertNotEqual((tests[0]['k'],tests[0]['m'],tests[0]['block']),
                                        (tests[1]['k'],tests[1]['m'],tests[1]['block']))
                    self.assertEqual(tests[5]['expected'],[0]*(ys*2))
                    self.assertEqual(tests[3]['r'],ys*2)
                    for t in tests:
                        self.assertTrue(all(-(1<<(wb-1))<=v<(1<<(wb-1)) for v in t['w']))
                    if floating:
                        self.assertTrue(all(v==0x7fc00000 for v in tests[4]['expected']))

if __name__=='__main__':unittest.main()
