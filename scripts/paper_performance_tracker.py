"""
paper_performance_tracker.py — 纸面实盘绩效追踪器
2026-08-27 苏摩111封印

目标：纸面模式运行1个月，积累铁证后决策注资
所有信号以纸面执行，统计真实绩效指标：
- 胜率/平均盈亏/最大回撤/Sharpe/EV
- 体制维度分析（BULL/BEAR/CHOP分别统计）
- 信号质量分析（score分层统计）

接入位置: scripts/auto_executor.py (纸面路径PAPER_PENDING之后)
"""

import json
import time
import math
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

BASE         = Path(__file__).parent.parent
PAPER_ACC    = BASE / 'data' / 'paper_account.json'
PAPER_TRADES = BASE / 'data' / 'paper_trades.jsonl'
PAPER_DAILY  = BASE / 'data' / 'paper_daily.jsonl'

# ══ 纸面绩效计算 ══════════════════════════════════════════════

def record_paper_trade(signal: dict, result: dict):
    """
    记录一笔纸面交易结果
    由 auto_executor PAPER_PENDING 路径在信号触达 TP/SL 时调用
    """
    PAPER_TRADES.parent.mkdir(parents=True, exist_ok=True)

    sym       = signal.get('symbol', '')
    direction = signal.get('direction', '')
    score     = float(signal.get('score_final') or signal.get('score') or 0)
    regime    = signal.get('regime', '')
    sl_pct    = float(signal.get('sl_pct', 2.0) or 2.0)
    rr        = float(signal.get('rr1', 1.0) or 1.0)

    outcome   = result.get('outcome', '')   # 'TP' / 'SL' / 'TIMEOUT'
    pnl_pct   = float(result.get('pnl_pct', 0) or 0)
    is_win    = outcome in ('TP', 'TP1', 'TP2', 'WIN') or pnl_pct > 0

    trade = {
        'ts':        time.time(),
        'ts_iso':    datetime.now(timezone.utc).isoformat(),
        'symbol':    sym,
        'direction': direction,
        'score':     score,
        'regime':    regime,
        'sl_pct':    sl_pct,
        'rr':        rr,
        'outcome':   outcome,
        'pnl_pct':   round(pnl_pct, 4),
        'is_win':    is_win,
    }

    with open(PAPER_TRADES, 'a', encoding='utf-8') as f:
        f.write(json.dumps(trade, ensure_ascii=False) + '\n')

    # 更新账户状态
    _update_account(pnl_pct, is_win)
    return trade


def _update_account(pnl_pct: float, is_win: bool):
    """更新纸面账户NAV和统计"""
    acc = _load_account()
    nav = acc.get('current_nav', 100.0)

    # 以NAV百分比计算本次盈亏
    new_nav = nav * (1 + pnl_pct / 100)
    acc['current_nav']   = round(new_nav, 4)
    acc['total_trades']  = acc.get('total_trades', 0) + 1
    acc['total_pnl_pct'] = round(acc.get('total_pnl_pct', 0) + pnl_pct, 4)

    if is_win:
        acc['win_trades'] = acc.get('win_trades', 0) + 1
    else:
        acc['loss_trades'] = acc.get('loss_trades', 0) + 1

    # 更新峰值和最大回撤
    peak = acc.get('peak_nav', 100.0)
    if new_nav > peak:
        acc['peak_nav'] = round(new_nav, 4)
        peak = new_nav

    dd = (peak - new_nav) / peak * 100
    if dd > acc.get('max_drawdown_pct', 0):
        acc['max_drawdown_pct'] = round(dd, 2)

    acc['last_updated'] = datetime.now(timezone.utc).isoformat()
    PAPER_ACC.write_text(json.dumps(acc, indent=2, ensure_ascii=False))


def _load_account() -> dict:
    if PAPER_ACC.exists():
        try: return json.loads(PAPER_ACC.read_text())
        except: pass
    return {'mode': 'PAPER_LIVE', 'start_nav': 100.0, 'current_nav': 100.0,
            'peak_nav': 100.0, 'total_trades': 0, 'win_trades': 0,
            'loss_trades': 0, 'total_pnl_pct': 0.0, 'max_drawdown_pct': 0.0}


