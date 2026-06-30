#!/usr/bin/env python3
"""
达摩院学习飞轮 v1.0
每50笔 dharma_eligible=True 的交易触发参数校准
输出参数建议报告推送钉钉2，人工确认后写入配置
"""

import json, os, sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / 'data'
TRADE_RECORDS_PATH = DATA_DIR / 'trade_records.jsonl'
FLYWHEEL_STATE_PATH = DATA_DIR / 'flywheel_state.json'

# 触发阈值
FLYWHEEL_TRIGGER_N = 50   # 每50笔触发参数校准
RETRAIN_TRIGGER_N = 500   # 达摩院2026-05-23: 每500笔新增触发全量重训


# ─── 数据加载 ──────────────────────────────────────────────────────

def load_eligible_trades() -> list:
    """读取 trade_records.jsonl，过滤 dharma_eligible=True"""
    trades = []
    if not TRADE_RECORDS_PATH.exists():
        return trades
    with open(TRADE_RECORDS_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get('dharma_eligible', False):
                    trades.append(rec)
            except json.JSONDecodeError:
                continue
    return trades


def _load_flywheel_state() -> dict:
    if FLYWHEEL_STATE_PATH.exists():
        try:
            return json.loads(FLYWHEEL_STATE_PATH.read_text())
        except Exception:
            pass
    return {"last_triggered_count": 0, "trigger_history": []}


def _save_flywheel_state(state: dict):
    FLYWHEEL_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))


# ─── 统计计算 ──────────────────────────────────────────────────────

def _calc_group_stats(trades: list, key_fn) -> dict:
    """通用分组统计函数，key_fn(trade) -> group_key"""
    groups = defaultdict(list)
    for t in trades:
        k = key_fn(t)
        if k:
            groups[k].append(t)

    stats = {}
    for group, group_trades in groups.items():
        n = len(group_trades)
        wins = sum(1 for t in group_trades if t.get('pnl', 0) > 0)
        total_profit = sum(t.get('pnl', 0) for t in group_trades if t.get('pnl', 0) > 0)
        total_loss   = abs(sum(t.get('pnl', 0) for t in group_trades if t.get('pnl', 0) < 0))
        wr = wins / n if n > 0 else 0.0
        pf = total_profit / total_loss if total_loss > 0 else (float('inf') if total_profit > 0 else 0.0)
        stats[group] = {
            'n': n,
            'wr': round(wr, 4),
            'pf': round(pf, 4) if pf != float('inf') else 999.0,
            'total_pnl': round(total_profit - total_loss, 4),
        }
    return stats


def calc_regime_stats(trades: list) -> dict:
    """按 regime × direction 分组统计 WR/PF/n"""
    def key_fn(t):
        regime = t.get('regime', '')
        direction = t.get('direction', '')
        if not regime or not direction:
            return None
        return f"{regime}×{direction}"
    return _calc_group_stats(trades, key_fn)


def calc_channel_stats(trades: list) -> dict:
    """按通道 A/B/C/D 分组统计 WR/PF/n"""
    def key_fn(t):
        channel = t.get('channel', t.get('signal_channel', ''))
        return channel if channel else None
    return _calc_group_stats(trades, key_fn)


def calc_tier_stats(trades: list) -> dict:
    """按 tier S/A/B/C 统计"""
    def key_fn(t):
        tier = t.get('tier', t.get('signal_tier', ''))
        return tier if tier else None
    return _calc_group_stats(trades, key_fn)


# ─── 建议生成 ──────────────────────────────────────────────────────

