#!/usr/bin/env python3
"""
brahma_signal_router.py — 信号分级路由 + 模拟自动开单
[封印 2026-10-18 苏摩111] 三方联合设计

接入位置: brahma_flow_batch.py run_batch()末尾调用
         trading_copilot.py M7每分钟检查SL/TP

信号分级（三方评估后确定）:
  S级: 直接推送苏摩 + 自动模拟开单
  A级: 推送苏摩（等111确认）
  B级: 静默写入 data/batch_signals.json

三方铁律（不可妥协）:
  ① 方仓n≥10才激活方仓权重（无方仓=技术信号，门槛+20）
  ② LSR>2.5才标记P2猎杀
  ③ 无主力手法=最高A级（不自动开单）
  ④ 宏观event_risk>0=全冻结
  ⑤ 账本最多6笔同时持仓
  ⑥ 单日亏损>3%NAV=冻结新开单
"""
import json, time, sys
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'brahma_brain'))
sys.path.insert(0, str(BASE / 'scripts'))

PAPER_NAV = 10_000.0   # 模拟NAV基准（U）
MAX_POSITIONS = 6      # 最大同时持仓数
DAILY_DD_LIMIT = 0.03  # 单日亏损上限3%NAV

# ── 信号评分 ──────────────────────────────────────────────────────────────

def score_signal(r: dict) -> tuple:
    """
    返回: (score, grade, direction, reason_list)
    grade: S/A/B
    """
    score = 0
    reasons = []
    fc_weight = r.get('fc_weight', 1.0)  # 无方仓=0.5降级

    # 方仓置信度（最高权重40分）
    fc_n = r.get('fc_n', 0)
    fc_long = r.get('fc_long', 0)
    fc_short = r.get('fc_short', 0)
    if fc_n >= 10:
        if fc_long >= 0.75:
            score += int(40 * fc_weight)
            reasons.append(f'方仓LONG={fc_long:.0%} n={fc_n}')
            direction = 'LONG'
        elif fc_short >= 0.75:
            score += int(40 * fc_weight)
            reasons.append(f'方仓SHORT={fc_short:.0%} n={fc_n}')
            direction = 'SHORT'
        else:
            score += int(10 * fc_weight)
            reasons.append(f'方仓中性 n={fc_n}')
            direction = 'LONG' if fc_long > fc_short else 'SHORT'
    else:
        score += 0
        reasons.append(f'方仓样本不足n={fc_n}(<10)')
        direction = 'LONG' if r.get('rsi_1h', 50) < 40 else 'SHORT'

    # 主力手法识别（30分）
    patterns = r.get('patterns', [])
    if patterns:
        top_p = patterns[0]
        pid, conf = top_p[0], top_p[1]
        if pid in ('P2_HUNT', 'P3_ACCUMULATE') and conf >= 0.70:
            score += 30
            reasons.append(f'主力手法={pid}({conf:.0%})')
            if pid == 'P2_HUNT':
                direction = 'LONG'  # 猎杀完成后做多
        elif pid == 'P1_SQUEEZE' and conf >= 0.70:
            score += 20
            reasons.append(f'逼空={pid}({conf:.0%})')
            direction = 'SHORT'  # 逼空后做空
    else:
        reasons.append('无主力手法')

    # RSI豁免区（20分）Gate5
    rsi = r.get('rsi_1h', 50)
    if 55 <= rsi <= 75:
        score += 20
        reasons.append(f'RSI={rsi:.0f}豁免区')
    elif rsi < 30 or rsi > 80:
        score -= 10
        reasons.append(f'RSI={rsi:.0f}极值区-10')

    # LSR猎杀结构（15分）
    lsr = r.get('lsr', 1.0)
    if lsr >= 2.5:
        score += 15
        reasons.append(f'LSR={lsr:.3f}⚡猎杀')
    elif lsr >= 2.0:
        score += 8
        reasons.append(f'LSR={lsr:.3f}偏多')

    # OI确认（10分）
    oi = r.get('oi_dir', 'FLAT')
    if oi == 'SHORT_BUILD' and direction == 'SHORT':
        score += 10; reasons.append('OI空头建仓确认')
    elif oi == 'LONG_UNWIND' and direction == 'SHORT':
        score += 5; reasons.append('OI多头平仓')

    # Taker（5分）
    taker = r.get('taker', 1.0)
    if taker >= 1.5 and direction == 'LONG':
        score += 5; reasons.append(f'Taker买={taker:.2f}')
    elif taker <= 0.7 and direction == 'SHORT':
        score += 5; reasons.append(f'Taker卖={taker:.2f}')

    # 无主力手法=强制A级上限
    if not patterns:
        score = min(score, 55)

    # 分级
    if score >= 70:
        grade = 'S'
    elif score >= 40:
        grade = 'A'
    else:
        grade = 'B'

    return score, grade, direction, reasons

