#!/usr/bin/env python3
"""
dharma/blueprint_writer.py — 达摩院结论自动写回 Blueprint  (D9修复)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
职责：
  将 dharma_flywheel 的参数建议写回 FANTAN_BLUEPRINT_V3.json 的三个位置：
    1. _runtime_config        — 可执行参数（hunter_config.py 读取）
    2. DHARMA_VALIDATED_CONCLUSIONS — 人类可读结论归档
    3. BLUEPRINT_DOMAINS 域健康分   — D9/D10 进度更新

触发方式：
  - dharma_flywheel.check_and_trigger() 结束时自动调用
  - python3 dharma/blueprint_writer.py --dry-run   # 预览不写入
  - python3 dharma/blueprint_writer.py             # 实际写入

Blueprint v3.10 · 2026-05-18
"""

import json, sys, os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any

ROOT       = Path(__file__).parent.parent
BP_FILE    = ROOT / "FANTAN_BLUEPRINT_V3.json"
DATA_DIR   = ROOT / "data"
WRITE_LOG  = DATA_DIR / "blueprint_sync_log.json"


# ─── 建议 → Blueprint 映射规则 ───────────────────────────────────

# 每条建议的 suggestion_type → (runtime_config路径, 转换函数)
_PARAM_MAP = {
    # Kelly 调整
    'kelly_increase':  ('kelly.base_pct',    lambda v: round(float(v), 4)),
    'kelly_decrease':  ('kelly.base_pct',    lambda v: round(float(v), 4)),
    'kelly_adjust':    ('kelly.base_pct',    lambda v: round(float(v), 4)),
    # 评分门槛
    'score_raise':     ('score_min',         lambda v: int(v)),
    'score_lower':     ('score_min',         lambda v: int(v)),
    # 持仓时间
    'time_stop_shorten': ('time_stop_hours', lambda v: int(v)),
    'time_stop_extend':  ('time_stop_hours', lambda v: int(v)),
    # TP倍数
    'tp1_adjust':      ('tp_mult.tp1',       lambda v: round(float(v), 2)),
    'tp2_adjust':      ('tp_mult.tp2',       lambda v: round(float(v), 2)),
    # 黑名单
    'blacklist_add':   ('blacklist',         lambda v: v),   # v = list
    'avoid_add':       ('avoid_list',        lambda v: v),   # v = list
}


# ─── 核心写回函数 ─────────────────────────────────────────────────

