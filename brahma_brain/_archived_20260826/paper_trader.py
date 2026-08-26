"""
paper_trader.py — 梵天纸面交易系统 v1.0
══════════════════════════════════════════
设计院 2026-08-25 苏摩A方案封印

使命：
  score≥165 + AI议会≥3票支持 → 自动纸面下单，不等苏摩确认
  每天推日报：昨日纸面胜率 + 持仓盈亏 + 信号质量

纸面账户：
  起始NAV: $10,000（模拟）
  每笔风险: NAV × 2%
  最大持仓: 10笔同时
  止损: 按梵天SL_PCT执行
  止盈: 按梵天TP1执行

文件：
  data/paper_positions.json   — 当前纸面持仓
  data/paper_trades.jsonl     — 历史纸面交易记录
  data/paper_nav.json         — NAV追踪
"""

import os
import sys
import json
import time
from datetime import datetime, timezone
from pathlib import Path

_BASE  = Path(__file__).parent
_ROOT  = _BASE.parent
_DATA  = _ROOT / 'data'

PAPER_NAV_INIT   = 10000.0   # 初始模拟NAV
PAPER_RISK_PCT   = 0.02      # 每笔风险2%NAV
PAPER_MAX_POS    = 10        # 最大同时持仓数
PAPER_LOG        = _DATA / 'paper_trades.jsonl'
PAPER_POS_FILE   = _DATA / 'paper_positions.json'
PAPER_NAV_FILE   = _DATA / 'paper_nav.json'

# A方案门槛
AUTO_SCORE_MIN   = 165
AUTO_COUNCIL_MIN = 3  # 议会支持票数


# ── NAV管理 ──────────────────────────────────────────────────────────
def get_paper_nav() -> float:
    try:
        if PAPER_NAV_FILE.exists():
            d = json.loads(PAPER_NAV_FILE.read_text())
            return float(d.get('nav', PAPER_NAV_INIT))
    except Exception:
        pass
    return PAPER_NAV_INIT


