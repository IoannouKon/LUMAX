"""Read saved HW measurements and call the editable cycle model directly.

No cycle equations live here. Model.py is reread and compiled on every comparison;
only its unused notebook plotting imports are skipped to avoid pip dependencies.
"""
import ast
import collections
import csv
import hashlib
import json
import math
from pathlib import Path
import re

MODEL_PATH = Path(__file__).resolve().parents[1] / 'Performance Modeling' / 'Model.py'
STAGES = {
    'total': ('Total active cycles', 'total_cycles'),
    'load_x': ('Load X', 'cycles_load_X'),
    'generate': ('Generate products', 'cycles_generate_Products'),
    'loadw_select': ('Combined Load W/Select', 'cycles_loadW_select'),
    'store_o': ('Store output', 'cycles_store_output'),
    'load_w': ('Load W alone (overlaps)', 'cycles load w Only'),
    'select': ('Select alone (overlaps)', 'cycles select Only'),
}
MAIN = ('load_x', 'generate', 'loadw_select', 'store_o')


def model_info():
    source = MODEL_PATH.read_bytes()
    return dict(path=str(MODEL_PATH), sha256=hashlib.sha256(source).hexdigest(),
                bytes=len(source))


def load_model():
    # Read once so the fingerprint and executed source are the same snapshot.
    source = MODEL_PATH.read_bytes()
    tree = ast.parse(source, filename=str(MODEL_PATH))
    plotting = ('matplotlib', 'ipywidgets', 'seaborn')
    body = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names = [n for n in node.names if n.name.split('.')[0] not in plotting]
            if names:
                node.names = names
                body.append(node)
        elif isinstance(node, ast.ImportFrom) and (node.module or '').split('.')[0] in plotting:
            continue
        else:
            body.append(node)
    tree.body = body
    namespace = dict(__file__=str(MODEL_PATH), __name__='lumax_cycle_model')
    exec(compile(tree, str(MODEL_PATH), 'exec'), namespace)
    function = namespace.get('compute_cycles')
    if not callable(function):
        raise ValueError('Model.py must define compute_cycles(**parameters)')
    return function, dict(path=str(MODEL_PATH), sha256=hashlib.sha256(source).hexdigest())


def sources(logs):
    """Only advertised saved artifacts may be requested through the API."""
    logs = Path(logs)
    found = []
    specs = [('regression', 'run_*/total_results.csv'),
             ('test_cases', 'run_*/Log_TC*.log'),
             ('llama_shapes', 'run_*.log'), ('matrix_1024', 'run_*.log'),
             ('gui', 'run_*/job.json')]
    for kind, pattern in specs:
        for path in sorted((logs / kind).glob(pattern), reverse=True):
            if not path.resolve().is_relative_to(logs.resolve()):
                continue
            name = path.parent.name if kind == 'regression' else path.name
            if kind == 'gui':
                data = json.loads(path.read_text())
                # Built-in GUI jobs already appear as their linked raw suite logs.
                if data.get('suite', {}).get('kind') not in ('custom', 'llama_fast'):
                    continue
                name = data.get('name', path.parent.name) + ' · ' + data.get('status', '')
            found.append(dict(id=str(path.relative_to(logs)), name=name, kind=kind,
                              modified=path.stat().st_mtime))
    return found


def physical_parameters(text=''):
    """Read saved Scala settings; historical runner defaults fill absent fields."""
    def value(key, default):
        matches = re.findall(r'(?m)^\s*' + key + r'\s*=\s*(\d+)\b', text)
        values = set(map(int, matches))
        if len(values) > 1:
            raise ValueError('Inconsistent recorded hardware parameter: ' + key)
        return next(iter(values), default)
    return dict(YS=value('y_slice', 1), ID=value('Dma_Ids', 2),
                DMA_WIDTH=value('DMA_Bytes', 8) * 8,
                IN_MAX=value('XBitWidth', 16), IN_min=8,
                W_MAX=value('WBitWidth', 8), buffers_w=2, HS=8)


def parameters(k, m, wb, b, rf, in_bits=16, physical=None):
    return dict(physical or physical_parameters(), Rin=1, Cin=int(k), Cout=int(m),
                in_bits=int(in_bits), w_bits=int(wb), XS=int(b), factor=int(rf))


def pc_stages(pc):
    if not isinstance(pc, list) or len(pc) != 6:
        return {}
    return dict(zip(('load_x', 'generate', 'load_w', 'select', 'store_o', 'loadw_select'), pc))


