"""FP16 operand reference and FP32 output comparisons for GUI verification."""
import math
import struct

import run_fp16 as model


def value(word):
    return struct.unpack('<f', struct.pack('<I', word))[0]


def display(word):
    number = value(word)
    return number if math.isfinite(number) else 'NaN' if math.isnan(number) else '+Infinity' if number > 0 else '-Infinity'


def sum_words(words):
    """Exact sum of rounded FP32 HW partials, then one FP32 rounding.

    Units are 2^-149, so every finite FP32 partial is an integer. This avoids
    host float64 double rounding. A single native result keeps its exact bits.
    """
    if len(words) == 1:
        return words[0]
    total, infinities = 0, set()
    for word in words:
        exponent, fraction = (word >> 23) & 255, word & 0x7fffff
        if exponent == 255:
            if fraction:
                return 0x7fc00000
            infinities.add(bool(word >> 31))
        else:
            magnitude = ((0x800000 | fraction) << (exponent - 1)) if exponent else fraction
            total += -magnitude if word >> 31 else magnitude
    if len(infinities) == 2:
        return 0x7fc00000
    if infinities:
        return 0xff800000 if True in infinities else 0x7f800000
    if not total:
        return 0
    sign, magnitude = (0x80000000 if total < 0 else 0), abs(total)
    top = magnitude.bit_length() - 1
    if top < 23:
        return sign | magnitude  # Exact subnormal.
    shift = top - 23
    quotient, remainder = divmod(magnitude, 1 << shift)
    quotient += remainder * 2 > 1 << shift or (remainder * 2 == 1 << shift and quotient & 1)
    exponent = top - 149
    if quotient == 1 << 24:
        quotient >>= 1
        exponent += 1
    return sign | (0x7f800000 if exponent > 127 else ((exponent + 127) << 23) | (quotient & 0x7fffff))


def compare(actual, expected, options):
    """Never change thresholds or a saved result to make it pass."""
    atol, rtol = options.get('atol', 0.0), options.get('rtol', 0.0)
    mode = options.get('fp_comparison', 'exact')
    pairs, bad_special, exact, close, mismatches = [], 0, 0, 0, []
    for index, (hw, ref) in enumerate(zip(actual, expected)):
        same = hw == ref
        exact += same
        a, b = value(hw), value(ref)
        if math.isfinite(a) and math.isfinite(b):
            error, scale = abs(a - b), abs(b)
            accepted = error <= atol + rtol * scale
            pairs.append((error, scale))
        else:
            accepted = same  # Equal canonical NaNs / same-signed infinity.
            bad_special += not same
        close += accepted
        if not (same if mode == 'exact' else accepted) and len(mismatches) < 16:
            mismatches.append(dict(index=index, hw=display(hw), reference=display(ref),
                                   hw_bits=f'0x{hw:08x}', reference_bits=f'0x{ref:08x}'))
    count = len(expected)
    complete = len(actual) == count
    required_atol = max((max(0.0, error - rtol * scale) for error, scale in pairs), default=0.0)
    relative_impossible = any(scale == 0 and error > atol for error, scale in pairs)
    required_rtol = max((max(0.0, error - atol) / scale for error, scale in pairs if scale), default=0.0)
    # Round suggestions upward when necessary so the displayed, round-trippable
    # number really satisfies this same finite comparison for every output.
    while any(error > required_atol + rtol * scale for error, scale in pairs):
        required_atol = math.nextafter(required_atol, math.inf)
    if not relative_impossible:
        while any(error > atol + required_rtol * scale for error, scale in pairs):
            required_rtol = math.nextafter(required_rtol, math.inf)
    unbounded = any(scale == 0 and error != 0 for error, scale in pairs)
    max_relative = max((error / scale for error, scale in pairs if scale), default=0.0)
    stats = dict(mode=mode, atol=atol, rtol=rtol, exact_matches=exact, tolerance_matches=close,
                 outputs_complete=complete,
                 max_absolute_error=max((error for error, _ in pairs), default=0.0) if complete and not bad_special else None,
                 max_relative_error=max_relative if complete and not unbounded and not bad_special else None,
                 relative_error_unbounded=unbounded, nonfinite_mismatches=bad_special,
                 required_atol=required_atol if complete and not bad_special else None,
                 required_rtol=required_rtol if complete and not bad_special and not relative_impossible else None,
                 tolerance_pass=complete and close == count,
                 note='Required atol holds rtol fixed; required rtol holds atol fixed. These apply to numeric comparison only.')
    matches = exact if mode == 'exact' else close
    return dict(matches=matches, exact_matches=exact, comparison=stats, mismatches=mismatches,
                numeric_pass=complete and matches == count)