# ── 风控检查 ──────────────────────────────────────────────────────────────

def risk_check() -> tuple:
    """返回 (ok, reason)"""
    # 宏观门控
    mf = BASE / 'data' / 'macro_state.json'
    if mf.exists():
        try:
            macro = json.load(open(mf))
            if macro.get('event_risk', 0) > 0:
                return False, f"宏观event_risk={macro['event_risk']}≥1，全冻结"
        except: pass

    # 账本检查
    pf = BASE / 'data' / 'paper_positions.json'
    if pf.exists():
        try:
            pos = json.load(open(pf))
            open_cnt = len(pos.get('positions', {}))
            if open_cnt >= MAX_POSITIONS:
                return False, f'持仓已满{open_cnt}/{MAX_POSITIONS}'
            # 单日亏损
            today_pnl = pos.get('today_pnl', 0)
            if today_pnl < -PAPER_NAV * DAILY_DD_LIMIT:
                return False, f'今日亏损{today_pnl:.1f}U超限({DAILY_DD_LIMIT*100:.0f}%NAV)'
        except: pass

    return True, 'OK'

def already_has_position(sym: str) -> bool:
    """同一标的禁止重复建仓"""
    pf = BASE / 'data' / 'paper_positions.json'
    if not pf.exists(): return False
    try:
        pos = json.load(open(pf))
        return (sym.upper() + 'USDT') in pos.get('positions', {})
    except: return False

# ── 推送格式 ──────────────────────────────────────────────────────────────

def format_signal_push(r: dict, grade: str, direction: str,
                        score: int, reasons: list) -> str:
    sym = r.get('sym', '?')
    px = r.get('price', 0)
    atr4h = r.get('atr4h', 0)
    sl_dist = atr4h * 1.5
    sl = px * (1 - sl_dist/px) if direction == 'LONG' else px * (1 + sl_dist/px)
    tp1 = px * (1 + sl_dist*1.5/px) if direction == 'LONG' else px * (1 - sl_dist*1.5/px)
    icon = '🚨' if grade == 'S' else '⚡'
    patterns = r.get('patterns', [])
    pat_str = f"{patterns[0][0]}({patterns[0][1]:.0%})" if patterns else '无手法'
    action = '已自动模拟开单' if grade == 'S' else '等苏摩111确认'
    return (
        f"{icon} 梵天{grade}级信号 | {sym} ${px:,.3f}\n"
        f"方向={direction}  score={score}  手法={pat_str}\n"
        f"LSR={r.get('lsr',1):.3f}  RSI={r.get('rsi_1h',50):.0f}  OI={r.get('oi_dir','?')}\n"
        f"SL≈${sl:,.2f}  TP1≈${tp1:,.2f}  ATR4H=${atr4h:.1f}\n"
        f"理由: {' | '.join(reasons[:3])}\n"
        f"→ {action}"
    )

# ── 主路由 ────────────────────────────────────────────────────────────────

def route_signals(results: list) -> dict:
    """
    输入: brahma_flow_batch 的results列表
    输出: {'S':[], 'A':[], 'B':[], 'frozen': reason}
    """
    output = {'S': [], 'A': [], 'B': [], 'frozen': None, '_ts': time.time()}

    # 全局风控
    risk_ok, risk_reason = risk_check()
    if not risk_ok:
        output['frozen'] = risk_reason
        print(f'[router] 🔴 全局冻结: {risk_reason}')
        return output

    for r in results:
        sym = r.get('sym', '')
        if not sym or r.get('error') or r.get('price', 0) <= 0:
            continue

        score, grade, direction, reasons = score_signal(r)
        r['_score'] = score; r['_grade'] = grade
        r['_direction'] = direction; r['_reasons'] = reasons

        if grade == 'S':
            # 重复建仓检查
            if already_has_position(sym):
                grade = 'A'; r['_grade'] = 'A'
                reasons.append(f'已持{sym}，降级A')
            else:
                output['S'].append(r)
                # 自动模拟开单
                _auto_open(sym, direction, r)
        if grade == 'A':
            output['A'].append(r)
        else:
            output['B'].append(r)

    # 推送S级和A级
    _push_signals(output)

    # 写入路由结果
    rf = BASE / 'data' / 'signal_route_latest.json'
    json.dump({k: v for k, v in output.items() if k != 'S' and k != 'A'},
              open(rf, 'w'), ensure_ascii=False)

    print(f'[router] S={len(output["S"])} A={len(output["A"])} B={len(output["B"])}')
    return output

