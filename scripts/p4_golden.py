#!/usr/bin/env python3
"""P4 golden 结构快照: analyze('BTCUSDT') → {key: type} 存档"""
# [2026-10-05 P1 苏摩111] 统一路径管理，替代裸 sys.path.insert
try:
    import brahma_path_setup  # noqa
except ImportError:
    pass  # 兜底：原有 sys.path.insert 仍保留

import sys, json
from pathlib import Path
sys.path.insert(0, str(Path('/root/.openclaw/workspace/trading-system/brahma_brain')))
import brahma_core as bc

out_path = sys.argv[1]
r = bc.analyze('BTCUSDT')
out = {}
for k in r:
    out[k] = type(r[k]).__name__
json.dump(out, open(out_path, 'w'), ensure_ascii=False, indent=0, sort_keys=True)
print('GOLDEN_KEYS', len(out))
print('EXIT=0')