def generate_suggestions(regime_stats: dict, channel_stats: dict, tier_stats: dict) -> list:
    """
    根据统计结果生成参数建议：
    - WR < 40% 且 n >= 5：建议加入黑名单或降权
    - PF > 1.3 且 n >= 5：建议提升 kelly 权重
    - 体制组合 PF < 0.8：建议降低 REGIME_KELLY_MULT
    - 通道 PF 排名：建议更新 channel 优先级
    """
    suggestions = []

    # 分析体制统计
    for key, s in regime_stats.items():
        n, wr, pf = s['n'], s['wr'], s['pf']
        if n >= 5 and wr < 0.40:
            suggestions.append({
                'type': 'blacklist_or_downweight',
                'target': 'regime',
                'key': key,
                'reason': f'WR={wr*100:.1f}% < 40%，n={n}',
                'action': f'建议将体制组合 [{key}] 加入黑名单或降低 REGIME_KELLY_MULT',
            })
        if n >= 5 and pf < 0.8:
            suggestions.append({
                'type': 'reduce_regime_kelly',
                'target': 'regime',
                'key': key,
                'reason': f'PF={pf:.2f} < 0.8，n={n}',
                'action': f'建议降低体制组合 [{key}] 的 REGIME_KELLY_MULT',
            })
        if n >= 5 and pf > 1.3:
            suggestions.append({
                'type': 'increase_kelly',
                'target': 'regime',
                'key': key,
                'reason': f'PF={pf:.2f} > 1.3，n={n}',
                'action': f'建议提升体制组合 [{key}] 的 kelly 权重',
            })

    # 分析通道统计
    if channel_stats:
        sorted_channels = sorted(channel_stats.items(), key=lambda x: x[1]['pf'], reverse=True)
        priority_list = [ch for ch, _ in sorted_channels]
        suggestions.append({
            'type': 'channel_priority_update',
            'target': 'channel',
            'key': 'all_channels',
            'reason': f'基于PF排序: {" > ".join(priority_list)}',
            'action': f'建议更新 channel 优先级为: {priority_list}',
        })

        for ch, s in channel_stats.items():
            n, wr, pf = s['n'], s['wr'], s['pf']
            if n >= 5 and wr < 0.40:
                suggestions.append({
                    'type': 'blacklist_or_downweight',
                    'target': 'channel',
                    'key': ch,
                    'reason': f'WR={wr*100:.1f}% < 40%，n={n}',
                    'action': f'建议降低通道 [{ch}] 权重或暂停使用',
                })
            if n >= 5 and pf > 1.3:
                suggestions.append({
                    'type': 'increase_kelly',
                    'target': 'channel',
                    'key': ch,
                    'reason': f'PF={pf:.2f} > 1.3，n={n}',
                    'action': f'建议提升通道 [{ch}] kelly 权重',
                })

    # 分析 tier 统计
    for tier, s in tier_stats.items():
        n, wr, pf = s['n'], s['wr'], s['pf']
        if n >= 5 and wr < 0.40:
            suggestions.append({
                'type': 'blacklist_or_downweight',
                'target': 'tier',
                'key': tier,
                'reason': f'WR={wr*100:.1f}% < 40%，n={n}',
                'action': f'建议降低 Tier {tier} 信号评分权重',
            })
        if n >= 5 and pf > 1.3:
            suggestions.append({
                'type': 'increase_kelly',
                'target': 'tier',
                'key': tier,
                'reason': f'PF={pf:.2f} > 1.3，n={n}',
                'action': f'建议提升 Tier {tier} kelly 系数',
            })

    return suggestions


# ─── 报告格式化 ────────────────────────────────────────────────────

def format_report(
    regime_stats: dict,
    channel_stats: dict,
    tier_stats: dict,
    suggestions: list,
    total_eligible: int,
) -> str:
    """格式化为钉钉消息（Markdown）"""
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    lines = [
        f"## 🎡 达摩院学习飞轮报告",
        f"**触发时间:** {now_str}  |  **本批交易数:** {total_eligible}",
        "",
        "### 📊 体制 × 方向统计",
    ]

    if regime_stats:
        lines.append("| 体制×方向 | n | WR | PF | 总PnL |")
        lines.append("|-----------|---|----|----|-------|")
        for key, s in sorted(regime_stats.items(), key=lambda x: x[1]['pf'], reverse=True):
            wr_str = f"{s['wr']*100:.1f}%"
            pf_icon = "🟢" if s['pf'] > 1.2 else ("🔴" if s['pf'] < 0.8 else "🟡")
            lines.append(f"| {key} | {s['n']} | {wr_str} | {pf_icon}{s['pf']:.2f} | {s['total_pnl']:+.2f} |")
    else:
        lines.append("_暂无数据_")

    lines.extend(["", "### 📡 通道统计"])
    if channel_stats:
        lines.append("| 通道 | n | WR | PF | 总PnL |")
        lines.append("|------|---|----|----|-------|")
        for ch, s in sorted(channel_stats.items(), key=lambda x: x[1]['pf'], reverse=True):
            wr_str = f"{s['wr']*100:.1f}%"
            pf_icon = "🟢" if s['pf'] > 1.2 else ("🔴" if s['pf'] < 0.8 else "🟡")
            lines.append(f"| {ch} | {s['n']} | {wr_str} | {pf_icon}{s['pf']:.2f} | {s['total_pnl']:+.2f} |")
    else:
        lines.append("_暂无数据_")

    lines.extend(["", "### 🏷️ Tier 统计"])
    if tier_stats:
        lines.append("| Tier | n | WR | PF | 总PnL |")
        lines.append("|------|---|----|----|-------|")
        for tier, s in sorted(tier_stats.items(), key=lambda x: x[1]['pf'], reverse=True):
            wr_str = f"{s['wr']*100:.1f}%"
            pf_icon = "🟢" if s['pf'] > 1.2 else ("🔴" if s['pf'] < 0.8 else "🟡")
            lines.append(f"| {tier} | {s['n']} | {wr_str} | {pf_icon}{s['pf']:.2f} | {s['total_pnl']:+.2f} |")
    else:
        lines.append("_暂无数据_")

    lines.extend(["", "### 💡 参数建议"])
    if suggestions:
        for i, sg in enumerate(suggestions, 1):
            icon = "⚠️" if 'blacklist' in sg['type'] or 'reduce' in sg['type'] else "✅"
            lines.append(f"{i}. {icon} **[{sg['target'].upper()}·{sg['key']}]** {sg['action']}")
            lines.append(f"   > 原因: {sg['reason']}")
    else:
        lines.append("✅ 当前参数表现良好，暂无建议")

    lines.extend(["", "---", "_⚙️ 人工确认后方可写入配置_"])
    return "\n".join(lines)