def _auto_open(sym: str, direction: str, r: dict):
    """S级信号自动模拟开单"""
    try:
        from paper_executor import open_paper_position
        px = r.get('price', 0)
        atr4h = r.get('atr4h', 0) or px * 0.005
        sl_dist = atr4h * 1.5
        sl  = px * (1 - sl_dist/px) if direction == 'LONG' else px * (1 + sl_dist/px)
        tp1 = px * (1 + sl_dist*1.5/px) if direction == 'LONG' else px * (1 - sl_dist*1.5/px)
        tp2 = px * (1 + sl_dist*2.5/px) if direction == 'LONG' else px * (1 - sl_dist*2.5/px)
        result = open_paper_position(
            symbol=sym.upper()+'USDT',
            direction=direction,
            entry_price=px,
            sl=round(sl, 4), tp1=round(tp1, 4), tp2=round(tp2, 4),
            nav_pct=1.5, leverage=3,
            regime=r.get('regime','CHOP_MID'),
            score=r.get('_score', 50),
            rr=round((tp1-px)/abs(px-sl), 2) if abs(px-sl)>0 else 1.5,
            source='auto_signal_router',
            note=f"S级信号 手法={r.get('patterns',[['']])[0][0] if r.get('patterns') else '无'}"
        )
        print(f'  [router] 🟢 自动开单 {sym} {direction} @${px:,.3f} SL=${sl:,.3f} → {result}')
    except Exception as e:
        print(f'  [router] ❌ 开单失败 {sym}: {e}')

def _push_signals(output: dict):
    """推送S/A级信号到Jarvis"""
    try:
        import importlib.util, pathlib
        spec = importlib.util.spec_from_file_location(
            'push_hub', pathlib.Path(__file__).parent / 'push_hub.py')
        ph = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ph)

        for r in output.get('S', []):
            sym = r.get('sym', '?')
            msg = format_signal_push(r, 'S', r['_direction'], r['_score'], r['_reasons'])
            ph.push_jarvis(msg, priority='P1',
                           dedup_key=f'signal_S_{sym}', dedup_ttl=3600)

        for r in output.get('A', [])[:3]:  # A级最多推3条，防刷屏
            sym = r.get('sym', '?')
            msg = format_signal_push(r, 'A', r['_direction'], r['_score'], r['_reasons'])
            ph.push_jarvis(msg, priority='P2',
                           dedup_key=f'signal_A_{sym}', dedup_ttl=14400)
    except Exception as e:
        print(f'[router] 推送失败(非致命): {e}')

# ── SL/TP实时检查（接入trading_copilot M7）────────────────────────────────

def check_sl_tp_all() -> list:
    """
    检查所有模拟持仓的SL/TP
    接入: trading_copilot.py M7每分钟调用
    返回: 触发平仓的列表
    """
    triggered = []
    pf = BASE / 'data' / 'paper_positions.json'
    if not pf.exists(): return triggered

    try:
        data = json.load(open(pf))
        positions = data.get('positions', {})
        # 兼容list格式（旧账本）
        if isinstance(positions, list): positions = {}
        if not positions: return triggered

        import urllib.request
        prices = {}
        for sym_full in positions.keys():
            try:
                url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym_full}'
                with urllib.request.urlopen(url, timeout=5) as resp:
                    prices[sym_full] = float(json.loads(resp.read())['price'])
            except: pass

        closed_ids = []
        for sym_full, pos in positions.items():
            px = prices.get(sym_full, 0)
            if not px: continue
            direction = pos.get('direction', 'LONG')
            sl = pos.get('sl', 0); tp1 = pos.get('tp1', 0)
            entry = pos.get('entry_price', px)

            close_reason = None
            exit_px = px
            if direction == 'LONG':
                if sl > 0 and px <= sl:
                    close_reason = '🔴SL止损'; exit_px = sl
                elif tp1 > 0 and px >= tp1:
                    close_reason = '🟢TP1止盈'; exit_px = tp1
            else:  # SHORT
                if sl > 0 and px >= sl:
                    close_reason = '🔴SL止损'; exit_px = sl
                elif tp1 > 0 and px <= tp1:
                    close_reason = '🟢TP1止盈'; exit_px = tp1

            if close_reason:
                result = _close_position(data, sym_full, pos, exit_px, close_reason)
                if result:
                    triggered.append(result)
                    closed_ids.append(sym_full)

        # 移除已平仓
        for sid in closed_ids:
            positions.pop(sid, None)

        if closed_ids:
            data['positions'] = positions
            json.dump(data, open(pf, 'w'), ensure_ascii=False, indent=2)
            print(f'[sl_tp] 平仓{len(closed_ids)}笔: {closed_ids}')

    except Exception as e:
        print(f'[sl_tp] 检查失败: {e}')

    return triggered

