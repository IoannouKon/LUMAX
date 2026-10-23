"""Dashboard checks; fake HW is used only in isolated worker unit tests."""
import ast
import copy
import collections
from email.message import Message
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gui_core as g
import gui


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='lumax-gui-unit-')
        self.root = Path(self.temporary.name)
        self.store = g.Store(self.root)
        self.design = dict(id='b8_rf8', name='b8_rf8', b=8, rf=8, max_k=4, max_m=3, builtin=True)
        self.payload = dict(name='Unit suite', kind='custom', design_ids=['b8_rf8'],
                            tests=[dict(k=7, m=5, weights=[8, 4, 2])])

    def tearDown(self):
        self.temporary.cleanup()

    def suite(self, **options):
        data = copy.deepcopy(self.payload)
        data['options'] = options
        return g.normalize_suite(data, [self.design])

    def test_native_capacity_and_auto_tiling(self):
        with self.assertRaisesRegex(ValueError, 'exceeds'):
            self.suite(mode='native')
        suite = self.suite()
        plan = g.make_plan(suite)
        self.assertEqual((plan['cases'], plan['operations'], plan['builds']), (3, 12, 1))
        self.assertTrue(plan['detail'][0]['reduction'])
        ranges = g.tile_ranges(7, 5, self.design, suite['options'])
        cells = [(j, c) for a, n, b, m in ranges for j in range(a, a+n) for c in range(b, b+m)]
        self.assertEqual(len(cells), 35)
        self.assertEqual(len(set(cells)), 35)

    def test_classifier_output_tiles(self):
        d = dict(self.design, max_k=1024, max_m=1024)
        options = dict(g.DEFAULT_OPTIONS, mode='tile', tile_m=512)
        chunks = g.tile_ranges(288, 32000, d, options)
        self.assertEqual(len(chunks), 63)
        self.assertEqual(chunks[-1], (0, 288, 31744, 256))

    def fast_suite(self, design=None):
        design=design or dict(self.design,max_k=1024,max_m=1024)
        payload=dict(name='Fast Llama2',kind='llama_fast',design_ids=[design['id']])
        return g.normalize_suite(payload,[design])

    def test_fast_llama_uses_selected_capacity_not_fixed_tile(self):
        suite=self.fast_suite()
        plan=g.make_plan(suite)
        self.assertEqual((plan['cases'],plan['operations'],plan['builds']),(18,18,1))
        classifier=next(d for d in plan['detail'] if d['test']=='LQ04')
        self.assertEqual((classifier['sample_k'],classifier['sample_m'],classifier['tiles']),(288,1024,32))
        self.assertEqual(suite['options']['mode'],'native')
        self.assertTrue(suite['options']['cycles'])
        suite=self.fast_suite(dict(self.design,max_k=256,max_m=128))
        tile=g.representative_tile(suite['tests'][0],suite['designs'][0])
        self.assertEqual(tile,dict(k=256,m=128,k_tiles=2,m_tiles=250,tiles=500))
        self.assertEqual(g.make_plan(suite)['operations'],18)
        custom=dict(self.design,id='design_123456abcdef',builtin=False,max_k=288,max_m=2048)
        suite=self.fast_suite(custom)
        self.assertEqual(g.representative_tile(suite['tests'][0],custom)['tiles'],16)

    def test_cycle_extrapolation_is_marked_and_never_verifies_missing_outputs(self):
        design=dict(self.design,max_k=1024,max_m=1024)
        test=dict(k=288,m=32000)
        sample=dict(status='PASS',cycles=123,core_cycles=150,matches=1024,elements=1024)
        result=g.add_cycle_estimate(dict(sample),test,design)
        self.assertEqual(result['estimated_cycles'],123*32)
        self.assertEqual(result['estimated_core_cycles'],150*32)
        self.assertEqual(result['matches'],1024)
        self.assertEqual(result['status'],'ESTIMATED')
        self.assertEqual(result['sample_status'],'PASS')
        self.assertFalse(result['full_result_verified'])
        self.assertEqual(g.suite_status([result]),'ESTIMATED')
        whole=g.add_cycle_estimate(dict(sample),dict(k=288,m=1024),design)
        self.assertTrue(whole['full_result_verified'])
        self.assertEqual(whole['status'],'PASS')
        for status in ('FAIL','TIMEOUT','BUILD_FAIL'):
            bad=g.add_cycle_estimate(dict(sample,status=status),test,design)
            self.assertIsNone(bad['estimated_cycles'])
            self.assertFalse(bad['full_result_verified'])
            self.assertEqual(bad['status'],status)
        unverified=g.add_cycle_estimate(dict(sample,status='UNVERIFIED'),test,design)
        self.assertEqual(g.suite_status([unverified]),'UNVERIFIED')
        self.assertFalse(unverified['full_result_verified'])
        self.assertEqual(g.suite_status([result,dict(status='FAIL')]),'FAIL')

    def test_fast_llama_worker_runs_only_one_sample_per_shape_precision(self):
        suite=self.fast_suite(dict(self.design,max_k=16,max_m=32))
        folder=self.root/'fast';folder.mkdir()
        g.write_json(folder/'job.json',dict(suite=suite,status='QUEUED',results=[],completed=0,operations_done=0))
        job=g.Job(folder)
        config=self.root/'fast-config';config.write_text(g.hw.CONFIG.read_text())
        temporary=self.root/'fast-work';temporary.mkdir()
        seen=[]
        def build(job,design,base,tmp,env):
            p=tmp/design['id'];p.mkdir();s=p/'simulator';s.touch();return s
        def operation(job,sim,design,base,options,x,w,wb,folder,env,expected):
            seen.append((len(x),len(w)//len(x),wb))
            self.assertEqual(options['mode'],'native')
            # Isolated orchestration test, not a hardware verification result.
            return dict(status='PASS',actual=list(expected),expected=list(expected),cycles=17,core_cycles=19)
        with patch.object(g.hw,'CONFIG',config),patch.object(g,'fingerprint',lambda:{str(config):g.hw.sha(config)}),patch.object(g,'build_design',build),patch.object(g,'run_operation',operation):
            g.run_custom(job,temporary,{})
        self.assertEqual(len(seen),18)
        self.assertEqual(job.data['operations_done'],18)
        self.assertEqual(collections.Counter(b for _,_,b in seen),{8:6,4:6,2:6})
        self.assertTrue(all(k<=16 and m<=32 for k,m,_ in seen))
        self.assertEqual(g.suite_status(job.data['results']),'ESTIMATED')
        self.assertTrue(all(r['estimated_cycles']==17*r['tiles_needed'] for r in job.data['results'] if r['tiles_needed']>1))
        self.assertTrue(all(not r['full_result_verified'] for r in job.data['results'] if r['tiles_needed']>1))
        self.assertIn('TILES NEEDED=18000', (folder/'run.log').read_text())

    def test_original_suite_counts(self):
        designs = [dict(self.design, id=f'b{b}_rf{rf}') for b in (2,4,8) for rf in (4,8)]
        for kind, count, ops in [('regression',180,180),('test_cases',3798,3888),('llama_shapes',108,1224),('matrix_1024',18,18)]:
            data = dict(name='Preset',kind=kind,design_ids=[d['id'] for d in designs])
            plan = g.make_plan(g.normalize_suite(data,designs))
            self.assertEqual(plan['cases'],count)
            self.assertEqual(plan['operations'],ops)

    def test_legacy_progress_counts_operations_and_ignores_partial_lines(self):
        log=self.root/'llama.log'
        log.write_text('TILE RESULT LQ04 W8: {"status":"PASS"}\nCASE RESULT: {"status":"PASS"}\nCASE RESULT: {"status":')
        counts,done=g.legacy_counts(log,'llama_shapes')
        self.assertEqual((dict(counts),done),({'PASS':1},1))
        self.assertEqual(g.legacy_operations(log,'llama_shapes',done),1)
        (self.root/'Log_TC11.log').write_text('FINAL RESULT: PASS\nFINAL RESULT: PASS\n')
        self.assertEqual(g.legacy_operations(self.root,'test_cases',2),12)

    def test_invalid_or_extreme_input_rejected(self):
        for options in [dict(mode='bad'),dict(timeout=-1),dict(cycles='yes'),dict(reference='fake'),dict(tcs=['TC99'])]:
            with self.assertRaises(ValueError):self.suite(**options)
        with self.assertRaises(ValueError):g.validate_design(dict(name='Bad',b=3,rf=4,max_k=1024,max_m=1024))
        with self.assertRaises(ValueError):g.integer('1; rm -r x','dimension')
        with self.assertRaises(ValueError):g.reference([-32768]*1024,[-128]*1024,1)

    def test_saved_suites_designs_and_cancellation(self):
        design = self.store.save_design(dict(name='Custom',b=16,rf=8,max_k=1024,max_m=1024))
        self.assertIn(design,self.store.designs())
        value = dict(self.payload,design_ids=[design['id']])
        saved = self.store.save_suite(value)
        self.assertEqual(len(self.store.suites()),1)
        saved['name']='Renamed';self.store.save_suite(saved)
        self.assertEqual(len(self.store.suites()),1)
        job = self.store.create_job(value)
        self.assertEqual(job['status'],'QUEUED')
        self.assertEqual(self.store.cancel(job['id'])['status'],'STOPPED')
        with self.assertRaises(ValueError):self.store.folder('../secrets')

    def test_signed_packing_and_reference(self):
        for bits in (8,4,2):
            lo,hi=-(1<<(bits-1)),(1<<(bits-1))-1
            w=[lo,hi,0,1,lo,hi,1,0,hi,lo,1,0,lo,hi,0]
            data=g.pack_weights(w,3,5,bits)
            rebuilt=[]
            for j in range(3):
                for col in range(5):
                    bit=(col*3+j)*bits
                    value=(data[bit//8]>>(bit%8))&((1<<bits)-1)
                    if value >= 1<<(bits-1):value-=1<<bits
                    rebuilt.append(value)
            self.assertEqual(rebuilt,w)
            self.assertEqual(g.reference([2,-3,4],w,5),[sum([2,-3,4][j]*w[j*5+c] for j in range(3)) for c in range(5)])

    def test_sources_only_operands_python_mode(self):
        base=(g.hw.SRC/'Linear-sw.c').read_text()
        for bits in (8,4,2):
            for method in ('python','riscv','none'):
                options=dict(g.DEFAULT_OPTIONS,reference=method,integrity=True)
                source=g.test_source(base,self.design,[2,-3,4],[1,0,-1,1,0,1],bits,options)
                main=source.split('int main(void)',1)[1]
                self.assertNotIn('memcpy(',main)
                self.assertNotIn('memset(',main)
                self.assertEqual(main.count('preload_hw(3,1,2,'),1)
                self.assertEqual(main.count('matmul_hw_uniform(actual,&tx,&tw,3,2,'),1)
                self.assertEqual('reference_w' in source,method=='riscv')
                self.assertIn('PHASE %s',source)

    def test_result_verification_rejects_bad_data(self):
        options=dict(g.DEFAULT_OPTIONS)
        uart='CYCLES 1 2 3 4 5 6 CORE 20\nGUARDS PASS\nHW_OUTPUT 12 -15 \nTEST_DONE\n'
        self.assertEqual(g.parse_result(uart,[12,-15],options,0,2)['status'],'PASS')
        for bad in [uart.replace('12 -15','13 -15'),uart.replace('GUARDS PASS','GUARDS FAIL'),uart.replace('TEST_DONE','INCOMPLETE'),uart+uart]:
            self.assertNotEqual(g.parse_result(bad,[12,-15],options,0,2)['status'],'PASS')
        self.assertNotEqual(g.parse_result(uart,[12,-15],options,1,2)['status'],'PASS')
        self.assertEqual(g.parse_result(uart,None,dict(options,reference='none'),0,2)['status'],'UNVERIFIED')
        cuart='C_REFERENCE 12 -15 \n'+uart
        self.assertEqual(g.parse_result(cuart,None,dict(options,reference='riscv'),0,2)['status'],'PASS')
        self.assertNotEqual(g.parse_result(uart,None,dict(options,reference='riscv'),0,2)['status'],'PASS')

    def test_make_failure_restores_configuration(self):
        config=self.root/'Config.scala'
        original=b' x_slice = 2,\n Mem_row_factor = 4,\n Cin = 1024,\n Cout = 1024,\n DEBUG = true,\n'
        config.write_bytes(original)
        job=SimpleNamespace(data=dict(suite=dict(options=dict(g.DEFAULT_OPTIONS))),log=lambda *_:None)
        def failed_command(args,cwd,output,*rest):
            output.write_text('Deliberate unit-test make failure')
            return 2
        job.command=failed_command
        with patch.object(g.hw,'CONFIG',config):
            with self.assertRaisesRegex(RuntimeError,'Make failed'):
                g.build_design(job,self.design,original,self.root,dict())
        self.assertEqual(config.read_bytes(),original)

    def test_waiting_for_config_lock_can_be_cancelled(self):
        suite=self.suite()
        folder=self.root/'waiting';folder.mkdir()
        g.write_json(folder/'job.json',dict(suite=suite,status='QUEUED',results=[]))
        job=g.Job(folder)
        config=self.root/'locked-config';config.write_bytes(b'original configuration')
        with config.open('rb') as lock:
            g.fcntl.flock(lock,g.fcntl.LOCK_EX)
            with patch.object(g.hw,'CONFIG',config),patch.object(g.time,'sleep',side_effect=g.Cancelled('Stopped while waiting')):
                with self.assertRaises(g.Cancelled):g.run_custom(job,self.root,{})
        self.assertEqual(config.read_bytes(),b'original configuration')
        self.assertEqual(job.data['status'],'WAITING')

    def test_subprocess_timeout_and_cancellation(self):
        folder=self.root/'command';folder.mkdir()
        g.write_json(folder/'job.json',dict(status='RUNNING'))
        job=g.Job(folder)
        args=[sys.executable,'-c','import time; print("PHASE Smoke command",flush=True); time.sleep(30)']
        with self.assertRaises(TimeoutError):
            job.command(args,self.root,folder/'output.log',0.01,None,'Unit timeout')
        self.assertIsNone(job.proc)
        timer=threading.Timer(0.1,lambda:(folder/'STOP').touch())
        timer.start()
        try:
            with self.assertRaises(g.Cancelled):
                job.command(args,self.root,folder/'output.log',30,None,'Unit cancellation')
        finally:timer.cancel();timer.join()
        self.assertIsNone(job.proc)

    def test_tiled_host_reduction_uses_actual_hw_partials(self):
        suite=self.suite()
        folder=self.root/'job';folder.mkdir()
        g.write_json(folder/'job.json',dict(suite=suite,status='QUEUED',results=[],completed=0,operations_done=0))
        config=self.root/'config.scala'
        config.write_text(g.hw.CONFIG.read_text())
        temporary=self.root/'work';temporary.mkdir()
        job=g.Job(folder)
        seen=[]
        def fake_build(job,design,base,tmp,env):
            build=tmp/design['id'];build.mkdir();sim=build/'simulator';sim.touch();return sim
        def fake_hw(job,sim,design,base,options,x,w,wb,folder,env,expected):
            seen.append((len(x),len(w)//len(x)))
            # Synthetic hardware is confined to this unit test, not the product.
            actual=[sum(x[j]*w[j*(len(w)//len(x))+c] for j in range(len(x))) for c in range(len(w)//len(x))]
            return dict(status='PASS',actual=actual,expected=expected,matches=len(actual),elements=len(actual),cycles=11,core_cycles=20)
        with patch.object(g.hw,'CONFIG',config),patch.object(g,'fingerprint',lambda:{str(config):g.hw.sha(config)}),patch.object(g,'build_design',fake_build),patch.object(g,'run_operation',fake_hw):
            g.run_custom(job,temporary,{})
        self.assertEqual(len(seen),12)
        self.assertTrue(all(k<=4 and m<=3 for k,m in seen))
        self.assertEqual([r['status'] for r in job.data['results']],['PASS']*3)
        self.assertEqual([r['cycles'] for r in job.data['results']],[44]*3)
        self.assertEqual([r['matches'] for r in job.data['results']],[5]*3)

    def request(self,path,method='GET',data=None,authenticated=True,origin=None):
        handler=gui.Handler.__new__(gui.Handler)
        handler.server=SimpleNamespace(token='test-only-key',store=self.store)
        handler.path=path
        handler.headers=Message()
        handler.headers['Host']='localhost:8765'
        if authenticated:handler.headers['X-Lumax-Token']='test-only-key'
        if origin:handler.headers['Origin']=origin
        encoded=json.dumps(data).encode() if data is not None else b''
        handler.headers['Content-Length']=str(len(encoded))
        handler.rfile=io.BytesIO(encoded)
        response={}
        handler.send=lambda code,value,mime=None,cookie=False:response.update(code=code,value=value)
        if method=='GET':handler.do_GET()
        else:handler.do_POST()
        return response

    def test_http_auth_plan_and_submission(self):
        self.assertEqual(self.request('/api/runs',authenticated=False)['code'],401)
        result=self.request('/api/plan','POST',self.payload)
        self.assertEqual(result['code'],200)
        result=self.request('/api/runs','POST',self.payload)
        self.assertEqual(result['code'],200)
        self.assertEqual(result['value']['status'],'QUEUED')
        self.assertEqual(self.request('/api/runs')['value']['runs'][0]['id'],result['value']['id'])
        self.assertEqual(self.request('/api/runs','POST',self.payload,origin='http://other.example')['code'],403)
        self.assertEqual(self.request('/api/designs','POST',[])['code'],400)

    def test_fast_llama_http_plan_and_saved_suite(self):
        payload=dict(name='Fast classifier',kind='llama_fast',design_ids=['b8_rf8'])
        plan=self.request('/api/plan','POST',payload)
        self.assertEqual(plan['code'],200)
        self.assertEqual(plan['value']['operations'],18)
        saved=self.request('/api/suites','POST',payload)
        self.assertEqual(saved['code'],200)
        self.assertEqual(saved['value']['kind'],'llama_fast')
        self.assertEqual(len(saved['value']['tests']),6)
        job=self.request('/api/runs','POST',payload)
        self.assertEqual(job['code'],200)
        self.assertEqual(job['value']['operations_total'],18)
        self.assertEqual(job['value']['status'],'QUEUED')

    def test_fp16_api_format_and_tolerance_contract(self):
        initial=self.request('/api/initial')['value']
        self.assertTrue(initial['features']['fp_comparison'])
        created=self.request('/api/designs','POST',dict(name='FP API',b=2,rf=4,max_k=8,max_m=8,activation='fp16'))
        self.assertEqual(created['code'],200)
        self.assertEqual(created['value']['activation'],'fp16')
        payload=dict(name='FP API suite',kind='custom',design_ids=[created['value']['id']],
                     tests=[dict(k=4,m=4,weights=[8,4,2])],
                     options=dict(fp_comparison='tolerance',atol=1e-6,rtol=1e-5))
        saved=self.request('/api/suites','POST',payload)
        self.assertEqual(saved['code'],200)
        self.assertEqual(saved['value']['options']['rtol'],1e-5)
        job=self.request('/api/runs','POST',payload)
        self.assertEqual(job['value']['suite']['designs'][0]['activation'],'fp16')
        payload['options']['atol']=-1
        self.assertEqual(self.request('/api/plan','POST',payload)['code'],400)

    def test_log_reads_stay_inside_logs_root(self):
        logs=self.root/'logs';logs.mkdir(exist_ok=True)
        (logs/'example.log').write_text('Actual test log')
        (self.root/'secret').write_text('Do not expose')
        with patch.object(g,'LOGS',logs):
            self.assertEqual(self.request('/api/log?path=example.log')['code'],200)
            self.assertEqual(self.request('/api/log?path=../secret')['code'],404)
            self.assertEqual(self.request('/api/download?path=../secret')['code'],404)


if __name__=='__main__':
    unittest.main()
