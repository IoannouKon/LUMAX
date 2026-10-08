#!/usr/bin/env python3
import argparse
import csv
from functools import lru_cache
import hashlib
import importlib.util
import json
from pathlib import Path
import re


def predict(inputs, weights, columns, outputs, lanes, factor, precision,
            wrap_address=True, wrap_sum=True):
    chunk_size = min(columns, lanes * factor * (1 << (8 - precision)))
    blocks = (chunk_size + lanes - 1) // lanes

    @lru_cache(maxsize=None)
    def column_result(base):
        if not wrap_sum:
            return sum(value * weights[base + index] for index, value in enumerate(inputs))
        total = 0
        for start in range(0, columns, chunk_size):
            for block in range(blocks):
                subtotal = sum(
                    inputs[index] * weights[base + index]
                    for lane in range(lanes)
                    for index in [start + lane * blocks + block]
                    if index < min(start + chunk_size, columns)
                )
                total += ((subtotal + (1 << 24)) % (1 << 25)) - (1 << 24)
        return total

    return [column_result((column * columns) & 65535 if wrap_address else column * columns)
            for column in range(outputs)]


def main():
    parser = argparse.ArgumentParser(description='Reproduce saved 512x512 failures from the RTL address and reduction widths.')
    parser.add_argument('--sweep', type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    sweep = args.sweep.resolve()
    script = sweep.parent.parent
    chipyard = script.parents[4]
    manifest = json.loads((sweep / 'manifest.json').read_text())
    for relative in [
        'generators/LUMAX/src/main/scala/LUMAX_Top.scala',
        'generators/LUMAX/src/main/scala/ChunkInfoModules.scala',
        'generators/LUMAX/software/tests/src/prepare_test_vectors.py',
    ]:
        actual = hashlib.sha256((chipyard / relative).read_bytes()).hexdigest()
        if actual != manifest['sources'][relative]:
            raise SystemExit(f'Source differs from the saved sweep: {relative}')
    spec = importlib.util.spec_from_file_location('vectors', script / 'prepare_test_vectors.py')
    vectors = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vectors)
    with (sweep / 'summary.csv').open() as stream:
        cases = [row for row in csv.DictReader(stream) if row['status'] == 'FAIL']
    prepared = {}
    evidence = []
    for case in cases:
        rows, columns, outputs, activation_bits, precision = (
            int(case[key]) for key in ['RIN', 'CIN', 'COUT', 'IN_BITS', 'W_BITS'])
        if (rows, columns, outputs, activation_bits) != (1, 512, 512, 16):
            raise SystemExit('This reproducer is scoped to the saved 1x512 times 512x512 failures.')
        if precision not in prepared:
            inputs, packed, reference = vectors.prepare(
                rows, columns, outputs, activation_bits, precision,
                int(manifest['flags']['TEST_SEED']), int(manifest['flags']['TEST_PATTERN']))
            per_byte = 8 // precision
            mask = (1 << precision) - 1
            weights = []
            for index in range(columns * outputs):
                value = (packed[index // per_byte] >> ((index % per_byte) * precision)) & mask
                weights.append(value - (1 << precision) if value & (1 << (precision - 1)) else value)
            prepared[precision] = inputs, weights, reference
        inputs, weights, reference = prepared[precision]
        lanes, factor = int(case['b']), int(case['RF'])
        predictions = {
            'current_rtl_model': predict(inputs, weights, columns, outputs, lanes, factor, precision),
            'wide_address_only': predict(inputs, weights, columns, outputs, lanes, factor, precision, wrap_address=False),
            'wide_sum_only': predict(inputs, weights, columns, outputs, lanes, factor, precision, wrap_sum=False),
            'both_widened': predict(inputs, weights, columns, outputs, lanes, factor, precision, wrap_address=False, wrap_sum=False),
        }
        samples = re.findall(r'Mismatch \[0,(\d+)\]: HW=(-?\d+) SW=(-?\d+)', Path(case['log']).read_text())
        assert samples, case['log']
        for column, hardware, expected in samples:
            assert predictions['current_rtl_model'][int(column)] == int(hardware), case
            assert reference[int(column)] == int(expected), case
        row = {'b': lanes, 'RF': factor, 'W_BITS': precision,
               'observed_matches': case['exact_matches'], 'logged_mismatches_reproduced': len(samples)}
        for name, values in predictions.items():
            row[name] = f'{sum(actual == expected for actual, expected in zip(values, reference))}/{outputs}'
        assert row['current_rtl_model'] == case['exact_matches'], row
        assert row['both_widened'] == f'{outputs}/{outputs}', row
        evidence.append(row)
        print(row, flush=True)
    if not evidence:
        raise SystemExit('No failed cases found.')
    destination = Path(__file__).resolve().with_name('evidence.csv')
    with destination.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(evidence[0]))
        writer.writeheader()
        writer.writerows(evidence)
    print(f'Confirmed {len(evidence)} failure counts and {sum(row["logged_mismatches_reproduced"] for row in evidence)} saved mismatch values. Evidence: {destination}')


if __name__ == '__main__':
    main()
