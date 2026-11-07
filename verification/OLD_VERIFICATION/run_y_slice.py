#!/usr/bin/env python3
"""Small row-parallel LUMAX checks using make, RISC-V and real Verilator."""
import argparse
from datetime import datetime
import fcntl
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
import run_fp16 as fp
import run_llama_shapes as hw

ROOT = Path(__file__).resolve().parent


def cases(wb, floating, ys):
    row = [1, 2, 3, 4, 5, 6] if not floating else [0x3c00, 0xc000, 1, 0x7bff, 0x3555, 0x0400]
    weights = ([1, 2, 2, 1, 3, 4, 4, 3, 5, 6, 6, 5] if wb != 2 else
               [1, 0, -1, 1, 0, -1, 1, -2, -2, 1, 1, 0])
    result = []
    def add(name, rows, k, m, x, w, block):
        expected = sum((fp.reference(x[r*k:(r+1)*k], w, m, floating) for r in range(rows)), [])
        result.append(dict(name=name, r=rows, k=k, m=m, x=x, w=w, block=block, expected=expected))
    # Legacy INT2 software blocks must contain a full packed weight register.
    small_block = 2 if ys > 1 else max(2, 8 // wb)
    add('identical_rows' if ys > 1 else 'legacy_one_row', 2 if ys > 1 else 1,
        6, 2, row * (2 if ys > 1 else 1), weights, small_block)
    if ys > 1:
        other = [7, 8, 9, 10, 11, 12] if not floating else [v ^ 0x8000 for v in row]
        add('different_rows', 2, 6, 2, row + other, weights, 2)
        x = [r*7+j-6 for r in range(3) for j in range(7)] if not floating else [
            [0x3c00, 0xbc00, 0x4000, 1, 0x3555, 0x0400, 0x4200][j] ^ (0x8000 if r % 2 else 0)
            for r in range(3) for j in range(7)]
        lo, hi = -(1 << (wb-1)), (1 << (wb-1))-1
        w = [lo + (j*3+c) % (hi-lo+1) for j in range(7) for c in range(3)]
        add('odd_row_and_K_tail', 3, 7, 3, x, w, 2)
    lo, hi = -(1 << (wb-1)), (1 << (wb-1))-1
    x = [j-3 for j in range(8)] if not floating else [0x3c00, 0xc000, 1, 0x8001, 0x3555, 0x4400, 0x0400, 0xbc00]
    add('full_block', 2 if ys > 1 else 1, 8, 8, x * (2 if ys > 1 else 1),
        [lo+(j*5+c*3) % (hi-lo+1) for j in range(8) for c in range(8)], 8)
    add('multiple_groups_identical', ys*2, 6, 2, row*(ys*2), weights, 4)
    k, m = 7, 3
    x = [r*k+j-6 for r in range(ys*2) for j in range(k)] if not floating else [
        [0x3c00, 0xbc00, 0x4000, 1, 0x3555, 0x0400, 0x4200][j] ^ (0x8000 if r % 2 else 0)
        for r in range(ys*2) for j in range(k)]
    add('multiple_groups_different', ys*2, k, m, x,
        [lo+(j*3+c) % (hi-lo+1) for j in range(k) for c in range(m)], small_block)
    return result


def sequence_cases(wb, floating, ys):
    available=cases(wb,floating,ys)
    a,b,c=available[0],next(t for t in available if t['name']=='full_block'),available[-1]
    r,k,m=ys,4,2
    poison_x=([0x7e00,0x3c00,0xbc00,0] if floating else [-32768,32767,-32768,32767])*r
    weights=[1,-1]*k
    def directed(name,x):
        expected=sum((fp.reference(x[row*k:(row+1)*k],weights,m,floating) for row in range(r)),[])
        return dict(name=name,r=r,k=k,m=m,x=x,w=weights,block=4,expected=expected)
    poison=directed('NaN_flags' if floating else 'large_signed',poison_x)
    zero=directed('zero_after_poison',[0]*(r*k))
    return [dict(t,name=f'{i+1}_{label}') for i,(label,t) in enumerate([
        ('A',a),('B_changed_shape_and_block',b),('A_repeat',a),('C_multirow',c),
        ('poison',poison),('zero_after_poison',zero),('C_repeat',c),('A_final',a)])]


def source(base, tests, wb, floating, ys, b, rf, consecutive=False):
    text = base.split('int main() {', 1)[0]
    for key, value in dict(IN_BITS=16, W_BITS=wb, XS=b, YS=ys, Mem_row_factor=rf, Debug=0).items():
        text, count = re.subn(r'(?m)^(#define\s+'+key+r'\s+)\d+', lambda m: m[1]+str(value), text)
        if count != 1: raise ValueError('Missing C define '+key)
    text += '\n#undef printf\n#include <stddef.h>\n'
    for i, t in enumerate(tests):
        packed = bytearray((t['k']*t['m']*wb+7)//8)
        for col in range(t['m']):
            for j in range(t['k']):
                bit = (col*t['k']+j)*wb
                packed[bit//8] |= (t['w'][j*t['m']+col] & ((1<<wb)-1)) << (bit % 8)
        for name, ctype, data, const in [
            ('x','input_t',t['x'],True), ('w','uint8_t',packed,True),
            ('o','output_t',[0x5a5a5a5a]*(t['r']*t['m']),False)]:
            text += f'static {"const " if const else ""}struct __attribute__((packed,aligned(8))) {{ uint8_t pre[8]; {ctype} data[{len(data)}]; uint8_t post[16]; }} {name}{i} = '
            text += '{{'+','.join(['165']*8)+'},{'+','.join(map(str,data))+'},{'+','.join(['165']*16)+'}};\n'
            text += f'_Static_assert(offsetof(__typeof__({name}{i}),data)==8,"aligned payload");\n'
    text += '''
static unsigned long previous_combined=0;
static int boundary(const uint8_t *pre,const uint8_t *post) {
  for(int i=0;i<8;i++)if(pre[i]!=165)return 0;
  for(int i=0;i<16;i++)if(post[i]!=165)return 0;
  return 1;
}
int main(void) {int failed=0;
'''
    if consecutive:
        text+=f'unsigned long cores[{len(tests)}];\n'
        for i,t in enumerate(tests):
            text+=f'''{{
 QuantizedTensor_input tx={{.q=(input_t*)x{i}.data}};
 QuantizedTensor_weight tw={{.q=(weight_t*)w{i}.data}};
 output_t *out=o{i}.data;
 preload_hw({t['k']},{t['r']},{t['m']},x_slice_max*RF,y_slice,tx.q,tw.q,out);
 load_w(tw.q,{t['block']},{(t['block']+8//wb-1)//(8//wb)},{(t['k']+t['block']-1)//t['block']});
 unsigned long begin=rdcycle();
 matmul_hw_uniform(out,&tx,&tw,{t['k']},{t['m']},x_slice_max*RF,y_slice,{t['r']});
 cores[{i}]=rdcycle()-begin;
}}
'''
        text+='/* All hardware calls completed. Only now read counters, compare and print. */\n'
        text+='unsigned long final_pc[6];for(int j=0;j<6;j++)final_pc[j]=read_rd();\n'
        text+='printf("FINAL_COUNTERS %lu %lu %lu %lu %lu %lu\\n",final_pc[0],final_pc[1],final_pc[2],final_pc[3],final_pc[4],final_pc[5]);\n'
    for i, t in enumerate(tests):
        n = t['r'] * t['m']
        header=f'''{{
 QuantizedTensor_input tx={{.q=(input_t*)x{i}.data}};
 QuantizedTensor_weight tw={{.q=(weight_t*)w{i}.data}};
 output_t *out=o{i}.data,sw[{n}];
'''
        text+=header
        if consecutive:
            text+=f'unsigned long core=cores[{i}],pc[6]={{0}}; /* Stage counters intentionally not sampled between calls. */\n'
        else:
            text += f'''
 preload_hw({t['k']},{t['r']},{t['m']},x_slice_max*RF,y_slice,tx.q,tw.q,out);
 /* Restrict the internal K block while keeping the original RoCC helpers. */
 load_w(tw.q,{t['block']},{(t['block']+8//wb-1)//(8//wb)},{(t['k']+t['block']-1)//t['block']});
 unsigned long start=rdcycle();
 matmul_hw_uniform(out,&tx,&tw,{t['k']},{t['m']},x_slice_max*RF,y_slice,{t['r']});
 unsigned long core=rdcycle()-start,pc[6];
 for(int j=0;j<6;j++)pc[j]=read_rd();
 for(int j=6;j<32;j++)(void)read_rd();
 unsigned long combined=pc[5];pc[5]-=previous_combined;previous_combined=combined;
'''
        text+=f'''
 matmul_sw_uniform(sw,&tx,&tw,{t['k']},{t['m']},{t['r']});
 int same=!memcmp(out,sw,{n}*4);
 int safe=boundary(x{i}.pre,x{i}.post)&&boundary(w{i}.pre,w{i}.post)&&boundary(o{i}.pre,o{i}.post);
 printf("RESULT {i} GUARDS %d C_MATCH %d CORE %lu PC %lu %lu %lu %lu %lu %lu\\n",safe,same,core,pc[0],pc[1],pc[2],pc[3],pc[4],pc[5]);
 printf("HW {i} ");for(int j=0;j<{n};j++)printf("%08x ",(uint32_t)out[j]);printf("\\n");fflush(stdout);
 failed|=!same||!safe;
}}
'''
    return text+'printf("TEST_DONE %d\\n",failed);return failed;}\n'


def run(args):
    work = Path(tempfile.mkdtemp(prefix='lumax-y-slice-test-'))
    logdir = ROOT/'logs/y_slice';logdir.mkdir(parents=True,exist_ok=True)
    logfile = logdir/('run_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.log')
    def log(value):
        with logfile.open('a') as out: out.write(str(value)+'\n')
        print(value,flush=True)
    def command(cmd, cwd, output, timeout, env):
        log('COMMAND: '+shlex.join(list(map(str,cmd))))
        with output.open('w') as out:
            proc = subprocess.Popen(cmd,cwd=cwd,env=env,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
            try: return proc.wait(timeout)
            except BaseException:
                import os,signal
                os.killpg(proc.pid,signal.SIGTERM)
                try: proc.wait(5)
                except subprocess.TimeoutExpired: os.killpg(proc.pid,signal.SIGKILL);proc.wait()
                raise
    env = hw.environment();runtime=work/'runtime';runtime.mkdir();env['XDG_RUNTIME_DIR']=str(runtime)
    env['SBT_OPTS']=(env.get('SBT_OPTS') or f'-Dsbt.ivy.home={hw.CY}/.ivy2 -Dsbt.global.base={hw.CY}/.sbt -Dsbt.boot.directory={hw.CY}/.sbt/boot/')+' -Dsbt.server.forcestart=true -Dsbt.server.autostart=false -Dsbt.supershell=false'
    base=(hw.SRC/'Linear-sw.c').read_text();results=[]
    sequence=args.consecutive or args.interleaved_checks
    modes=[False,True] if args.mode=='both' else [args.mode=='fp16']
    log('ROW PARALLEL VERIFICATION — actual make-built Verilator / original Linear-sw.c helpers')
    log('LOG: '+str(logfile)+'\nWORK: '+str(work))
    calls=2 if args.pair_only else 8
    if args.consecutive:
        log(f'CONSECUTIVE MODE: {calls} matmuls per ELF in one simulator boot, without reset. No printf, reference calculation or counter reads between hardware calls; outputs checked after the entire sequence. Per-operation core cycles only; FINAL_COUNTERS mix last-operation stages with cumulative combined counter.')
    elif args.interleaved_checks:
        log(f'INTERLEAVED CHECKS: {calls} matmuls per ELF in one simulator boot without reset; C reference, output checks, printing and counter reads after each call.')
    with hw.CONFIG.open('rb') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        original=hw.CONFIG.read_bytes()
        for floating in modes:
            for ys in args.y_slices:
                label=('fp16' if floating else 'int16')+f'_ys{ys}'
                folder=work/label;folder.mkdir();text=original.decode()
                for key,value in dict(Rin=8,Cin=32,Cout=16,x_slice=args.b,Mem_row_factor=args.rf,y_slice=ys,FP16_ACTIVATIONS=str(floating).lower()).items():
                    text,count=re.subn(r'(?m)^(\s*'+key+r'\s*=\s*)(?:\d+|true|false)',lambda m:m[1]+str(value),text)
                    if count!=1:raise ValueError('Missing configuration '+key)
                snapshot=folder/'Config.scala';snapshot.write_text(text)
                sim=folder/'simulator-LUMAXROcketConfig'
                try:
                    hw.CONFIG.write_text(text)
                    code=command(['make','-j'+str(args.jobs),'CONFIG=LUMAXROcketConfig','sim='+str(sim),'gen_dir='+str(folder/'generated-src')],hw.CY/'sims/verilator',folder/'make.log',args.build_timeout,env)
                finally:
                    if hw.CONFIG.read_bytes()==text.encode():hw.CONFIG.write_bytes(original)
                    elif hw.CONFIG.read_bytes()!=original:raise RuntimeError('External Config.scala edit; not overwritten')
                if code:
                    log((folder/'make.log').read_text(errors='replace')[-6000:]);raise RuntimeError('Make failed '+label)
                log('MAKE PASS '+label+' SIMULATOR_SHA256='+hw.sha(sim))
                for wb in (8,4,2):
                    selected=sequence_cases(wb,floating,ys) if sequence else cases(wb,floating,ys)
                    if args.pair_only: selected=selected[:2]
                    if args.multirow_only:
                        selected=[t for t in selected if t['name'].startswith('multiple_groups_')]
                    # Default smoke checks isolate one-row cases. Sequence modes
                    # deliberately share one boot across cases for both Y_SLICE values.
                    # Two-row default smoke cases also share one boot.
                    boots=[selected] if sequence else [[t] for t in selected] if ys == 1 else [selected]
                    for boot,tests in enumerate(boots):
                        case=folder/f'w{wb}_boot{boot}';case.mkdir()
                        log(f'BOOT {label} W{wb}: '+','.join(t['name'] for t in tests))
                        (case/'Linear-sw.c').write_text(source(base,tests,wb,floating,ys,args.b,args.rf,args.consecutive))
                        log('TEST SOURCE SHA256='+hw.sha(case/'Linear-sw.c'))
                        for name in ['build.sh','rocc.h','compiler.h']:shutil.copy2(hw.SRC/name,case/name)
                        case_env=dict(env,LUMAX_CONFIG_PATH=str(snapshot),TEST_CFLAGS=f'-O2 -DLUMAX_FP16_ACTIVATIONS={int(floating)}')
                        assert command(['bash','./build.sh','baremetal'],case,case/'build.log',600,case_env)==0,(case/'build.log').read_text()
                        elf=case/'Linear-sw.riscv';log('ELF SHA256='+hw.sha(elf)+'; no Python answers embedded')
                        code=command([str(sim),'+permissive','+max-cycles=1000000000','+loadmem='+str(elf),'+permissive-off',str(elf)],case,case/'uart.log',args.timeout,case_env)
                        uart=(case/'uart.log').read_text(errors='replace');log('UART\n'+uart)
                        outputs={int(i):[int(v,16) for v in data.split()] for i,data in re.findall(r'^HW (\d+) (.*)$',uart,re.M)}
                        flags={int(i):(int(g),int(c),int(core),list(map(int,pc.split()))) for i,g,c,core,pc in re.findall(r'^RESULT (\d+) GUARDS (\d) C_MATCH (\d) CORE (\d+) PC ((?:\d+ ){5}\d+)$',uart,re.M)}
                        complete=code==0 and uart.splitlines().count('TEST_DONE 0')==1 and len(outputs)==len(tests)
                        reported_failure=(code!=0 and uart.splitlines().count('TEST_DONE 1')==1
                            and len(outputs)==len(flags)==len(tests)
                            and any(g!=1 or c!=1 for g,c,_,_ in flags.values()))
                        for i,t in enumerate(tests):
                            safe,match,core,pc=flags.get(i,(0,0,0,[0]*6));actual=outputs.get(i,[])
                            status='PASS' if safe==match==1 and actual==t['expected'] and (core>0 if args.consecutive else sum(pc)>0) else 'FAIL'
                            if sequence and not (complete or reported_failure): status='FAIL'
                            record=dict(mode='FP16' if floating else 'INT16',y_slice=ys,b=args.b,rf=args.rf,wb=wb,case=t['name'],shape=[t['r'],t['k'],t['m']],block=t['block'],status=status,hw=actual,python=t['expected'],cycles=sum(pc[j] for j in (0,1,4,5)),pc=pc,core=core)
                            if args.consecutive:
                                record.update(cycles=None,pc=None,consecutive=True)
                            elif args.interleaved_checks:
                                record.update(consecutive=True,checks_between_calls=True)
                            log('OPERANDS: '+json.dumps(dict(X=t['x'],W=t['w'])))
                            log('CASE RESULT: '+json.dumps(record,sort_keys=True));results.append(record)
                        if not sequence and (not complete or any(r['status']!='PASS' for r in results)):
                            raise RuntimeError('Verification failed; retained '+str(work))
                        if sequence and not complete:
                            log('Sequence did not exit successfully; individual results above retain FAILs and missing outputs. Continuing remaining configurations.')
        assert hw.CONFIG.read_bytes()==original
    failures=sum(r['status']!='PASS' for r in results)
    log('TOTAL: '+json.dumps(dict(PASS=len(results)-failures,FAIL=failures)))
    shutil.rmtree(work);log('RUN COMPLETE. Config restored; temporary builds, vectors and executables removed.')
    return int(failures>0)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=['int16','fp16','both'],default='int16')
    p.add_argument('--y-slices',nargs='+',type=int,choices=[1,2],default=[1,2])
    p.add_argument('--b',type=int,choices=[2,4,8],default=2)
    p.add_argument('--rf',type=int,choices=[4,8],default=4)
    p.add_argument('--jobs',type=int,default=8)
    p.add_argument('--timeout',type=int,default=1800)
    p.add_argument('--build-timeout',type=int,default=7200)
    selection=p.add_mutually_exclusive_group()
    selection.add_argument('--multirow-only',action='store_true',help='Check two row groups: 2 rows for y_slice=1, 4 rows for y_slice=2')
    selection.add_argument('--consecutive',action='store_true',help='Eight matmuls per ELF, no simulator reset or SW reference between calls')
    selection.add_argument('--interleaved-checks',action='store_true',help='Eight matmuls per ELF, no simulator reset, C verification between calls')
    p.add_argument('--pair-only',action='store_true',help='Keep just A then B; use with a consecutive/interleaved sequence')
    args=p.parse_args()
    if args.pair_only and not (args.consecutive or args.interleaved_checks):
        p.error('--pair-only requires --consecutive or --interleaved-checks')
    raise SystemExit(run(args))
