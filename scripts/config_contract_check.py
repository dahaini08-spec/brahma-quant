#!/usr/bin/env python3
"""
config_contract_check.py — 配置契约验证
[9.19 设计院封印 苏摩111] 防止config类型与代码预期不一致

验证scoring_config.json中每个体制×方向的字段类型与dag_executor代码预期一致：
  - active_dims: 必须是 'all' (str) 或 list of str
  - sleep_dims: 必须是 list of str
  - position_mult: 必须是 float/int
  - weights: 必须是 dict

同时验证dag_executor能正确解析每种配置形式。

接入位置: supercronic启动后 / 手动运维检查
"""
import json, sys, os
from pathlib import Path

BASE = Path(__file__).parent.parent
CONFIG = BASE / 'data' / 'scoring_config.json'

EXPECTED_FIELDS = {
    'active_dims': (str, list),   # 'all' or ['s1','s2',...]
    'sleep_dims': (list,),         # ['s1','s2',...]
    'position_mult': (int, float), # 1.0
    'weights': (dict,),            # {}
}

REGIMES = ['BEAR_TREND', 'BEAR_EARLY', 'CHOP_MID', 'BULL_TREND', 'BEAR_RECOVERY', 'BULL_EARLY']
DIRECTIONS = ['LONG', 'SHORT']

def check():
    if not CONFIG.exists():
        print('❌ scoring_config.json不存在')
        return 1

    cfg = json.loads(CONFIG.read_text())
    errors = []
    warnings = []

    for regime in REGIMES:
        if regime not in cfg:
            errors.append(f'{regime}: 体制缺失')
            continue
        for direction in DIRECTIONS:
            key = f'{regime}:{direction}'
            entry = cfg[regime].get(direction)
            if not entry:
                errors.append(f'{key}: 方向缺失')
                continue

            # 类型检查
            for field, expected_types in EXPECTED_FIELDS.items():
                if field not in entry:
                    errors.append(f'{key}: 缺少字段 {field}')
                    continue
                val = entry[field]
                if not isinstance(val, expected_types):
                    errors.append(f'{key}: {field}类型={type(val).__name__} 期望={expected_types}')

            # 语义检查：V4设计：active_dims='all'+sleep_dims=非空 = 全激活后sleep逆势偏向维度
            # 只有active_dims是list且非空时，sleep_dims不能与之重叠
            _active = entry.get('active_dims')
            _sleep = entry.get('sleep_dims', [])
            if isinstance(_active, list) and _active and _sleep:
                overlap = set(_active) & set(_sleep)
                if overlap:
                    errors.append(f'{key}: active_dims和sleep_dims重叠 → {overlap}')

            # 语义检查：active_dims是list时不能是空list
            if isinstance(_active, list) and len(_active) == 0 and not _sleep:
                warnings.append(f'{key}: active_dims和sleep_dims都为空 → DAG不生效（可能预期）')

    # 验证dag_executor能正确解析
    try:
        sys.path.insert(0, str(BASE / 'brahma_brain'))
        from dag_executor import get_sparse_config
        for regime in REGIMES:
            for direction in DIRECTIONS:
                cfg_out = get_sparse_config(regime, direction)
                active = cfg_out.get('active_dims')
                sleep = cfg_out.get('sleep_dims')
                # 如果active是list且包含单字符 → 可能是字符串被拆
                if isinstance(active, list) and active and len(active[0]) == 1:
                    errors.append(f'{regime}:{direction} DAG返回active_dims含单字符={active} → 疑似字符串拆分bug')
    except Exception as e:
        errors.append(f'DAG executor验证失败: {e}')

    print(f'扫描 {len(REGIMES)}×{len(DIRECTIONS)}={len(REGIMES)*len(DIRECTIONS)} 个配置')
    if warnings:
        print(f'⚠️ {len(warnings)} 警告:')
        for w in warnings:
            print(f'  {w}')
    if errors:
        print(f'❌ {len(errors)} 错误:')
        for e in errors:
            print(f'  {e}')
        return 1
    else:
        print('✅ 配置契约验证通过')
        return 0

if __name__ == '__main__':
    sys.exit(check())
