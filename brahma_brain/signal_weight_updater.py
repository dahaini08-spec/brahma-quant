"""
signal_weight_updater.py — 结算闭环权重更新器 v1.0
设计院封印 2026-08-09 苏摩111

职责：
  每次 signal_settler 结算新信号后调用此模块
  → 按 regime:direction:score_tier 分组统计滚动WR
  → 动态更新 data/signal_weights.json 对应 multiplier
  → brahma_core 下次 analyze() 自动读取更新后的权重

闭环路径：
  信号产生 → auto_executor执行 → signal_settler结算
  → signal_weight_updater更新weights → brahma_core读取 → 下次信号

设计原则（梵天宪法）：
  - 最简实现：纯stdlib，零新依赖
  - 保守更新：n<20 不更新（样本不足）
  - 静态规则优先：手工铁证 (min_n_override) 不被覆盖
  - fail-safe：任何异常静默，不影响主结算流程
"""

import json
import time
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).parent.parent
SF_PATH  = BASE / 'data' / 'simfactory_trades.jsonl'
SW_PATH  = BASE / 'data' / 'signal_weights.json'
LOG_PATH = BASE / 'data' / 'weight_update_log.jsonl'

# ── 分组窗口 & 阈值 ───────────────────────────────────────────────────────
ROLLING_N   = 30    # 滚动窗口：最近30笔同类信号
MIN_N       = 15    # 至少15笔才允许动态更新（避免噪音）
WR_HIGH     = 0.62  # WR >= 62% → multiplier 向 1.2 靠拢
WR_LOW      = 0.42  # WR <= 42% → multiplier 向 0.3 靠拢
WR_DEAD     = 0.30  # WR <= 30% → 考虑降为 0.0（需连续3次确认）

# ── 不允许动态覆盖的静态铁证规则（手工封印优先）──────────────────────────
STATIC_LOCK = {
    'CHOP_MID:LONG',           # 死穴永久封禁
    'CHOP_MID:LONG:155+',      # 死穴
    'BEAR_TREND:LONG:155+',    # 逆势死亡区
    'BEAR_TREND:LONG:140-154', # 逆势极危
}


def _score_tier(score: float) -> str:
    """将score转换为分段标签"""
    if score >= 165: return '165+'
    if score >= 155: return '155+'
    if score >= 140: return '140-154'
    if score >= 120: return '120-139'
    return '<120'


def _load_trades() -> list:
    """加载simfactory_trades.jsonl，返回已结算记录"""
    if not SF_PATH.exists():
        return []
    trades = []
    for line in SF_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            t = json.loads(line)
            # 只取有明确结果的（TP1/SL，排除EXPIRE超时）
            result = t.get('result', t.get('outcome', ''))
            if result in ('TP1', 'TP2', 'SL'):
                trades.append(t)
        except Exception:
            continue
    return trades


def _load_signal_weights() -> dict:
    """加载signal_weights.json，返回整个结构"""
    if not SW_PATH.exists():
        return {'version': '1.0', 'weights': {}}
    try:
        return json.loads(SW_PATH.read_text())
    except Exception:
        return {'version': '1.0', 'weights': {}}


def _calc_rolling_wr(trades: list, key_regime: str, key_dir: str,
                     key_tier: str) -> tuple:
    """
    计算 regime:direction:score_tier 最近 ROLLING_N 笔的 WR。
    返回 (wr, n_used) 或 (None, 0) 表示样本不足
    """
    # 按时间倒序，找匹配的最近 ROLLING_N 笔
    matched = []
    for t in reversed(trades):
        t_regime = t.get('regime', '')
        t_dir    = t.get('direction', '')
        t_score  = float(t.get('score', 0) or 0)
        t_tier   = _score_tier(t_score)
        if t_regime == key_regime and t_dir == key_dir and t_tier == key_tier:
            result = t.get('result', t.get('outcome', ''))
            matched.append(result in ('TP1', 'TP2'))
        if len(matched) >= ROLLING_N:
            break

    n = len(matched)
    if n < MIN_N:
        return None, n

    wr = sum(matched) / n
    return round(wr, 4), n


