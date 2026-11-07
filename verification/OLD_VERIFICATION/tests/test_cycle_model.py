"""Controller timing contracts, independent of numerical matmul verification."""
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cycle_compare as c
import gui_core as g


class ControllerModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compute, cls.info = c.load_model()
        # Resolve the helper from the same executed source as compute_cycles.
        cls.dma_finish = staticmethod(cls.compute.__globals__['dma_finish_time_exact_bits'])
        cls.memory = staticmethod(cls.compute.__globals__['_Memory'])
        cls.compute = staticmethod(cls.compute)

    def run_model(self, **changes):
        p = dict(Rin=1, Cin=4, Cout=4, in_bits=16, w_bits=8,
                 XS=2, YS=1, ID=2, factor=4, memory_profile='uniform')
        p.update(changes)
        return self.compute(**p)

    def test_single_credit_returns_on_next_edge_and_response_spacing_matters(self):
        # Issue 0,8,16; responses 7,15,23. Two credits issue 0,1,8.
        self.assertEqual(self.dma_finish(3*64, 1, L=7), 23)
        self.assertEqual(self.dma_finish(3*64, 2, L=7), 15)
        self.assertEqual(self.dma_finish(3*64, 3, L=7, beat_time=4), 15)
        self.assertEqual(self.dma_finish(0, 1), 0)
        self.assertEqual(self.dma_finish(65, 2, L=7), 8)

    def test_hand_counted_small_uniform_transfer(self):
        r = self.run_model()
        # One X beat + completion edges; 2 banks spans * 65 LUT edges.
        # Two selection inputs per column, four-cycle tagged tails. Later
        # columns wait for weights; the last retirement leaves the state.
        self.assertEqual([r[k] for k in (
            'cycles_load_X', 'cycles_generate_Products', 'cycles_loadW_select',
            'cycles_store_output', 'cycles load w Only', 'cycles select Only',
            'total_cycles')], [9, 130, 25, 9, 32, 20, 173])
        for cols in (1, 2):
            one = self.run_model(Cout=cols)
            self.assertGreater(one['cycles_loadW_select'], 0)
            self.assertGreater(one['total_cycles'], 0)

    def test_handshake_latency_and_dma_credits_apply_to_output(self):
        fast = self.run_model(HS=3, ID=1, Cin=32)
        slow = self.run_model(HS=9, ID=1, Cin=32)
        self.assertGreater(slow['cycles_load_X'], fast['cycles_load_X'])
        self.assertGreater(slow['cycles_store_output'], fast['cycles_store_output'])
        parallel = self.run_model(HS=9, ID=6, Cin=32)
        self.assertLess(parallel['cycles_load_X'], slow['cycles_load_X'])
        self.assertLess(parallel['cycles_store_output'], slow['cycles_store_output'])
        # HS9 includes the old receiver register: physical returns take eight.
        # ID1 issues 0,9 and retires 8,17; ID6 issues 0,1 and retires 8,9.
        self.assertEqual(slow['cycles_store_output'], 19)
        self.assertEqual(parallel['cycles_store_output'], 11)

    def test_literal_rtl_tail_span_differs_between_row_paths(self):
        serial = self.run_model(Cin=17, Cout=3, trace=True)
        parallel = self.run_model(Cin=17, Cout=3, YS=2, trace=True)
        self.assertEqual([w['bank_span'] for w in serial['windows']], [4, 4, 4])
        self.assertEqual([w['bank_span'] for w in parallel['windows']], [4, 4, 1])
        self.assertEqual(serial['cycles_generate_Products'], 780)
        self.assertEqual(parallel['cycles_generate_Products'], 585)
        self.assertEqual(serial['cycles select Only'], 60)
        self.assertEqual(parallel['cycles select Only'], 51)

    def test_tagged_columns_keep_four_cycle_tail_and_capture_before_refill(self):
        result = self.run_model(Cin=8, Cout=4, w_bits=2, HS=1, ID=6, trace=True)
        columns = result['columns']
        # Four bank-position inputs per column: 16 input cycles plus one
        # four-cycle final tail. No Done_acc handoff cycle is added afterward.
        self.assertEqual(result['cycles select Only'], 20)
        self.assertEqual(result['cycles_loadW_select'], 20)
        for col in columns:
            self.assertEqual(col['retire']-col['read_end'], 4)
            self.assertEqual(col['buffer_release']-col['read_end'], 1)
        self.assertEqual(columns[1]['issue_start'], columns[0]['read_end'])
        self.assertLess(columns[1]['issue_start'], columns[0]['retire'])
        refill = next(t for t in result['transactions']
                      if t['stage']=='load_w' and t['column']==2)
        self.assertEqual(refill['issue'], columns[0]['buffer_release'])
        self.assertEqual(self.info['version'], 'controller-events-v5')

    def test_accepted_verilator_case_combined_stage_and_selection_counter(self):
        # Saved native RTL run_20261105_211028_031082.log: b2/RF8/YS1,
        # IDs6, X1x32 W32x32, INT16/W4. Memory parameters are the existing
        # defaults, not fitted to the counters. Whole-call memory error remains.
        result = self.run_model(Cin=32, Cout=32, w_bits=4, factor=8, ID=6,
                                memory_profile='chipyard_llc', dma_bypass=False)
        self.assertEqual(result['cycles_loadW_select'], 558)
        self.assertEqual(result['cycles select Only'], 544)
        self.assertEqual(result['cycles load w Only'], 408)

    def test_bypass_verilator_case_without_memory_parameter_fitting(self):
        # Native run_20261106_033102_418881.log: same geometry as above.
        result = self.run_model(Cin=32, Cout=32, w_bits=4, factor=8, ID=6,
                                memory_profile='chipyard_llc')
        self.assertTrue(result['dma_response_bypass'])
        self.assertEqual(result['cycles_loadW_select'], 551)
        self.assertEqual(result['cycles select Only'], 544)
        self.assertEqual(result['cycles load w Only'], 376)

    def transfer(self, ids, count=3, **changes):
        settings = dict(hs=7, profile='uniform', line_bytes=64,
                        cache_bytes=1024, read_penalty=11, write_penalty=4,
                        trace=True)
        settings.update(changes)
        memory = self.memory(**settings)
        memory.transfer([(i*8, 1) for i in range(count)], 0, ids)
        return memory.trace

    def test_bypass_single_credit_and_next_edge_reuse(self):
        fast = self.transfer(1)
        registered = self.transfer(1, bypass=False)
        self.assertEqual([t['issue'] for t in fast], [0, 7, 14])
        self.assertEqual([t['response'] for t in fast], [6, 13, 20])
        self.assertEqual([t['response'] for t in registered], [7, 15, 23])
        self.assertTrue(all(t['bypassed'] for t in fast))

    def test_out_of_order_returns_buffered_and_single_d_channel(self):
        # A arrives at 6, B is ready at 3, C is also ready at 3. B and C
        # cannot enter the physical D channel together: C enters at 4.
        fast = self.transfer(3, latencies=[6, 2, 1], latency_basis='tilelink')
        self.assertEqual([t['tilelink_response'] for t in fast], [6, 3, 4])
        self.assertEqual([t['response'] for t in fast], [6, 7, 8])
        self.assertEqual([t['bypassed'] for t in fast], [True, False, False])
        slow = self.transfer(3, latencies=[6, 2, 1], latency_basis='tilelink', bypass=False)
        self.assertEqual([t['response'] for t in slow], [7, 8, 9])

    def test_empty_queue_zero_latency_reply_cannot_bypass(self):
        trace = self.transfer(1, count=2, hs=1)
        self.assertEqual([t['issue'] for t in trace], [0, 2])
        self.assertEqual([t['tilelink_response'] for t in trace], [0, 2])
        self.assertEqual([t['response'] for t in trace], [1, 3])
        self.assertEqual([t['bypassed'] for t in trace], [False, False])

    def test_controller_replay_not_reduced_twice(self):
        visible = self.transfer(1, count=1, latencies=[7])
        physical = self.transfer(1, count=1, latencies=[6], latency_basis='tilelink')
        self.assertEqual(visible[0]['response'], 7)
        self.assertIsNone(visible[0]['bypassed'])
        self.assertEqual(physical[0]['response'], 6)
        self.assertTrue(physical[0]['bypassed'])

    def test_parallel_rows_share_weight_reads_and_generation(self):
        one = self.run_model(YS=2, trace=True)
        two = self.run_model(Rin=2, YS=2, trace=True)
        sequential = self.run_model(Rin=2, YS=1, trace=True)
        def weight_requests(result):
            return sum(t['stage']=='load_w' for t in result['transactions'])
        self.assertEqual(weight_requests(one), weight_requests(two))
        self.assertEqual(weight_requests(sequential), 2*weight_requests(two))
        self.assertEqual(two['cycles_generate_Products'], one['cycles_generate_Products'])
        self.assertEqual(sequential['cycles_generate_Products'], 2*one['cycles_generate_Products'])
        self.assertEqual(two['cycles select Only'], one['cycles select Only'])

    def test_output_row_boundary_keeps_partial_writes_separate(self):
        result = self.run_model(Rin=3, Cout=3, YS=2, ID=6, trace=True)
        writes = [t for t in result['transactions'] if t['write']]
        self.assertEqual(result['WriteOp'], 6)  # Not ceil(9/2) = 5.
        self.assertEqual([t['elements'] for t in writes], [2, 1, 1, 2, 2, 1])
        self.assertEqual(writes[1]['address'], writes[2]['address'])
        self.assertEqual([t['row'] for t in writes], [0, 0, 1, 1, 2, 2])
        # The next row starts while the preceding row's partial write is pending.
        self.assertEqual(writes[2]['issue'], writes[1]['issue']+1)
        self.assertLess(writes[2]['issue'], writes[1]['response'])
        # A new row group waits for all acknowledgements from the previous one.
        self.assertGreater(writes[4]['issue'], writes[3]['response'])
        self.assertEqual(result['cycles_store_output'], 20)

    def test_output_backpressure_ordered_acks_credit_reuse_and_final_drain(self):
        baseline = self.run_model(Cout=9, trace=True)
        latencies = [7]*baseline['dma_requests']
        delays = [0]*baseline['dma_requests']
        indices = [i for i,t in enumerate(baseline['transactions']) if t['write']]
        for i,latency in zip(indices, (20, 1, 2, 30, 1)):
            latencies[i] = latency
        delays[indices[0]] = 5
        result = self.run_model(Cout=9, trace=True,
                                dma_latencies=latencies, dma_issue_delays=delays)
        writes = [t for t in result['transactions'] if t['write']]
        # Relative to store entry: stalled first issue at 5. Two slots and
        # ordered delivery hold the early second/final responses until their turn.
        start = writes[0]['issue']-5
        self.assertEqual([t['issue']-start for t in writes], [5, 6, 26, 27, 29])
        self.assertEqual([t['response']-start for t in writes], [25, 26, 28, 57, 58])
        self.assertEqual(result['cycles_store_output'], 60)
        self.assertEqual([t['elements'] for t in writes], [2, 2, 2, 2, 1])

    def test_fp16_has_same_controller_cycles_and_explicit_format(self):
        for wb in (8, 4, 2):
            integer = self.run_model(w_bits=wb)
            half = self.run_model(w_bits=wb, activation='fp16')
            self.assertEqual(half['activation'], 'fp16')
            for key in ('total_cycles', 'cycles_generate_Products', 'cycles_loadW_select'):
                self.assertEqual(integer[key], half[key])
        self.assertEqual(set(self.info['activation_formats']), {'int16', 'fp16'})

    def test_addresses_control_cache_line_sharing_without_hw_counters(self):
        separate = self.run_model(memory_profile='chipyard_llc')
        shared = self.run_model(memory_profile='chipyard_llc',
                                w_address=0x10000008, o_address=0x10000020)
        self.assertLess(shared['cache_misses'], separate['cache_misses'])
        self.assertLess(shared['total_cycles'], separate['total_cycles'])
        self.assertFalse(shared['diagnostic_dma_replay'])

    def test_invalid_or_unsupported_parameters_fail_clearly(self):
        for changes in (dict(Rin=0), dict(Cin=2.5), dict(ID=True), dict(in_bits=8),
                        dict(w_bits=1), dict(activation='fp32'), dict(DMA_WIDTH=128),
                        dict(dma_bypass=1), dict(dma_latency_basis='unknown'),
                        dict(x_address=3), dict(memory_profile='unknown')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.run_model(**changes)

    def test_observed_issue_delays_also_mark_replay_as_diagnostic(self):
        ordinary = self.run_model()
        delayed = self.run_model(dma_issue_delays=[0]*ordinary['dma_requests'])
        self.assertTrue(delayed['diagnostic_dma_replay'])
        self.assertEqual(delayed['total_cycles'], ordinary['total_cycles'])

    def test_elf_addresses_come_from_symbols_with_guard_offset(self):
        symbols = '80001000 b xbuf\n80002000 d wbuf\n80003000 b obuf\n'
        with patch.object(g.shutil, 'which', return_value='/tool/nm'), \
             patch.object(g.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=symbols)) as run:
            self.assertEqual(g.elf_buffer_addresses(Path('test.riscv'), {'PATH':'/tool'}),
                             dict(x_address=0x80001008,w_address=0x80002008,o_address=0x80003008))
            self.assertEqual(run.call_args.args[0], ['/tool/nm', '--defined-only', 'test.riscv'])
            run.return_value.stdout += '80004000 b xbuf\n'
            self.assertEqual(g.elf_buffer_addresses(Path('test.riscv'), {'PATH':'/tool'}), {})
            run.side_effect = subprocess.TimeoutExpired('nm', 10)
            self.assertEqual(g.elf_buffer_addresses(Path('test.riscv'), {'PATH':'/tool'}), {})


if __name__ == '__main__':
    unittest.main()