def record(source, identifier, design, k, m, wb, total, stages=None,
           status='PASS', reason='', physical=None, in_bits=16, **extra):
    match = re.fullmatch(r'b(\d+)_rf(\d+)', design)
    saved_b, saved_rf = extra.pop('b', None), extra.pop('rf', None)
    b, rf = ((saved_b, saved_rf) if saved_b is not None and saved_rf is not None
             else map(int, match.groups()) if match else (None, None))
    if b is None or rf is None:
        reason = reason or 'Recorded b/RF parameters are missing'
    stages = stages or {}
    if status not in ('PASS', 'UNVERIFIED'):
        reason = reason or 'HW status is ' + status
    if not isinstance(total, (int, float)) or not math.isfinite(total) or total <= 0:
        reason = reason or 'No valid positive hardware cycle measurement'
    if all(key in stages for key in MAIN) and sum(stages[key] for key in MAIN) != total:
        reason = reason or 'Stage counters do not sum to the recorded active total'
    return dict(id=source + ':' + identifier, case=identifier, source=source, design=design,
                b=b, rf=rf, k=int(k), m=int(m), wb=int(wb), in_bits=int(in_bits),
                hw=total, hw_stages=stages, status=status, reason=reason,
                parameters=parameters(k, m, wb, b, rf, in_bits, physical) if b and rf else None,
                **extra)


def hw_cycle_line(text):
    match = re.search(r'^HW_CYCLES (.+)$', text, re.M)
    if not match:
        return None, {}
    fields = {k: int(v) for k, v in re.findall(r'(\w+)=(\d+)', match[1])}
    names = dict(load_x='load_x', generate='generate_brams', load_w='load_w',
                 select='select_accumulate', store_o='store_o', loadw_select='load_w_and_select')
    return fields.get('total'), {k: fields[v] for k, v in names.items() if v in fields}


def regression(path, source):
    rows = []
    names = dict(load_x='hw_load_x_cycles', generate='hw_generate_brams_cycles',
                 load_w='hw_load_w_cycles', select='hw_select_accumulate_cycles',
                 store_o='hw_store_o_cycles', loadw_select='hw_load_w_and_select_cycles')
    with path.open() as f:
        for index, row in enumerate(csv.DictReader(f), 1):
            total = int(row['hw_cycles']) if row.get('hw_cycles', '').isdigit() else None
            stages = {key: int(row[col]) for key, col in names.items() if row.get(col, '').isdigit()}
            rows.append(record(source, 'regression_' + str(index), f'b{row["b"]}_rf{row["RF"]}',
                               row['CIN'], row['COUT'], row['W_BITS'], total, stages,
                               row['status'], in_bits=row['IN_BITS']))
    return rows


def test_cases(path, source):
    text = path.read_text(errors='replace')
    physical = physical_parameters(text)
    rows = []
    tc = path.stem.removeprefix('Log_')
    if tc in ('TC01', 'TC02'):
        blocks = list(re.finditer(r'^TC\d+ RUN (\d+)/(\d+): (b\d+_rf\d+)\s*$', text, re.M))
        for i, h in enumerate(blocks):
            block = text[h.end():blocks[i+1].start() if i+1 < len(blocks) else len(text)]
            fields = {k: int(v) for k, v in re.findall(r'\b(RIN|CIN|COUT|IN_BITS|W_BITS|XS|YS|RF)=(\d+)', block)}
            if not all(key in fields for key in ('CIN', 'COUT', 'W_BITS')):
                continue
            total, stages = hw_cycle_line(block)
            status = re.search(r'^FINAL RUN RESULT: (\w+)', block, re.M)
            rows.append(record(source, f'{tc}_{int(h[1]):04d}', h[3], fields['CIN'], fields['COUT'],
                               fields['W_BITS'], total, stages, status[1] if status else 'INCOMPLETE',
                               physical=physical, in_bits=fields.get('IN_BITS', 16)))
        # Recovered TC01 retains 54 correctness results, but only one timing block.
        known = {r['case'] for r in rows}
        for h in re.finditer(r'^RUN (\d+)/\d+ (b\d+_rf\d+) X1x(\d+) W\d+x(\d+) A16 W([842]): (\w+)', text, re.M):
            identifier = f'{tc}_{int(h[1]):04d}'
            if identifier not in known:
                rows.append(record(source, identifier, h[2], h[3], h[4], h[5], None,
                                   status=h[6], reason='Original detailed timing block is unavailable', physical=physical))
                known.add(identifier)
        return rows
    headers = list(re.finditer(r'^(TC\d+_\d+) (b\d+_rf\d+) input16 W([842]) pattern=', text, re.M))
    for i, h in enumerate(headers):
        block = text[h.end():headers[i+1].start() if i+1 < len(headers) else len(text)]
        status_match = re.search(r'^FINAL RESULT: (\w+)', block, re.M)
        status = status_match[1] if status_match else 'INCOMPLETE'
        steps = list(re.finditer(r'^STEP (\d+): X=1x(\d+), W=\d+x(\d+);', block, re.M))
        totals = {int(s): int(v) for s, v in re.findall(r'^STEP (\d+) MATCHES .* HW_CYCLES (\d+)', block, re.M)}
        for step in steps:
            n = int(step[1])
            reason = 'TC11 counter selector is misaligned after step 1; timing is unreliable' if tc == 'TC11' and n > 1 else ''
            rows.append(record(source, h[1] + f'/step{n}', h[2], step[2], step[3], h[3],
                               totals.get(n), status=status, reason=reason, physical=physical))
    return rows