def save_paper_nav(nav: float):
    _DATA.mkdir(exist_ok=True)
    PAPER_NAV_FILE.write_text(json.dumps({
        'nav': nav,
        'ts': time.time(),
        'ts_iso': datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False))


# ── 持仓管理 ─────────────────────────────────────────────────────────
def get_paper_positions() -> dict:
    try:
        if PAPER_POS_FILE.exists():
            return json.loads(PAPER_POS_FILE.read_text())
    except Exception:
        pass
    return {}


def save_paper_positions(positions: dict):
    _DATA.mkdir(exist_ok=True)
    PAPER_POS_FILE.write_text(json.dumps(positions, ensure_ascii=False, indent=2))


# ── 开仓 ─────────────────────────────────────────────────────────────
def open_paper_trade(symbol: str, direction: str, entry_price: float,
                     sl_price: float, tp_price: float,
                     score: float, regime: str, source: str = 'cpu_auto') -> dict:
    """
    纸面开仓。A方案：score≥165+议会3票自动触发，不等确认。
    返回 trade dict
    """
    positions = get_paper_positions()

    # 防重建
    if symbol in positions:
        return {'ok': False, 'reason': f'{symbol}已有纸面持仓'}

    # 最大持仓限制
    if len(positions) >= PAPER_MAX_POS:
        return {'ok': False, 'reason': f'纸面持仓已满{PAPER_MAX_POS}笔'}

    nav = get_paper_nav()
    risk_amount = nav * PAPER_RISK_PCT  # 每笔风险金额

    # 计算仓位大小
    sl_dist = abs(entry_price - sl_price) / entry_price
    if sl_dist <= 0:
        return {'ok': False, 'reason': 'SL距离为0，无法计算仓位'}

    position_size = risk_amount / (entry_price * sl_dist)  # 币数
    notional = position_size * entry_price                  # 名义价值

    trade = {
        'id':          f'{symbol}_{int(time.time())}',
        'symbol':      symbol,
        'direction':   direction,
        'entry_price': entry_price,
        'sl_price':    sl_price,
        'tp_price':    tp_price,
        'position_size': round(position_size, 6),
        'notional':    round(notional, 2),
        'risk_amount': round(risk_amount, 2),
        'sl_dist_pct': round(sl_dist * 100, 2),
        'score':       score,
        'regime':      regime,
        'source':      source,
        'status':      'OPEN',
        'open_ts':     time.time(),
        'open_ts_iso': datetime.now(timezone.utc).isoformat(),
        'pnl':         0.0,
        'pnl_pct':     0.0,
    }

    positions[symbol] = trade
    save_paper_positions(positions)
    _log_trade({**trade, 'event': 'OPEN'})

    return {'ok': True, 'trade': trade}


def close_paper_trade(symbol: str, close_price: float, reason: str) -> dict:
    """纸面平仓，更新NAV"""
    positions = get_paper_positions()
    if symbol not in positions:
        return {'ok': False, 'reason': f'{symbol}无纸面持仓'}

    trade = positions[symbol]
    entry  = trade['entry_price']
    size   = trade['position_size']
    direct = trade['direction']

    if direct == 'LONG':
        pnl = (close_price - entry) * size
    else:
        pnl = (entry - close_price) * size

    pnl_pct = pnl / trade['notional'] * 100

    nav = get_paper_nav()
    new_nav = nav + pnl
    save_paper_nav(new_nav)

    closed_trade = {**trade,
        'close_price': close_price,
        'close_reason': reason,
        'pnl':         round(pnl, 4),
        'pnl_pct':     round(pnl_pct, 2),
        'status':      'CLOSED',
        'close_ts':    time.time(),
        'close_ts_iso': datetime.now(timezone.utc).isoformat(),
        'nav_after':   round(new_nav, 2),
    }

    del positions[symbol]
    save_paper_positions(positions)
    _log_trade({**closed_trade, 'event': 'CLOSE'})

    return {'ok': True, 'trade': closed_trade, 'pnl': pnl, 'new_nav': new_nav}


# ── 持仓更新（止损/止盈检查）────────────────────────────────────────
def update_paper_positions() -> list:
    """
    检查所有纸面持仓的止损/止盈，自动平仓。
    由 position_guardian cron 调用。
    """
    from brahma_bus import get_price
    positions = get_paper_positions()
    closed = []

    for sym, trade in list(positions.items()):
        try:
            price = get_price(sym)
            direct = trade['direction']
            sl = trade['sl_price']
            tp = trade['tp_price']

            hit_sl = (direct == 'LONG' and price <= sl) or \
                     (direct == 'SHORT' and price >= sl)
            hit_tp = (direct == 'LONG' and price >= tp) or \
                     (direct == 'SHORT' and price <= tp)

            if hit_sl:
                result = close_paper_trade(sym, price, 'SL_HIT')
                closed.append({**result, 'reason': 'SL_HIT', 'symbol': sym})
            elif hit_tp:
                result = close_paper_trade(sym, price, 'TP_HIT')
                closed.append({**result, 'reason': 'TP_HIT', 'symbol': sym})
        except Exception:
            continue

    return closed


# ── 日报生成 ─────────────────────────────────────────────────────────
def generate_daily_report() -> str:
    """
    生成纸面交易日报：昨日胜率 + 持仓盈亏 + 信号质量
    每天早8点(CST)由cron推送
    """
    nav     = get_paper_nav()
    nav_ret = (nav - PAPER_NAV_INIT) / PAPER_NAV_INIT * 100
    positions = get_paper_positions()

    # 读历史交易
    trades = []
    if PAPER_LOG.exists():
        with open(PAPER_LOG) as f:
            for line in f:
                try:
                    t = json.loads(line)
                    if t.get('event') == 'CLOSE':
                        trades.append(t)
                except Exception:
                    pass

    # 昨日统计
    yesterday_start = time.time() - 86400
    yesterday_trades = [t for t in trades if t.get('close_ts', 0) > yesterday_start]
    wins  = [t for t in yesterday_trades if t.get('pnl', 0) > 0]
    loses = [t for t in yesterday_trades if t.get('pnl', 0) <= 0]
    total_pnl = sum(t.get('pnl', 0) for t in yesterday_trades)
    wr = len(wins) / len(yesterday_trades) * 100 if yesterday_trades else 0

    # 当前持仓盈亏
    pos_lines = []
    for sym, pos in positions.items():
        try:
            from brahma_bus import get_price
            price = get_price(sym)
            entry = pos['entry_price']
            direct = pos['direction']
            size = pos['position_size']
            unrealized = (price - entry) * size if direct == 'LONG' else (entry - price) * size
            pct = unrealized / pos['notional'] * 100
            icon = '📈' if unrealized > 0 else '📉'
            pos_lines.append(
                f'  {icon} {sym} {direct} 入场${entry:,.2f} 现价${price:,.2f} '
                f'未实现PnL={unrealized:+.2f}({pct:+.1f}%)'
            )
        except Exception:
            pos_lines.append(f'  {sym}: 价格获取失败')

    ts_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    lines = [
        f'📊 **梵天纸面交易日报** | {ts_str}',
        '',
        f'**账户NAV:** ${nav:,.2f} ({nav_ret:+.2f}% vs 初始$10,000)',
        '',
        f'**昨日战绩:** {len(yesterday_trades)}笔 | 胜率{wr:.1f}% | PnL={total_pnl:+.2f}',
        f'  ✅ 盈利: {len(wins)}笔  ❌ 亏损: {len(loses)}笔',
        '',
        f'**当前持仓:** {len(positions)}笔',
    ]
    if pos_lines:
        lines += pos_lines
    else:
        lines.append('  （无持仓）')

    # 累计统计
    all_wins  = [t for t in trades if t.get('pnl', 0) > 0]
    total_wr  = len(all_wins) / len(trades) * 100 if trades else 0
    lines += [
        '',
        f'**累计统计:** {len(trades)}笔 | 总胜率{total_wr:.1f}%',
        '',
        '_梵天纸面系统 A方案全自动 score≥165+议会3票_',
    ]
    return '\n'.join(lines)


# ── 日志 ─────────────────────────────────────────────────────────────
def _log_trade(trade: dict):
    _DATA.mkdir(exist_ok=True)
    with open(PAPER_LOG, 'a') as f:
        f.write(json.dumps(trade, ensure_ascii=False) + '\n')


# ── A方案主入口：CPU大脑调用 ─────────────────────────────────────────
def auto_paper_trade(symbol: str, direction: str, score: float,
                     council_votes: int, score_result: dict) -> dict:
    """
    A方案入口：score≥165 + 议会≥3票 → 自动纸面下单。
    由 brahma_cpu.py Layer3 调用。
    """
    if score < AUTO_SCORE_MIN:
        return {'ok': False, 'reason': f'score={score:.1f}<{AUTO_SCORE_MIN}'}
    if council_votes < AUTO_COUNCIL_MIN:
        return {'ok': False, 'reason': f'议会{council_votes}票<{AUTO_COUNCIL_MIN}票'}

    decision = score_result.get('decision', {})
    if not isinstance(decision, dict):
        return {'ok': False, 'reason': 'decision格式错误'}

    entry_plan = decision.get('entry_plan', {})
    entry_price = float(entry_plan.get('entry_mid',
                  entry_plan.get('entry_hi', score_result.get('price', 0))))
    sl_price    = float(entry_plan.get('sl', 0))
    tp_price    = float(entry_plan.get('tp1', 0))

    if not entry_price or not sl_price or not tp_price:
        return {'ok': False, 'reason': f'入场参数不完整 entry={entry_price} sl={sl_price} tp={tp_price}'}

    regime = score_result.get('regime', 'UNKNOWN')
    result = open_paper_trade(symbol, direction, entry_price, sl_price, tp_price,
                               score, regime, source='cpu_auto_A')

    if result.get('ok'):
        # 推送通知苏摩
        trade = result['trade']
        nav   = get_paper_nav()
        msg = (
            f'📝 **梵天纸面自动开单** | {symbol} | A方案\n'
            f'方向: {direction} | 评分: {score:.1f} | 议会: {council_votes}票\n'
            f'入场: ${entry_price:,.2f} | SL: ${sl_price:,.2f} | TP: ${tp_price:,.2f}\n'
            f'仓位: ${trade["notional"]:,.2f} | 风险: ${trade["risk_amount"]:,.2f}\n'
            f'NAV: ${nav:,.2f}'
        )
        try:
            import subprocess
            subprocess.Popen([
                'openclaw', 'infer', '--channel', 'jarvis',
                '--to', '73295708:thread:01a033af-3697-734a-9f9c-c3e34a00c378',
                '--message', msg,
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    return result


# ── CLI ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'report'
    if cmd == 'report':
        print(generate_daily_report())
    elif cmd == 'positions':
        pos = get_paper_positions()
        if pos:
            for sym, p in pos.items():
                print(f'{sym}: {p["direction"]} entry={p["entry_price"]} score={p["score"]}')
        else:
            print('无纸面持仓')
    elif cmd == 'update':
        closed = update_paper_positions()
        print(f'检查完成，平仓{len(closed)}笔')
        for c in closed:
            print(f'  {c.get("symbol")} {c.get("reason")} pnl={c.get("pnl",0):+.4f}')
    elif cmd == 'nav':
        print(f'NAV: ${get_paper_nav():,.2f}')
