"""Frozen predicates from the deployed Go 1.26.8 Unicode 15.0.0 tables."""
from bisect import bisect_right
import hashlib
import json
from pathlib import Path

_raw = Path(__file__).with_name('go_unicode15.json').read_bytes()
if hashlib.sha256(_raw).hexdigest() != '427e7edf5ba5a7d98a6df25674a3e2817fb26832d8582f62e7927a1beff4a475':
    raise ValueError('PINNED_UNICODE_TABLE_CHANGED')
_data = json.loads(_raw)
if _data['unicodeVersion'] != '15.0.0':
    raise ValueError('PINNED_UNICODE_VERSION_CHANGED')
_ranges = _data['tables']
_starts = {name: [row[0] for row in rows] for name, rows in _ranges.items()}


def contains(name, character):
    number = ord(character)
    if 0xd800 <= number <= 0xdfff:
        raise ValueError('INVALID_UNICODE_SCALAR')
    index = bisect_right(_starts[name], number)-1
    if index < 0:
        return False
    start, end, stride = _ranges[name][index]
    return number <= end and (number-start) % stride == 0


def trim_space(value):
    left, right = 0, len(value)
    while left < right and contains('White_Space', value[left]):
        left += 1
    while right > left and contains('White_Space', value[right-1]):
        right -= 1
    return value[left:right]