def generate_performance_report() -> str:
    """生成完整绩效报告（供日报推送）"""
    acc   = _load_account()
    trades = []
    if PAPER_TRADES.exists():
        for l in PAPER_TRADES.read_text().strip().splitlines():
            try: trades.append(json.loads(l))
            except: pass

    n       = len(trades)
    wins    = [t for t in trades if t.get('is_win')]
    losses  = [t for t in trades if not t.get('is_win')]
    wr      = len(wins) / n if n > 0 else 0
    pnls    = [t.get('pnl_pct', 0) for t in trades]
    avg_win = sum(t.get('pnl_pct', 0) for t in wins) / max(len(wins), 1)
    avg_loss= sum(t.get('pnl_pct', 0) for t in losses) / max(len(losses), 1)
    ev      = wr * avg_win + (1 - wr) * avg_loss

    # Sharpe (简化：日收益标准差)
    sharpe = '计算中(需≥20笔)'
    if n >= 20:
        mean_p = sum(pnls) / n
        std_p  = math.sqrt(sum((p - mean_p)**2 for p in pnls) / n) if n > 1 else 0.001
        sharpe = f'{(mean_p / std_p * math.sqrt(365)):.2f}' if std_p > 0 else '∞'

    # 体制分层
    regime_stats = defaultdict(lambda: {'n': 0, 'wins': 0, 'pnl': 0.0})
    for t in trades:
        r = t.get('regime', '?')
        regime_stats[r]['n'] += 1
        regime_stats[r]['pnl'] += t.get('pnl_pct', 0)
        if t.get('is_win'): regime_stats[r]['wins'] += 1

    # score分层
    score_stats = defaultdict(lambda: {'n': 0, 'wins': 0})
    for t in trades:
        s = float(t.get('score', 0))
        bucket = '≥155' if s >= 155 else ('140-155' if s >= 140 else ('120-140' if s >= 120 else '<120'))
        score_stats[bucket]['n'] += 1
        if t.get('is_win'): score_stats[bucket]['wins'] += 1

    # 起始日期
    start = acc.get('start_date', '2026-08-27')
    now_str = datetime.now(timezone.utc).strftime('%m-%d %H:%M')
    nav_chg = acc.get('current_nav', 100) - acc.get('start_nav', 100)

    lines = [
        f'📊 **梵天纸面实盘绩效报告**',
        f'━━━━━━━━━━━━━━━━━━━━━━',
        f'📅 {start} 启动 → {now_str} UTC',
        f'━━━━━━━━━━━━━━━━━━━━━━',
        f'💰 NAV: {acc.get("start_nav",100):.1f} → {acc.get("current_nav",100):.2f} ({nav_chg:+.2f}单位)',
        f'📈 总收益: {acc.get("total_pnl_pct",0):+.2f}%  |  最大回撤: -{acc.get("max_drawdown_pct",0):.2f}%',
        f'🎯 胜率: {wr:.1%} ({len(wins)}W/{len(losses)}L/{n}总)',
        f'⚡ EV/笔: {ev:+.3f}%  |  Sharpe: {sharpe}',
        f'💹 平均盈利: {avg_win:+.2f}%  |  平均亏损: {avg_loss:+.2f}%',
        f'━━━━━━━━━━━━━━━━━━━━━━',
    ]

    if regime_stats:
        lines.append('📋 **体制分层绩效:**')
        for regime, s in sorted(regime_stats.items(), key=lambda x: -x[1]['n']):
            r_wr = s['wins'] / s['n'] if s['n'] > 0 else 0
            r_ev = s['pnl'] / s['n'] if s['n'] > 0 else 0
            lines.append(f'  {regime}: WR={r_wr:.0%} EV={r_ev:+.2f}% n={s["n"]}')

    if score_stats:
        lines.append('🏆 **Score分层绩效:**')
        for bucket in ['≥155', '140-155', '120-140', '<120']:
            s = score_stats.get(bucket, {'n': 0, 'wins': 0})
            if s['n'] > 0:
                s_wr = s['wins'] / s['n']
                lines.append(f'  score{bucket}: WR={s_wr:.0%} n={s["n"]}')

    # 注资建议
    lines.append('━━━━━━━━━━━━━━━━━━━━━━')
    days_run = max((time.time() - time.mktime(
        __import__('time').strptime(start, '%Y-%m-%d'))) / 86400, 0.1)
    days_left = max(30 - days_run, 0)

    if days_left > 0:
        lines.append(f'⏳ 注资倒计时: 还需 {days_left:.0f} 天')
    else:
        if wr >= 0.55 and ev > 0 and acc.get('max_drawdown_pct', 99) <= 15:
            lines.append('🟢 **注资建议: 达标！可以注入真实资金**')
            lines.append(f'   胜率{wr:.0%}≥55% ✅  EV{ev:+.3f}%>0 ✅  最大回撤{acc.get("max_drawdown_pct",0):.1f}%≤15% ✅')
        else:
            lines.append('🔴 **注资建议: 尚未达标，继续观察**')
            if wr < 0.55:   lines.append(f'   胜率{wr:.0%}<55% ❌')
            if ev <= 0:     lines.append(f'   EV{ev:+.3f}%≤0 ❌')
            if acc.get('max_drawdown_pct', 0) > 15:
                lines.append(f'   最大回撤{acc.get("max_drawdown_pct",0):.1f}%>15% ❌')

    return '\n'.join(lines)


def get_quick_stats() -> dict:
    """快速统计，供heartbeat/cron调用"""
    acc = _load_account()
    trades = []
    if PAPER_TRADES.exists():
        for l in PAPER_TRADES.read_text().strip().splitlines():
            try: trades.append(json.loads(l))
            except: pass

    n    = len(trades)
    wins = sum(1 for t in trades if t.get('is_win'))
    wr   = wins / n if n > 0 else 0
    pnls = [t.get('pnl_pct', 0) for t in trades]
    ev   = sum(pnls) / n if n > 0 else 0

    start = acc.get('start_date', '2026-08-27')
    days  = max((time.time() - time.mktime(
        __import__('time').strptime(start, '%Y-%m-%d'))) / 86400, 0.1)

    return {
        'n': n, 'wr': round(wr, 4), 'ev': round(ev, 4),
        'nav': acc.get('current_nav', 100),
        'max_dd': acc.get('max_drawdown_pct', 0),
        'days_run': round(days, 1),
        'days_left': max(30 - days, 0),
        'fund_ready': wr >= 0.55 and ev > 0 and acc.get('max_drawdown_pct', 99) <= 15 and days >= 30,
    }


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'report':
        print(generate_performance_report())
    else:
        stats = get_quick_stats()
        print(f'纸面实盘: {stats["n"]}笔 WR={stats["wr"]:.1%} EV={stats["ev"]:+.3f}% '
              f'NAV={stats["nav"]:.2f} DD={stats["max_dd"]:.1f}% '
              f'运行{stats["days_run"]:.0f}天 注资倒计时{stats["days_left"]:.0f}天')