def write_conclusions_to_blueprint(
    suggestions: List[Dict],
    regime_stats: Dict,
    n_trades: int,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    将达摩院飞轮建议写回 Blueprint。
    suggestions: generate_suggestions() 的输出
    regime_stats: calc_regime_stats() 的输出
    n_trades: 本轮触发样本数
    dry_run: True只预览不写文件

    返回 {'written': int, 'skipped': int, 'changes': list}
    """
    if not BP_FILE.exists():
        return {'written': 0, 'skipped': 0, 'changes': [], 'error': 'Blueprint文件不存在'}

    bp = json.loads(BP_FILE.read_text(encoding='utf-8'))
    rc = bp.setdefault('_runtime_config', {})
    dvc = bp.setdefault('DHARMA_VALIDATED_CONCLUSIONS', {})
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ')

    written = 0
    skipped = 0
    changes = []

    for sug in suggestions:
        stype = sug.get('type', sug.get('suggestion_type', ''))
        if stype not in _PARAM_MAP:
            skipped += 1
            continue

        path_str, transform = _PARAM_MAP[stype]
        new_val = sug.get('new_value', sug.get('value'))
        if new_val is None:
            skipped += 1
            continue

        try:
            new_val = transform(new_val)
        except Exception:
            skipped += 1
            continue

        # 设置嵌套路径
        old_val = _set_nested(rc, path_str, new_val)
        changes.append({
            'path':    f'_runtime_config.{path_str}',
            'old':     old_val,
            'new':     new_val,
            'reason':  sug.get('reason', sug.get('desc', '')),
            'stype':   stype,
        })
        written += 1

    # 更新 _runtime_config 元数据
    rc['_updated'] = now_str
    rc['_last_flywheel'] = now_str
    rc['_flywheel_n']    = n_trades

    # 归档 DHARMA_VALIDATED_CONCLUSIONS
    flywheel_key = f'flywheel_{now_str[:10].replace("-","")}'
    dvc[flywheel_key] = {
        'date':        now_str,
        'n_trades':    n_trades,
        'suggestions': len(suggestions),
        'written':     written,
        'changes':     changes,
        'regime_summary': {k: {
            'n': v.get('n', 0),
            'wr': round(v.get('win_rate', 0), 3),
            'pf': round(v.get('profit_factor', 0), 2),
        } for k, v in (regime_stats or {}).items()},
        'status': 'AUTO_WRITTEN',
    }
    dvc['_date'] = now_str

    # 更新 D9 域健康
    domains = bp.get('BLUEPRINT_DOMAINS', {})
    if 'D9_dharma_learning' in domains and isinstance(domains['D9_dharma_learning'], dict):
        domains['D9_dharma_learning']['health'] = '82/100'
        domains['D9_dharma_learning']['status'] = 'ACTIVE'
        domains['D9_dharma_learning']['gap'] = f'结论自动回写已建立，最近回写: {now_str}'

    if not dry_run:
        BP_FILE.write_text(json.dumps(bp, ensure_ascii=False, indent=2))
        _append_log({'ts': now_str, 'written': written, 'skipped': skipped, 'changes': changes})
        print(f"[BlueprintWriter] ✅ 写回完成: {written}项参数  {skipped}项跳过")
    else:
        print(f"[BlueprintWriter] 🔍 [DRY-RUN] 预览: {written}项参数变更  {skipped}项跳过")
        for c in changes:
            print(f"  {c['path']}: {c['old']} → {c['new']}  ({c['reason'][:50]})")

    return {'written': written, 'skipped': skipped, 'changes': changes}


def _set_nested(d: dict, path: str, value) -> Any:
    """
    设置嵌套dict路径，如 'kelly.base_pct'。
    特殊处理 list 字段：blacklist/avoid_list 是追加而非替换。
    返回旧值。
    """
    keys = path.split('.')
    obj = d
    for k in keys[:-1]:
        obj = obj.setdefault(k, {})

    last_key = keys[-1]
    old_val = obj.get(last_key)

    # list 字段：追加去重
    if last_key in ('blacklist', 'avoid_list'):
        existing = list(obj.get(last_key, []))
        additions = [v.upper() for v in (value if isinstance(value, list) else [value])]
        new_list = list(set(existing + additions))
        obj[last_key] = new_list
    else:
        obj[last_key] = value

    return old_val


def _append_log(entry: dict):
    """追加写入 blueprint_sync_log.json"""
    try:
        log = []
        if WRITE_LOG.exists():
            try:
                log = json.loads(WRITE_LOG.read_text())
            except Exception:
                log = []
        log.append(entry)
        WRITE_LOG.write_text(json.dumps(log[-50:], ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"[BlueprintWriter] ⚠️ 日志写入失败: {e}")


# ─── 快速回写接口（供 dharma_flywheel 调用）──────────────────────

def write_from_flywheel(
    suggestions: List[Dict],
    regime_stats: Dict = None,
    n_trades: int = 0,
) -> Dict:
    """
    dharma_flywheel.check_and_trigger() 的便捷入口。
    示例：
        from dharma.blueprint_writer import write_from_flywheel
        write_from_flywheel(suggestions, regime_stats, len(new_trades))
    """
    return write_conclusions_to_blueprint(
        suggestions=suggestions,
        regime_stats=regime_stats or {},
        n_trades=n_trades,
        dry_run=False,
    )


# ─── 查看写回历史 ─────────────────────────────────────────────────

def show_history(n: int = 5):
    """打印最近N次写回记录"""
    try:
        log = json.loads(WRITE_LOG.read_text())
        print(f"\n=== Blueprint写回历史 (最近{n}次) ===")
        for entry in log[-n:]:
            print(f"  {entry['ts'][:16]}  写入{entry['written']}项  跳过{entry['skipped']}项")
            for c in entry.get('changes', []):
                print(f"    {c['path']}: {c['old']} → {c['new']}")
    except Exception:
        print("  无历史记录")


# ─── 入口 ────────────────────────────────────────────────────────

if __name__ == '__main__':
    dry = '--dry-run' in sys.argv or '--dry' in sys.argv

    if '--history' in sys.argv:
        show_history()
        sys.exit(0)

    # 构造测试建议，演示写回流程
    test_suggestions = [
        {'type': 'kelly_adjust',   'new_value': 0.055, 'reason': 'BULL_TREND胜率60%，略提Kelly'},
        {'type': 'score_raise',    'new_value': 9,     'reason': '低分段WR<30%，建议提高门槛'},
        {'type': 'avoid_add',      'new_value': ['CHZUSDT'], 'reason': 'CHOP_HIGH+CHZ  连亏3次'},
    ]
    test_regime = {
        'BULL_TREND': {'n': 12, 'win_rate': 0.60, 'profit_factor': 2.1},
        'BEAR_CRASH': {'n': 8,  'win_rate': 0.38, 'profit_factor': 0.9},
    }

    result = write_conclusions_to_blueprint(test_suggestions, test_regime, 20, dry_run=dry)
    print(f"\n结果: {result}")
    if not dry:
        show_history(3)
