"""Contracts for the hypothetical column pipeline, not hardware verification."""
import importlib.util
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
MODEL = Path(__file__).resolve().parents[2] / 'Performance Modeling/Model_ColumnPipeline.py'
spec = importlib.util.spec_from_file_location('column_experiment', MODEL)
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)
reference = experiment._BASE


class ColumnPipelineTests(unittest.TestCase):
    def parameters(self, **changes):
        values = dict(Rin=1, Cin=8, Cout=4, in_bits=16, w_bits=2,
                      XS=2, YS=2, ID=6, factor=4,
                      HS=1, memory_profile='uniform')
        values.update(changes)
        return values

    def test_hand_counted_continuous_columns_keep_final_latency(self):
        p = self.parameters()
        old = reference.compute_cycles(**p)
        new = experiment.compute_cycles(**p)
        # Four reads per column. Old: 4*(4+5)=36 occupied cycles.
        # Streamed: 4*4+5=21, including the five-cycle final drain.
        self.assertEqual(old['cycles select Only'], 36)
        self.assertEqual(new['cycles select Only'], 21)
        self.assertEqual(new['cycles_loadW_select'], 22)
        self.assertEqual(old['total_cycles']-new['total_cycles'], 15)

    def test_one_column_has_no_handoff_to_remove(self):
        for wb in (8, 4, 2):
            for ys in (1, 2, 4):
                p = self.parameters(Rin=5, Cin=33, Cout=1, w_bits=wb, YS=ys)
                old = reference.compute_cycles(**p)
                new = experiment.compute_cycles(**p)
                self.assertEqual(old['total_cycles'], new['total_cycles'])

    def test_five_cycle_gap_reproduces_every_reference_result(self):
        for profile in ('uniform', 'chipyard_llc'):
            for wb in (8, 4, 2):
                for ys in (1, 2, 4):
                    p = self.parameters(Rin=5, Cin=33, Cout=17, w_bits=wb,
                                        YS=ys, memory_profile=profile)
                    old = reference.compute_cycles(**p)
                    control = experiment.compute_cycles(**p, column_gap=5)
                    for key, value in old.items():
                        if key != 'model_version':
                            self.assertEqual(value, control[key], (profile, wb, ys, key))

    def test_read_ports_buffer_lifetimes_and_request_payloads(self):
        p = self.parameters(Rin=3, Cin=17, Cout=9, HS=7, ID=2, trace=True)
        old = reference.compute_cycles(**p)
        new = experiment.compute_cycles(**p)
        def payloads(result):
            return [{k:v for k,v in t.items() if k not in ('issue', 'response')}
                    for t in result['transactions']]
        self.assertEqual(payloads(old), payloads(new))
        groups = {}
        for col in new['columns']:
            key = col['row_group'], col['k_start']
            previous = groups.setdefault(key, [])
            self.assertGreaterEqual(col['issue_start'], col['weight_ready'])
            self.assertEqual(col['retire']-col['read_end'], 5)
            if previous:
                self.assertGreaterEqual(col['issue_start'], previous[-1]['read_end'])
            if len(previous) >= 2:
                request = next(t for t in new['transactions']
                               if t['stage']=='load_w' and t['column']==col['column']
                               and t['row_group']==key[0] and t['k_start']==key[1])
                self.assertGreaterEqual(request['issue'], previous[-2]['buffer_release'])
            previous.append(col)
        self.assertLessEqual(new['total_cycles'], old['total_cycles'])

    def test_slow_memory_can_hide_most_of_the_saving(self):
        p = self.parameters(Cin=4, Cout=16, w_bits=8, HS=100, ID=1)
        old = reference.compute_cycles(**p)
        new = experiment.compute_cycles(**p)
        ideal_saving = 5*(p['Cout']-1)*old['Repeat activation Window']
        saving = old['total_cycles']-new['total_cycles']
        self.assertGreaterEqual(saving, 0)
        self.assertLess(saving, ideal_saving)

    def test_fp16_cycles_and_invalid_gap(self):
        integer = experiment.compute_cycles(**self.parameters())
        half = experiment.compute_cycles(**self.parameters(activation='fp16'))
        self.assertEqual(integer['total_cycles'], half['total_cycles'])
        for value in (-1, 6, 0.5, True):
            with self.assertRaises(ValueError):
                experiment.compute_cycles(**self.parameters(), column_gap=value)


if __name__ == '__main__':
    unittest.main()
