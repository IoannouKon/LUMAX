#!/usr/bin/env python3
"""Compare the hypothetical column pipeline with archived controller-events-v3.

This runs cycle schedulers only. It neither builds RTL nor checks matmul values.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import itertools
from pathlib import Path
import shlex
import statistics
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / 'Performance Modeling/Model_ColumnPipeline.py'
SHAPES = {
    'small4': (1, 4, 4),
    'small32': (1, 32, 32),
    'square128': (1, 128, 128),
    'square512': (1, 512, 512),
    'square1024': (1, 1024, 1024),
    'expand': (1, 288, 768),
    'contract': (1, 768, 288),
    'odd3': (3, 7, 17),
    'rows4': (4, 128, 128),
    'rows8': (8, 512, 512),
    'odd5': (5, 129, 257),
    'single_column': (1, 1024, 1),
}
STAGES = dict(load_x='cycles_load_X', generate='cycles_generate_Products',
              loadw_select='cycles_loadW_select', store_o='cycles_store_output',
              select='cycles select Only', load_w='cycles load w Only')


def load_experiment():
    spec = importlib.util.spec_from_file_location('column_experiment', MODEL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def summary(rows):
    before = sum(r['v3_total'] for r in rows)
    after = sum(r['pipeline_total'] for r in rows)
    reductions = [r['reduction_pct'] for r in rows]
    return len(rows), before, after, 100*(before-after)/before, statistics.median(reductions), min(reductions), max(reductions)


def report(path, rows, args, reference_sha, experiment_sha, seconds):
    n, old, new, weighted, median, minimum, maximum = summary(rows)
    lines = [
        '# Column pipeline scheduling experiment', '',
        f'Generated: {datetime.now(timezone.utc).isoformat()}', '',
        '**These are Python cycle predictions for a proposed design, not Verilator results.** '
        'This script changes neither the RTL nor production Model.py. PASS means scheduling checks passed; '
        'no numerical matrix multiplication is verified by this experiment.', '',
        '## Models and assumptions', '',
        '- Reference: `controller-events-v3`, the archived controller before tagged-column selection.',
        '- Candidate: `column-pipeline-experimental-v1`, in [Model_ColumnPipeline.py](../Model_ColumnPipeline.py).',
        '- Keep five cycles from the end of a column\'s reads to retirement. Allow the next column to issue during that latency.',
        '- Release a weight buffer after its final read is captured. Keep the same two weight buffers, '
        'DMA capacity, memory ports, b, RF, Y_SLICE and output DMA scheduling.',
        '- Assume tagged valid data and output-column indices, and activation/weight addressing that can '
        'switch columns without extra bubbles. Area and clock frequency are not estimated.',
        '- Keep weight-ready stalls, source-ID limits and the memory profile. The final pipeline drain '
        'is required before changing K window or storing outputs.',
        '- INT16 and FP16 have the same controller timing assumptions; test both formats explicitly.',
        '- V3\'s known Y_SLICE=1 tail-padding behavior is retained in both schedulers.', '',
        '## Sweep', '',
        f'- {n:,} model pairs; b={args.b}, RF={args.rf}, Y_SLICE={args.ys}, DMA IDs={args.ids}.',
        f'- W bits={args.weights}; activations={args.activations}; memory profile=`{args.memory_profile}`.',
        '- X uses 16-bit activations; outputs use 32-bit words. Shapes are X[R,K] × W[K,M].',
        '- Default, disjoint, cache-line-aligned addresses and the same cold-cache assumptions in each pair.',
        f'- Runtime: {seconds:.2f} seconds.', '',
        '| Shape | X | W |', '|---|---|---|',
    ]
    for name in args.shapes:
        r, k, m = SHAPES[name]
        lines.append(f'| {name} | {r}×{k} | {k}×{m} |')
    lines += ['', '## Results', '',
              f'- {n:,}/{n:,} scheduling checks passed. The five-cycle-gap control exactly '
              'matches every v3 result field except the version label for every pair.',
              f'- Faster: {sum(r["saved_cycles"]>0 for r in rows):,}; unchanged: '
              f'{sum(r["saved_cycles"]==0 for r in rows):,}; slower: '
              f'{sum(r["saved_cycles"]<0 for r in rows):,}.',
              f'- Per-case cycle reduction: min **{minimum:.2f}%**, median **{median:.2f}%**, max **{maximum:.2f}%**.',
              f'- Sum across this synthetic sweep: {old:,} → {new:,} cycles; '
              f'**{weighted:.2f}%** reduction weighted by baseline cycle count. '
              'This weighting does not represent an application workload.', '',
              'A zero reduction is valid: one output column has no inter-column handoff, and '
              'weight-memory stalls can absorb the removed pipeline bubbles. These predictions '
              'are opportunities for an RTL experiment, not measured hardware speedups.', '']
    for field, label in [('w_bits','Weight precision'), ('b','b'), ('rf','RF'), ('ys','Y_SLICE')]:
        lines += [f'### By {label}', '',
                  f'| {label} | Cases | Cycle-weighted reduction | Median reduction | Min | Max |',
                  '|---|---:|---:|---:|---:|---:|']
        for value in sorted({r[field] for r in rows}):
            count, _, _, gain, mid, lo, hi = summary([r for r in rows if r[field]==value])
            lines.append(f'| {value} | {count} | {gain:.2f}% | {mid:.2f}% | {lo:.2f}% | {hi:.2f}% |')
        lines.append('')
    # Representative large square, with fixed physical settings for easy comparison.
    lines += ['### 1024×1024 weights, X=1×1024', '',
              'Examples below use INT16, RF=8, Y_SLICE=2 and DMA IDs=6 when those settings are in the sweep.', '',
              '| b | W bits | V3 cycles | Pipeline cycles | Saved cycles | Reduction |',
              '|---|---:|---:|---:|---:|---:|']
    selected = [r for r in rows if r['shape']=='square1024' and r['activation']=='int16'
                and r['rf']==8 and r['ys']==2 and r['dma_ids']==6]
    for r in sorted(selected, key=lambda r:(r['b'],-r['w_bits'])):
        lines.append(f"| {r['b']} | {r['w_bits']} | {r['v3_total']:,} | {r['pipeline_total']:,} | {r['saved_cycles']:,} | {r['reduction_pct']:.2f}% |")
    lines += ['', '## Stages and full data', '',
              '[results.csv](results.csv) contains every parameter pair, total cycles, additive stages, '
              'the overlapping load-W/select counters, request counts and predicted reductions. '
              'Do not add the standalone load-W and selection counters to the combined stage.', '',
              'The production GUI uses current Model.py/v4. This archived five-cycle experiment does not replace it.', '',
              '## Reproduce', '', 'From the generator/repository root:', '', '```bash',
              ('PYTHONDONTWRITEBYTECODE=1 python3 verification/compare_column_pipeline.py '
              + ' '.join(shlex.quote(v) for v in sys.argv[1:])).rstrip(), '```', '',
              f'V3 SHA256: `{reference_sha}`  ',
              f'Experiment SHA256: `{experiment_sha}`', '',
              'The comparison validates the event model, not achievable Fmax or final hardware correctness. '
              'A real implementation must preserve SRAM latency, valid/tag alignment, buffer reuse and output accumulation.', '']
    path.write_text('\n'.join(lines))


def run(args):
    model = load_experiment()
    reference = model._BASE
    reference_sha = model.V3_MODEL_SHA256
    experiment_sha = hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest()
    args.output.mkdir(parents=True, exist_ok=True)
    combinations = list(itertools.product(args.shapes,args.weights,args.b,args.rf,args.ys,args.ids,args.activations))
    rows = []
    started = last_message = time.monotonic()
    print(f'Python scheduling comparison only: {len(combinations)} pairs; archived v3 reference; no RTL builds or edits.', flush=True)
    with (args.output/'results.csv').open('w', newline='') as output:
        writer = None
        for index, (shape,wb,b,rf,ys,ids,activation) in enumerate(combinations, 1):
            r,k,m = SHAPES[shape]
            parameters = dict(Rin=r,Cin=k,Cout=m,in_bits=16,w_bits=wb,XS=b,
                              YS=ys,ID=ids,factor=rf,activation=activation,
                              memory_profile=args.memory_profile)
            old = reference.compute_cycles(**parameters)
            control = model.compute_cycles(**parameters,column_gap=5)
            for key,value in old.items():
                if key!='model_version' and control[key]!=value:
                    raise AssertionError(('v3 control mismatch',parameters,key,value,control[key]))
            new = model.compute_cycles(**parameters)
            assert old['LoadOp']==new['LoadOp'] and old['WriteOp']==new['WriteOp']
            assert old['Repeat activation Window']==new['Repeat activation Window']
            assert old['cycles_generate_Products']==new['cycles_generate_Products']
            assert new['total_cycles']==sum(new[STAGES[s]] for s in ('load_x','generate','loadw_select','store_o'))
            assert new['total_cycles']<=old['total_cycles'],parameters
            saved = old['total_cycles']-new['total_cycles']
            if m==1:
                assert saved==0,parameters
            row = dict(shape=shape,rows=r,k=k,m=m,in_bits=16,w_bits=wb,b=b,rf=rf,
                       ys=ys,dma_ids=ids,activation=activation,memory_profile=args.memory_profile,
                       status='PASS',v3_total=old['total_cycles'],pipeline_total=new['total_cycles'],
                       saved_cycles=saved,reduction_pct=100*saved/old['total_cycles'],
                       speedup=old['total_cycles']/new['total_cycles'],windows=old['Repeat activation Window'],
                       read_requests=old['LoadOp'],write_requests=old['WriteOp'])
            for name,key in STAGES.items():
                row['v3_'+name] = old[key]
                row['pipeline_'+name] = new[key]
            if writer is None:
                writer = csv.DictWriter(output, fieldnames=list(row))
                writer.writeheader()
            writer.writerow(row)
            rows.append(row)
            if time.monotonic()-last_message>=20 or index==len(combinations):
                print(f'{index}/{len(combinations)} PASS; {time.monotonic()-started:.1f}s; latest {shape} b{b}/RF{rf}/YS{ys}/W{wb}/{activation}',flush=True)
                output.flush()
                last_message = time.monotonic()
    assert hashlib.sha256(model._BASE_PATH.read_bytes()).hexdigest()==reference_sha
    assert hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest()==experiment_sha
    seconds = time.monotonic()-started
    report(args.output/'README.md',rows,args,reference_sha,experiment_sha,seconds)
    n,_,_,weighted,median,lo,hi = summary(rows)
    print(f'DONE: {n} PASS, 0 FAIL; cycle-weighted reduction {weighted:.2f}%, median {median:.2f}%, range {lo:.2f}..{hi:.2f}%.',flush=True)
    print('Report: '+str(args.output/'README.md'),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'Performance Modeling/ColumnPipeline')
    parser.add_argument('--shapes',nargs='+',choices=SHAPES,default=list(SHAPES))
    parser.add_argument('--b',nargs='+',type=int,default=[2,4,8])
    parser.add_argument('--rf',nargs='+',type=int,default=[4,8])
    parser.add_argument('--ys',nargs='+',type=int,default=[1,2,4])
    parser.add_argument('--ids',nargs='+',type=int,default=[6])
    parser.add_argument('--weights',nargs='+',type=int,choices=[8,4,2],default=[8,4,2])
    parser.add_argument('--activations',nargs='+',choices=['int16','fp16'],default=['int16','fp16'])
    parser.add_argument('--memory-profile',choices=['uniform','chipyard_llc'],default='chipyard_llc')
    run(parser.parse_args())


if __name__=='__main__':
    main()