def legacy_shapes(path, source, kind):
    text = path.read_text(errors='replace')
    design_match = re.search(r'^DESIGN[:=]\s*(b\d+_rf\d+)', text, re.M)
    if not design_match:
        return []
    design = design_match[1]
    physical = physical_parameters(text)
    rows, native_case, native_wb, geometry, completed = [], None, None, None, set()
    for line in text.splitlines():
        if kind == 'matrix_1024':
            shape = re.search(r'X=1x(\d+); W=\d+x(\d+)', line)
            if shape:
                geometry = tuple(map(int, shape.groups()))
            head = re.match(r'CASE W([842]):', line)
            if head:
                native_case, native_wb = 'MAT1024', int(head[1])
        else:
            head = re.match(r'TILE (LQ\d+) W([842]) #(\d+) X=1x(\d+) W=\d+x(\d+);', line)
            if head:
                native_case, native_wb = head[1] + '/tile' + head[3], int(head[2])
                geometry = int(head[4]), int(head[5])
        result = re.match(r'TILE RESULT .*?: (\{.*\})$', line)
        if result and native_case and geometry:
            data = json.loads(result[1])
            identifier = f'{native_case}/W{native_wb}'
            rows.append(record(source, identifier, design, *geometry, native_wb, data.get('cycles'),
                               pc_stages(data.get('pc')), data.get('status', 'INCOMPLETE'), physical=physical))
            completed.add(identifier)
    # Surface launched-but-unfinished cases, rather than silently dropping them.
    if native_case and geometry and f'{native_case}/W{native_wb}' not in completed:
        rows.append(record(source, f'{native_case}/W{native_wb}', design, *geometry, native_wb, None,
                           status='INCOMPLETE', physical=physical))
    return rows


def gui_cases(path, source):
    data = json.loads(path.read_text())
    suite = data['suite']
    options = suite['options']
    rows = []
    for index, case in enumerate(data.get('results', []), 1):
        designs = [d for d in suite['designs'] if d.get('id') == case.get('design_id')] if case.get('design_id') else [
            d for d in suite['designs'] if d['name'] == case['design']]
        if len(designs) != 1:
            raise ValueError('Cannot determine a unique saved design for ' + case['design'])
        design = designs[0]
        operations = case.get('operation_results', [])
        # Older custom jobs stored only ordered operations. Recover the exact grid
        # from their saved suite/design, never from today's Config.scala.
        import gui_core
        ranges = gui_core.tile_ranges(case['k'], case['m'], design, options)
        for tile, op in enumerate(operations):
            if tile >= len(ranges):
                raise ValueError('Saved operation count exceeds the saved tile grid')
            _, k, _, m = ranges[tile]
            params = op.get('model_parameters')
            k, m = op.get('k', k), op.get('m', m)
            row = record(source, f'{index}/{case["name"]}/W{case["wb"]}/tile{tile+1}',
                         case['design'], k, m, case['wb'], op.get('cycles'), pc_stages(op.get('pc')),
                         op.get('status', 'INCOMPLETE'), b=design['b'], rf=design['rf'],
                         physical=data.get('cycle_parameters'))
            if case.get('activation', design.get('activation', 'int16')) == 'fp16':
                row['reason'] = 'FP16 measurements are not covered by the current INT16 Python cycle model'
            if params:
                row['parameters'] = params
            row['logical_case'] = case['name']
            if suite['kind'] == 'llama_fast' and case.get('tiles_needed', 1) > 1:
                row['note'] = f'Measured sample only; full-shape {case["tiles_needed"]}-tile estimate is excluded from accuracy'
            rows.append(row)
    return rows