# ─── 钉钉推送 ──────────────────────────────────────────────────────

def push_to_dingtalk(title: str, text: str) -> bool:
    """调用 push_hub.py 的 push_dd2(title, text)"""
    try:
        scripts_dir = str(BASE_DIR.parent / 'scripts')
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from push_hub import PushHub
        hub = PushHub()
        return hub.push_dd2(title, text)
    except Exception as e:
        print(f"[Flywheel] 钉钉推送失败: {e}")
        return False


# ─── 主入口 ────────────────────────────────────────────────────────

def check_and_trigger():
    """
    主入口：检查是否达到触发条件，达到则计算并推送
    每 FLYWHEEL_TRIGGER_N 笔合格交易触发一次
    """
    trades = load_eligible_trades()
    total = len(trades)

    state = _load_flywheel_state()
    last_triggered = state.get('last_triggered_count', 0)
    new_trades_since = total - last_triggered

    print(f"[Flywheel] 合格交易总数={total}，上次触发时={last_triggered}，新增={new_trades_since}")

    if new_trades_since < FLYWHEEL_TRIGGER_N:
        print(f"[Flywheel] 尚未达到触发阈值({FLYWHEEL_TRIGGER_N})，跳过")
        return False

    print(f"[Flywheel] 🎡 触发！开始计算参数校准报告...")

    # 只使用上次触发后的新增交易进行统计
    new_trades = trades[last_triggered:]

    regime_stats  = calc_regime_stats(new_trades)
    channel_stats = calc_channel_stats(new_trades)
    tier_stats    = calc_tier_stats(new_trades)
    suggestions   = generate_suggestions(regime_stats, channel_stats, tier_stats)

    report = format_report(regime_stats, channel_stats, tier_stats, suggestions, len(new_trades))

    title = f"🎡 达摩飞轮报告 · {len(new_trades)}笔新数据 · {len(suggestions)}条建议"
    ok = push_to_dingtalk(title, report)
    print(f"[Flywheel] 报告推送{'成功' if ok else '失败'}")

    # ── D9修复(2026-05-18): 将建议写回 Blueprint ──────────────────
    try:
        import sys as _sys_bw, os as _os_bw
        _sys_bw.path.insert(0, _os_bw.path.dirname(_os_bw.path.abspath(__file__)))
        from blueprint_writer import write_from_flywheel as _bw
        _bw(suggestions, regime_stats, len(new_trades))
    except Exception as _bw_e:
        print(f"[Flywheel] ⚠️ Blueprint写回失败(不阻断): {_bw_e}")

    # 更新状态
    state['last_triggered_count'] = total
    state['trigger_history'].append({
        'ts': datetime.now(timezone.utc).isoformat(),
        'count': len(new_trades),
        'suggestions': len(suggestions),
    })
    _save_flywheel_state(state)

    return True


# ─── 直接运行 ──────────────────────────────────────────────────────

if __name__ == '__main__':
    print("=" * 55)
    print("达摩院学习飞轮 v1.0 — 语法检查 & 功能自测")
    print("=" * 55)

    # 自测：构造假数据
    mock_trades = []
    for i in range(60):
        mock_trades.append({
            'dharma_eligible': True,
            'regime': 'BULL_TREND' if i % 2 == 0 else 'BEAR_TREND',
            'direction': 'LONG' if i % 3 != 0 else 'SHORT',
            'channel': ['A', 'B', 'C', 'D'][i % 4],
            'tier': ['S', 'A', 'B', 'C'][i % 4],
            'pnl': 10.0 if i % 5 != 0 else -8.0,
        })

    print(f"\n✅ 构造测试数据: {len(mock_trades)} 笔")

    regime_stats  = calc_regime_stats(mock_trades)
    channel_stats = calc_channel_stats(mock_trades)
    tier_stats    = calc_tier_stats(mock_trades)
    suggestions   = generate_suggestions(regime_stats, channel_stats, tier_stats)
    report        = format_report(regime_stats, channel_stats, tier_stats, suggestions, len(mock_trades))

    print(f"✅ 体制统计: {len(regime_stats)} 组")
    print(f"✅ 通道统计: {len(channel_stats)} 组")
    print(f"✅ Tier统计: {len(tier_stats)} 组")
    print(f"✅ 参数建议: {len(suggestions)} 条")
    print(f"\n--- 报告预览 (前500字符) ---")
    print(report[:500])
    print("\n✅ 语法检查通过，所有函数正常")

    # 检查真实触发逻辑（不推送，只打印状态）
    real_trades = load_eligible_trades()
    print(f"\n📂 真实 trade_records.jsonl: {len(real_trades)} 笔合格交易")
    state = _load_flywheel_state()
    print(f"📂 飞轮状态: 上次触发={state.get('last_triggered_count', 0)}")
