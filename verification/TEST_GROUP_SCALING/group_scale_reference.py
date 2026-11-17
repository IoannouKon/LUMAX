#!/usr/bin/env python3
"""Exact finite-FP16 x signed-INTx, per-K-group FP16 scaling reference.

Input : x[R][K] raw IEEE-754 half bits, q[N][K] signed integers,
        scales[ceil(K/G)][N] raw IEEE-754 half bits.
Output: raw FP32 bits with ONE final ties-to-even rounding.

This is a mathematical architecture reference for a proposed LUMIXED mode,
NOT a model of the existing RTL or a PyTorch kernel's intermediate rounding.
NaN and infinity support are deliberately excluded from this first milestone.
"""
from __future__ import annotations
from math import ceil
import struct


def fp16_from_float(value: float) -> int:
    return struct.unpack('<H', struct.pack('<e', value))[0]


def fp16_fixed24(bits: int) -> int:
    """Exact finite binary16 value as a signed integer measured in 2**-24."""
    exp = (bits >> 10) & 31
    frac = bits & 0x3ff
    if exp == 31:
        raise ValueError('NaN/Infinity are a separate hardware milestone')
    sig = frac if exp == 0 else 1024 + frac
    shift = 0 if exp == 0 else exp - 1
    fixed = sig << shift
    return -fixed if bits & 0x8000 else fixed


def fixed_to_fp32_bits(value: int, fractional_bits: int) -> int:
    """Round exact value * 2**(-fractional_bits) to FP32, nearest/ties-to-even.

    This reference handles normal FP32 results and zero, sufficient for
    finite FP16 * signed INT8 * finite FP16 with practical matrix sizes.
    """
    if value == 0:
        return 0
    sign = (1 << 31) if value < 0 else 0
    magnitude = abs(value)
    top = magnitude.bit_length() - 1
    if top <= 23:
        significand = magnitude << (23 - top)
    else:
        drop = top - 23
        significand = magnitude >> drop
        remainder = magnitude & ((1 << drop) - 1)
        midpoint = 1 << (drop - 1)
        if remainder > midpoint or (remainder == midpoint and (significand & 1)):
            significand += 1
    exponent = top - fractional_bits + 127
    if significand == (1 << 24):
        significand >>= 1
        exponent += 1
    if not 1 <= exponent <= 254:
        raise ValueError('Reference needs additional IEEE overflow/subnormal handling')
    return sign | (exponent << 23) | (significand & 0x7fffff)


def reference(x: list[list[int]], q: list[list[int]],
              scales: list[list[int]], group_size: int) -> list[list[int]]:
    if group_size <= 0 or not x or not q or not x[0]:
        raise ValueError('Invalid tensor shape or group size')
    k = len(x[0]); n = len(q); groups = ceil(k / group_size)
    if any(len(row) != k for row in x) or any(len(col) != k for col in q):
        raise ValueError('Shape mismatch')
    if len(scales) != groups or any(len(group) != n for group in scales):
        raise ValueError('Scales must be [ceil(K/G)][N]')
    out = []
    for xr in x:
        row = []
        for j in range(n):
            exact_fixed48 = 0
            for g in range(groups):
                partial_fixed24 = sum(
                    fp16_fixed24(xr[kk]) * q[j][kk]
                    for kk in range(g * group_size, min((g + 1) * group_size, k))
                )
                exact_fixed48 += partial_fixed24 * fp16_fixed24(scales[g][j])
            row.append(fixed_to_fp32_bits(exact_fixed48, 48))
        out.append(row)
    return out


def fp32(bits: int) -> float:
    return struct.unpack('<f', struct.pack('<I', bits))[0]


def self_test() -> None:
    # One group and identity scale must reproduce a plain dot product.
    x = [[fp16_from_float(float(v)) for v in range(1, 9)]]
    q = [[1] * 8, [1, -1, 0, 0, 1, -1, 0, 0]]
    scales = [[fp16_from_float(0.5), fp16_from_float(1.0)],
              [fp16_from_float(0.25), fp16_from_float(2.0)]]
    result = reference(x, q, scales, 4)
    assert fp32(result[0][0]) == 11.5, fp32(result[0][0])
    assert fp32(result[0][1]) == -3.0, fp32(result[0][1])
    # Uneven final group, negative values, and per-column scales.
    x2 = [[fp16_from_float(v) for v in (1.0, -2.0, 3.0, -4.0, 5.0)]]
    q2 = [[1, -2, 3, -4, 5]]
    scales2 = [[fp16_from_float(0.5)], [fp16_from_float(-0.25)]]
    answer2 = reference(x2, q2, scales2, 4)
    assert fp32(answer2[0][0]) == 8.75, fp32(answer2[0][0])  # (1+4+9+16)*.5 + 25*(-.25)
    print('PASS: exact finite-FP16 group-scaling reference')
    print('Example row:', [fp32(v) for v in result[0]])


if __name__ == '__main__':
    self_test()