def _close_position(data: dict, sym_full: str, pos: dict,
                    exit_px: float, reason: str) -> dict:
    """执行平仓，更新账本"""
    direction = pos.get('direction', 'LONG')
    entry = pos.get('entry_price', exit_px)
    qty = pos.get('qty', 0)
    leverage = pos.get('leverage', 3)
    nav_pct = pos.get('nav_pct', 1.5)
    notional = PAPER_NAV * nav_pct / 100 * leverage

    if direction == 'LONG':
        gross_pnl = (exit_px - entry) / entry * notional
    else:
        gross_pnl = (entry - exit_px) / entry * notional

    fee = notional * 0.0004
    net_pnl = round(gross_pnl - fee * 2, 4)

    closed_rec = dict(pos,
        status='CLOSED', exit_price=exit_px, exit_ts=int(time.time()),
        gross_pnl=round(gross_pnl, 4), net_pnl=net_pnl,
        close_reason=reason, pnl=net_pnl)

    data.setdefault('closed', []).append(closed_rec)
    data['realized_pnl'] = round(data.get('realized_pnl', 0) + net_pnl, 4)
    stats = data.setdefault('stats', {'total':0,'win':0,'loss':0,'pnl':0})
    stats['total'] += 1
    stats['win' if net_pnl > 0 else 'loss'] += 1
    stats['pnl'] = round(stats.get('pnl', 0) + net_pnl, 4)

    # 推送平仓通知
    try:
        import importlib.util, pathlib
        spec = importlib.util.spec_from_file_location(
            'push_hub', pathlib.Path(__file__).parent / 'push_hub.py')
        ph = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ph)
        ph.push_jarvis(
            f"{reason} {sym_full} {direction}\n"
            f"入场${entry:,.3f} → 出场${exit_px:,.3f}\n"
            f"PnL={net_pnl:+.2f}U  累计={data['realized_pnl']:+.2f}U",
            priority='P1', dedup_key=f'close_{sym_full}_{int(time.time())}', dedup_ttl=60)
    except: pass

    return closed_rec

def get_portfolio_summary() -> str:
    """持仓摘要，供daily_postmortem调用"""
    pf = BASE / 'data' / 'paper_positions.json'
    if not pf.exists(): return '无模拟账本'
    data = json.load(open(pf))
    pos = data.get('positions', {})
    if isinstance(pos, list): pos = {}
    stats = data.get('stats', {})
    lines = [
        f'📊 模拟账本摘要 (NAV={PAPER_NAV:,.0f}U)',
        f'开仓={len(pos)}笔  总交易={stats.get("total",0)}  '
        f'胜={stats.get("win",0)} 负={stats.get("loss",0)}',
        f'已实现PnL={data.get("realized_pnl",0):+.2f}U',
    ]
    for sym_full, p in list(pos.items())[:6]:
        lines.append(f'  {sym_full} {p.get("direction","?")} @${p.get("entry_price",0):,.3f} SL=${p.get("sl",0):,.3f}')
    return '\n'.join(lines)

if __name__ == '__main__':
    # 快速验证
    print(get_portfolio_summary())
    print('\n[check_sl_tp] 检查所有持仓...')
    triggered = check_sl_tp_all()
    print(f'触发平仓: {len(triggered)}笔')