def cases(logs, selected):
    if not isinstance(selected, list) or not 1 <= len(selected) <= 50 or any(not isinstance(s, str) for s in selected):
        raise ValueError('Select 1–50 saved runs')
    available = {s['id']: s for s in sources(logs)}
    rows = []
    for source in dict.fromkeys(selected):
        if source not in available:
            raise ValueError('Unknown saved run: ' + source)
        path = Path(logs) / source
        kind = available[source]['kind']
        if kind == 'regression': items = regression(path, source)
        elif kind == 'test_cases': items = test_cases(path, source)
        elif kind == 'gui': items = gui_cases(path, source)
        else: items = legacy_shapes(path, source, kind)
        rows.extend(items)
    if len({r['id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate native operation identifiers in saved logs')
    return rows


def stats(pairs):
    if not pairs:
        return dict(n=0, hw=None, predicted=None, mae=None, mape=None,
                    min_absolute_error=None, max_absolute_error=None, min_ape=None, max_ape=None,
                    wape=None, within_10_pct=None)
    errors = [abs(p-h) for h, p in pairs]
    percentages = [100*abs(p-h)/h for h, p in pairs]
    total = sum(h for h, _ in pairs)
    return dict(n=len(pairs), hw=total, predicted=sum(p for _, p in pairs),
                mae=sum(errors)/len(errors), mape=sum(percentages)/len(percentages),
                min_absolute_error=min(errors), max_absolute_error=max(errors),
                min_ape=min(percentages), max_ape=max(percentages),
                wape=100*sum(errors)/total,
                within_10_pct=100*sum(p <= 10 for p in percentages)/len(pairs))


def compare(logs, request):
    rows = cases(logs, request.get('sources'))
    selected = request.get('cases')
    if selected is not None:
        if not isinstance(selected, list) or any(not isinstance(s, str) for s in selected):
            raise ValueError('Test selection must be a list of recorded operation IDs')
        wanted = set(selected)
        if not wanted or not wanted.issubset({r['id'] for r in rows}):
            raise ValueError('Select at least one existing test')
        rows = [r for r in rows if r['id'] in wanted]
    function, info = load_model()
    include_stages = request.get('stages', False)
    if not isinstance(include_stages, bool):
        raise ValueError('Stages must be true or false')
    memo, results, excluded = {}, [], []
    stage_pairs = collections.defaultdict(list)
    for row in rows:
        if row['reason']:
            excluded.append(dict(id=row['id'], case=row['case'], reason=row['reason']))
            continue
        key = json.dumps(row['parameters'], sort_keys=True)
        try:
            if key not in memo:
                memo[key] = function(**row['parameters'])
            output = memo[key]
            predicted = output['total_cycles']
            if isinstance(predicted, bool) or not isinstance(predicted, (int, float)) or not math.isfinite(predicted) or predicted < 0:
                raise ValueError('Model returned an invalid total_cycles')
        except Exception as error:
            raise ValueError(f'Model.py failed for {row["case"]}: {error}') from error
        item = dict(row, predicted=predicted, absolute_error=abs(predicted-row['hw']),
                    signed_error=predicted-row['hw'], ape=100*abs(predicted-row['hw'])/row['hw'])
        stage_pairs['total'].append((row['hw'], predicted))
        if include_stages:
            item['stages'] = {}
            for stage, (_, model_key) in STAGES.items():
                if stage == 'total': continue
                hw = row['hw_stages'].get(stage)
                model = output.get(model_key)
                valid = (isinstance(model, (int, float)) and not isinstance(model, bool)
                         and math.isfinite(model) and model >= 0)
                # Never substitute a modeled stage for an unavailable HW counter.
                item['stages'][stage] = dict(hw=hw, predicted=model if valid else None,
                    absolute_error=abs(model-hw) if valid and hw is not None else None,
                    ape=100*abs(model-hw)/hw if valid and hw is not None and hw > 0 else None)
                if valid and hw is not None and hw > 0:
                    stage_pairs[stage].append((hw, model))
        results.append(item)
    groups = []
    for design, wb in sorted({(r['design'], r['wb']) for r in results}):
        pairs = [(r['hw'], r['predicted']) for r in results if r['design']==design and r['wb']==wb]
        groups.append(dict(design=design, wb=wb, **stats(pairs)))
    return dict(model=info, summary=stats(stage_pairs['total']), groups=groups,
                stages=[dict(id=k, name=v[0], **stats(stage_pairs[k])) for k, v in STAGES.items()
                        if k != 'total'] if include_stages else [],
                results=results, excluded=excluded,
                notes=['Accuracy = percentage of measured operations within ±10% of HW cycles; lower MAPE/MAE is better.',
                       'Tiles are compared at their actual native dimensions. Totals sum only selected measured operations.',
                       'Load W alone and Select alone overlap the combined phase; do not add them to total cycles.',
                       'Historical logs without a physical snapshot use the original runner contract: YS=1, DMA IDs=2, DMA=64 bits, IN_MAX=16, W_MAX=8, HS=8.'])