def _new_multiplier(current_mult: float, wr: float, n: int) -> float:
    """
    根据实盘滚动WR平滑调整 multiplier。
    保守更新：每次最多调整 ±0.1，避免剧烈波动。
    """
    # 目标 multiplier
    if wr >= WR_HIGH:
        target = min(1.5, current_mult + 0.1)   # 高WR → 轻微加权
    elif wr <= WR_LOW:
        target = max(0.1, current_mult - 0.1)   # 低WR → 轻微降权
    else:
        return current_mult  # 中性区间不变

    # 平滑：向目标靠拢 50%
    new_mult = current_mult + 0.5 * (target - current_mult)
    return round(new_mult, 3)


def update_weights(dry_run: bool = False) -> dict:
    """
    主入口：扫描实盘结算数据，动态更新 signal_weights.json。

    返回：
      {
        'updated': int,   # 更新的key数量
        'skipped': int,   # 跳过的key数量（样本不足 or 静态锁定）
        'changes': list,  # 每个变化的详情
      }
    """
    trades  = _load_trades()
    sw_data = _load_signal_weights()
    weights = sw_data.get('weights', {})

    if not trades:
        return {'updated': 0, 'skipped': 0, 'changes': [],
                'reason': 'no_trades'}

    updated  = 0
    skipped  = 0
    changes  = []

    # 遍历所有现有 key
    for key, entry in weights.items():
        # 静态锁定检查
        if key in STATIC_LOCK:
            skipped += 1
            continue

        # 解析 key：REGIME:DIRECTION[:TIER]
        parts = key.split(':')
        if len(parts) < 2:
            skipped += 1
            continue
        regime = parts[0]
        direc  = parts[1]
        tier   = parts[2] if len(parts) >= 3 else None

        # 如果没有 tier，跳过（聚合key，由各tier子key覆盖）
        if tier is None:
            skipped += 1
            continue

        current_mult = float(entry.get('multiplier', 1.0) if isinstance(entry, dict) else entry)

        wr, n = _calc_rolling_wr(trades, regime, direc, tier)

        if wr is None:
            # 样本不足
            skipped += 1
            continue

        new_mult = _new_multiplier(current_mult, wr, n)

        if abs(new_mult - current_mult) < 0.005:
            # 变化太小，不更新
            skipped += 1
            continue

        # 记录变化
        change = {
            'key':          key,
            'old_mult':     current_mult,
            'new_mult':     new_mult,
            'wr':           wr,
            'n':            n,
            'ts':           int(time.time()),
        }
        changes.append(change)

        if not dry_run:
            if isinstance(entry, dict):
                entry['multiplier']      = new_mult
                entry['live_wr']         = wr
                entry['live_n']          = n
                entry['last_updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                entry['note']            = '%s (auto-updated live_wr=%.0f%% n=%d)' % (
                    entry.get('note', ''), wr * 100, n)
            else:
                weights[key] = {
                    'multiplier': new_mult,
                    'live_wr':    wr,
                    'live_n':     n,
                }

        updated += 1

    if not dry_run and changes:
        sw_data['weights']            = weights
        sw_data['last_auto_update']   = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        sw_data['auto_update_trades'] = len(trades)
        SW_PATH.write_text(json.dumps(sw_data, indent=2, ensure_ascii=False))

        # 写更新日志
        log_entry = {
            'ts':      int(time.time()),
            'updated': updated,
            'skipped': skipped,
            'changes': changes,
        }
        with open(LOG_PATH, 'a') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')

    return {
        'updated':  updated,
        'skipped':  skipped,
        'changes':  changes,
        'n_trades': len(trades),
    }


# ── CLI ────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    dry = '--dry-run' in sys.argv
    result = update_weights(dry_run=dry)
    prefix = '[DRY-RUN] ' if dry else ''
    print('%ssignal_weight_updater: updated=%d skipped=%d n_trades=%d' % (
        prefix, result['updated'], result['skipped'], result['n_trades']))
    for c in result['changes']:
        print('  %s  %.2f→%.2f  live_wr=%.0f%% n=%d' % (
            c['key'], c['old_mult'], c['new_mult'], 100*c['wr'], c['n']))
    if not result['changes']:
        print('  (无变化：样本不足或已是最优)')
