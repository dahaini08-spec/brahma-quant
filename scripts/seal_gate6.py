#!/usr/bin/env python3
"""
seal_gate6.py — 封印第六道门控：接线验证
══════════════════════════════════════════
设计院 2026-08-25 苏摩111 封印

调用方式：
  python3 scripts/seal_gate6.py

通过条件（全部满足才允许封印）：
  1. 所有核心模块 import OK（fangcang / cross_asset / kronos）
  2. brahma_core.analyze() 返回 price_ts（非 None）
  3. price_ts age < 30s（刚取的实时数据）

任何一条不通过 → 非零退出码，封印流程中断。
"""
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'brahma_brain'))

print('🔍 封印第六道门控：接线验证')
print('=' * 45)

# 门1：接线巡检
from brahma_wiring_v2 import run_check
result = run_check(push_on_error=False)
print(result['report'])

if not result['ok']:
    print('\n❌ 门控6不通过：存在断线模块，禁止封印！')
    print('修复方法：从archive恢复对应文件后重新运行')
    sys.exit(1)

# 门2：price_ts 验证
print('\n🔍 验证 price_ts 写入...')
import brahma_core
r = brahma_core.analyze('BTCUSDT')
ts = r.get('price_ts')
if ts is None:
    print('❌ price_ts 缺失，brahma_core.py 未正确写入时间戳！')
    sys.exit(1)
age = time.time() - ts
if age > 30:
    print(f'❌ price_ts 过旧 {age:.0f}s，分析结果可能使用了缓存旧数据！')
    sys.exit(1)
print(f'✅ price_ts 验证通过，数据新鲜度 {age:.1f}s')

print('\n✅ 封印第六道门控通过，允许封印！')
sys.exit(0)
