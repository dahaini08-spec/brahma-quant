#!/usr/bin/env python3
"""
wuqu_signal_notifier.py — 武曲信号开单通知脚本 v1.1
设计院 · 2026-06-14

功能：
  1. 扫描 live_signal_log.jsonl 中新增的干净信号（_data_quality=None）
  2. 对比 last_notified_id 状态文件，只推送未通知过的新信号
  3. 输出到 stdout（Cron转发到 Jarvis 对话框）
  4. 不依赖 AI，不推送钉钉，纯脚本模式

用法：
  python3 scripts/wuqu_signal_notifier.py          # 正常输出
  python3 scripts/wuqu_signal_notifier.py --dry    # 空运行
  python3 scripts/wuqu_signal_notifier.py --stats  # 统计状态
"""

from brahma_brain.math_utils import _ema, _rsi, calc_rsi, rsi, ema  # 统一数学库

import sys, os, json, time, argparse, hashlib, requests
from pathlib import Path
from datetime import datetime, timezone

BASE    = Path(__file__).parent.parent
LOG_PATH   = BASE / 'data' / 'live_signal_log.jsonl'
STATE_PATH = BASE / 'data' / 'wuqu_notifier_state.json'

sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'brahma_brain'))

# ── 信号指纹（去重）───────────────────────────────────────────────────────────────────
def _fingerprint(r: dict) -> str:
    key = f'{r.get("symbol")}_{r.get("direction")}_{r.get("entry_price", r.get("entry_lo", 0)):.2f}_{r.get("score", 0):.0f}'
    return hashlib.md5(key.encode()).hexdigest()[:12]


# ── 加载/保存状态 ────────────────────────────────────────────────────────────────────
def _load_state() -> dict:
    try:
        if STATE_PATH.exists():
            with open(STATE_PATH) as f:
                return json.load(f)
    except Exception:
        pass
    return {'notified': [], 'last_run': 0}

def _save_state(state: dict) -> None:
    with open(STATE_PATH, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ── 实时 RSI ──────────────────────────────────────────────────────────────────────
def _rsi(sym: str) -> tuple:
    def _calc(c, n=14):
        d = [c[i]-c[i-1] for i in range(1, len(c))]
        g = [max(x,0) for x in d]; lo = [abs(min(x,0)) for x in d]
        ag = sum(g[:n])/n; al = sum(lo[:n])/n
        for i in range(n, len(g)):
            ag = (ag*(n-1)+g[i])/n; al = (al*(n-1)+lo[i])/n
        return 100-100/(1+ag/max(al, 1e-9))
    try:
        def _kl(tf):
            r = requests.get('https://fapi.binance.com/fapi/v1/klines',
                params={'symbol': sym, 'interval': tf, 'limit': 50}, timeout=5)
            return [float(x[4]) for x in r.json()]
        return _calc(_kl('1h')), _calc(_kl('4h')), _calc(_kl('1d'))
    except Exception:
        return 50.0, 50.0, 50.0

def _get_price(sym: str) -> float:
    try:
        r = requests.get('https://fapi.binance.com/fapi/v1/ticker/price',
            params={'symbol': sym}, timeout=5)
        return float(r.json()['price'])
    except Exception:
        return 0.0


# ── 信号格式化（对话框标准格式） ────────────────────────────────────────────────
def _format_signal(r: dict, price: float, r1h: float, r4h: float, r1d: float) -> str:
    sym       = r.get('symbol', '?')
    d         = r.get('direction', '?')
    entry     = r.get('entry_price', r.get('entry_lo', 0))
    sl        = r.get('stop_loss', r.get('sl', 0))
    tp1       = r.get('tp1', 0)
    tp2       = r.get('tp2', 0)
    score     = r.get('score', 0)
    grade     = r.get('structure_grade', r.get('grade', 0))
    regime    = r.get('regime', '-')
    signal_id = r.get('signal_id', '-')

    REGIME_CN = {
        'BULL_TREND': '牛市趋势', 'BULL_EARLY': '牛市初期',
        'BULL_CORRECTION': '牛市回调', 'BEAR_TREND': '熊市趋势',
        'BEAR_EARLY': '熊市初期', 'BEAR_RECOVERY': '熊市反弹',
        'CHOP': '震荡', 'CHOP_HIGH': '高位震荡', 'CHOP_LOW': '低位震荡',
    }
    regime_cn = REGIME_CN.get(regime, regime)

    # 方向emoji
    arrow = '▼' if d == 'SHORT' else '▲'
    dir_cn = '空' if d == 'SHORT' else '多'
    sym_short = sym.replace('USDT', '').replace('1000', '1000')

    # RR计算
    if entry > 0 and sl > 0 and tp1 > 0:
        if d == 'SHORT':
            risk = sl - entry
            rr1  = (entry - tp1) / max(risk, 1e-9)
            sl_pct = (sl - entry) / entry * 100
            tp1_pct = (entry - tp1) / entry * 100
        else:
            risk = entry - sl
            rr1  = (tp1 - entry) / max(risk, 1e-9)
            sl_pct = (entry - sl) / entry * 100
            tp1_pct = (tp1 - entry) / entry * 100
    else:
        rr1 = sl_pct = tp1_pct = 0

    # 入场距离标注
    if price > 0 and entry > 0:
        dist = abs(price - entry) / entry * 100
        if dist < 0.5:
            near_tag = '✅现价附近'
        elif dist < 2.0:
            near_tag = f'⌛等待 距{dist:.1f}%'
        else:
            near_tag = f'⭐挂单埋伏 距{dist:.1f}%'
    else:
        near_tag = ''

    def fp(v):
        if v == 0: return '-'
        if v >= 1000: return f'${v:,.0f}'
        elif v >= 10: return f'${v:.2f}'
        else: return f'${v:.4f}'

    lines = [
        f'📡 武曲信号 {arrow} {sym_short} {dir_cn}单',
        f'🆔 {signal_id}',
        f'体制: {regime_cn}  Score={score:.0f}  Grade={grade}',
        f'现价: {fp(price)}  {near_tag}',
        f'入场: {fp(entry)}',
        f'止损: {fp(sl)}  (+{sl_pct:.2f}%)',
        f'TP1 : {fp(tp1)}  (-{tp1_pct:.2f}%)  RR={rr1:.1f}x',
    ]
    if tp2 and tp2 != tp1:
        if d == 'SHORT':
            rr2 = (entry - tp2) / max(sl - entry, 1e-9)
        else:
            rr2 = (tp2 - entry) / max(entry - sl, 1e-9)
        lines.append(f'TP2 : {fp(tp2)}  RR={rr2:.1f}x')
    lines.append(f'RSI : 1H={r1h:.0f}  4H={r4h:.0f}  1D={r1d:.0f}')
    return '\n'.join(lines)


# ── 主流程 ──────────────────────────────────────────────────────────────────────────────
def run(dry_run: bool = False, stats_only: bool = False):
    state = _load_state()
    notified_set = set(state.get('notified', []))

    if not LOG_PATH.exists():
        pass  # [静默]
        return

    with open(LOG_PATH) as f:
        records = [json.loads(l) for l in f if l.strip()]

    # 过滤：干净 + 未结算 + grade≥70 + score≥138
    clean_open = [
        r for r in records
        if r.get('_data_quality') is None
        and r.get('outcome') not in ('WIN', 'LOSS', 'TIMEOUT')
        and r.get('structure_grade', r.get('grade', 0)) >= 70
        and r.get('score', 0) >= 138
    ]

    if stats_only:
        new_count = sum(1 for r in clean_open if _fingerprint(r) not in notified_set)
        print(f'武曲通知状态: 待通知={new_count}个 / 干净持仓={len(clean_open)}个 / 已历史通知={len(notified_set)}个')
        for r in clean_open:
            fp = _fingerprint(r)
            tag = '✅' if fp in notified_set else '🔔'
            sym=r.get('symbol','?'); d=r.get('direction','?')
            score=r.get('score',0); grade=r.get('structure_grade',r.get('grade','?'))
            print(f'  {tag} {sym} {d} score={score:.0f} grade={grade}')
        return

    # 找出新信号
    new_signals = [r for r in clean_open if _fingerprint(r) not in notified_set]

    if not new_signals:
        pass  # [静默]
        return

    # 输出到 stdout（Cron 会把这些内容转发到对话框）
    output_lines = [f'📡 武曲新信号 {len(new_signals)}个']

    for r in new_signals:
        sym = r.get('symbol', '?')
        r1h, r4h, r1d = _rsi(sym)
        price = _get_price(sym)

        msg = _format_signal(r, price, r1h, r4h, r1d)
        output_lines.append('')
        output_lines.append(msg)
        output_lines.append('─'*30)

        fp = _fingerprint(r)
        notified_set.add(fp)

    print('\n'.join(output_lines))

    # 保存状态
    if not dry_run:
        state['notified'] = list(notified_set)[-500:]
        state['last_run'] = time.time()
        state['last_pushed'] = len(new_signals)
        _save_state(state)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry',   action='store_true')
    parser.add_argument('--stats', action='store_true')
    args = parser.parse_args()
    run(dry_run=args.dry, stats_only=args.stats)

import sys, os, json, time, argparse, hashlib, requests
from pathlib import Path
from datetime import datetime, timezone

# ── brahma_bus 总线接入（设计院 2026-06-29）──
try:
    from brahma_brain.brahma_bus import bus as _brahma_bus
except Exception:
    _brahma_bus = None

def _bus_price(symbol: str) -> float:
    """统一价格获取：优先走 brahma_bus 缓存，降级走直接HTTP"""
    if _brahma_bus:
        return _brahma_bus.price(symbol)
    import requests
    r = requests.get(f'https://fapi.binance.com/fapi/v1/ticker/price',
                     params={'symbol': symbol}, timeout=5)
    return float(r.json()['price'])



BASE    = Path(__file__).parent.parent
LOG_PATH   = BASE / 'data' / 'live_signal_log.jsonl'
STATE_PATH = BASE / 'data' / 'wuqu_notifier_state.json'

sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'brahma_brain'))

# ── 信号指纹（去重）─────────────────────────────────────────────────
def _fingerprint(r: dict) -> str:
    key = f'{r.get("symbol")}_{r.get("direction")}_{r.get("entry_price", r.get("entry_lo", 0)):.2f}_{r.get("score", 0):.0f}'
    return hashlib.md5(key.encode()).hexdigest()[:12]


# ── 加载/保存状态 ────────────────────────────────────────────────────
def _load_state() -> dict:
    try:
        if STATE_PATH.exists():
            with open(STATE_PATH) as f:
                return json.load(f)
    except Exception:
        pass
    return {'notified': [], 'last_run': 0}

def _save_state(state: dict) -> None:
    with open(STATE_PATH, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ── 实时 RSI ────────────────────────────────────────────────────────
def _rsi(sym: str) -> tuple:
    """返回 (rsi_1h, rsi_4h, rsi_1d)，失败返回 (50,50,50)"""
    def _calc(c, n=14):
        d = [c[i]-c[i-1] for i in range(1, len(c))]
        g = [max(x,0) for x in d]; lo = [abs(min(x,0)) for x in d]
        ag = sum(g[:n])/n; al = sum(lo[:n])/n
        for i in range(n, len(g)):
            ag = (ag*(n-1)+g[i])/n; al = (al*(n-1)+lo[i])/n
        return 100-100/(1+ag/max(al, 1e-9))
    try:
        def _kl(tf):
            r = requests.get('https://fapi.binance.com/fapi/v1/klines',
                params={'symbol': sym, 'interval': tf, 'limit': 50}, timeout=5)
            return [float(x[4]) for x in r.json()]
        return _calc(_kl('1h')), _calc(_kl('4h')), _calc(_kl('1d'))
    except Exception:
        return 50.0, 50.0, 50.0


# ── 格式化推送 ───────────────────────────────────────────────────────
def _push_signal(r: dict, dry_run: bool = False) -> bool:
    sym      = r.get('symbol', 'UNKNOWN')
    d        = r.get('direction', 'SHORT')
    entry    = r.get('entry_price', r.get('entry_lo', 0))
    entry_lo = r.get('entry_lo', entry * 0.998)
    entry_hi = r.get('entry_hi', entry * 1.002)
    sl       = r.get('stop_loss', r.get('sl', 0))
    tp1      = r.get('tp1', r.get('tp_price', 0))
    tp2      = r.get('tp2', 0)
    score    = r.get('score', 0)
    grade    = r.get('structure_grade', r.get('grade', 0))
    regime   = r.get('regime', '-')
    signal_id = r.get('signal_id', '-')

    # 安全检查
    if entry <= 0 or sl <= 0 or tp1 <= 0:
        pass  # [静默]
        return False

    # [2026-07-08 设计院封印] 推送前实时价格偏离检查
    # 信号有效窗口=入场区±3%，超出则信号过期，不推送
    try:
        import requests as _req
        _pr = _req.get(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}', timeout=5)
        _cur = float(_pr.json().get('price', 0))
        if _cur > 0 and entry_lo > 0 and entry_hi > 0:
            _gap_long  = (_cur - entry_hi) / entry_hi  if d == 'LONG'  else 0
            _gap_short = (entry_lo - _cur) / entry_lo  if d == 'SHORT' else 0
            _gap = max(_gap_long, _gap_short)
            if _gap > 0.03:  # 偏离入场区>3% → 信号窗口已失效
                pass  # [静默]
                return False
    except Exception:
        pass  # 价格检查失败不阻断推送（保守策略）

    # RR计算
    if d == 'SHORT':
        risk   = sl - entry
        rr1    = (entry - tp1) / max(risk, 1e-9)
        rr2    = (entry - tp2) / max(risk, 1e-9) if tp2 else 0
        sl_pct = (sl - entry) / entry * 100
        tp1_pct = (entry - tp1) / entry * 100
    else:
        risk   = entry - sl
        rr1    = (tp1 - entry) / max(risk, 1e-9)
        rr2    = (tp2 - entry) / max(risk, 1e-9) if tp2 else 0
        sl_pct = (entry - sl) / entry * 100
        tp1_pct = (tp1 - entry) / entry * 100

    # RSI
    r1h, r4h, r1d = _rsi(sym)

    # 体制中文映射
    REGIME_CN = {
        'BULL_TREND': '牛市趋势', 'BULL_EARLY': '牛市初期',
        'BULL_CORRECTION': '牛市回调', 'BEAR_TREND': '熊市趋势',
        'BEAR_EARLY': '熊市初期', 'BEAR_RECOVERY': '熊市反弹',
        'CHOP': '震荡', 'CHOP_HIGH': '高位震荡', 'CHOP_LOW': '低位震荡',
    }
    regime_cn = REGIME_CN.get(regime, regime)

    pass  # [静默]

    if dry_run:
        pass  # [静默]
        return True

    try:
        from push_hub import send_strategy_dd1
        ok = send_strategy_dd1(
            symbol=sym, direction=d, price=entry,
            entry_lo=entry_lo, entry_hi=entry_hi,
            stop_loss=sl, tp1=tp1, tp2=tp2 or tp1,
            rsi_1h=r1h, rsi_4h=r4h, rsi_1d=r1d,
            regime=regime, regime_cn=regime_cn,
            score=score, structure_grade=grade,
            signal_id=signal_id,
            sl_pct=sl_pct, tp1_pct=tp1_pct,
            rr1=rr1, rr2=rr2,
            near_tag='✅现价附近',
        )
        return ok
    except Exception as e:
        pass  # [静默]
        return False


# ── 主流程 ───────────────────────────────────────────────────────────
def run(dry_run: bool = False, stats_only: bool = False):
    state = _load_state()
    notified_set = set(state.get('notified', []))

    # 读取信号日志
    if not LOG_PATH.exists():
        pass  # [静默]
        return

    with open(LOG_PATH) as f:
        records = [json.loads(l) for l in f if l.strip()]

    # 过滤：干净 + 未结算 + grade≥70
    clean_open = [
        r for r in records
        if r.get('_data_quality') is None
        and r.get('outcome') not in ('WIN', 'LOSS', 'TIMEOUT')
        and r.get('structure_grade', r.get('grade', 0)) >= 70
        and r.get('score', 0) >= 138
    ]

    if stats_only:
        pass  # [静默]
        print(f'  总记录: {len(records)}')
        print(f'  干净未结算(grade≥70,score≥138): {len(clean_open)}')
        print(f'  已通知: {len(notified_set)}')
        new_count = sum(1 for r in clean_open if _fingerprint(r) not in notified_set)
        print(f'  待通知: {new_count}')
        for r in clean_open:
            fp = _fingerprint(r)
            sym = r.get('symbol','?'); d = r.get('direction','?')
            score = r.get('score',0); grade = r.get('structure_grade',r.get('grade','?'))
            tag = '✅已通知' if fp in notified_set else '🆕待通知'
            print(f'  {tag} {sym} {d} score={score:.0f} grade={grade} fp={fp}')
        return

    # 推送新信号
    pushed = 0
    failed = 0
    for r in clean_open:
        fp = _fingerprint(r)
        if fp in notified_set:
            continue  # 已通知，跳过

        ok = _push_signal(r, dry_run=dry_run)
        if ok:
            notified_set.add(fp)
            pushed += 1
            time.sleep(1)  # 避免频率限制
        else:
            failed += 1

    # 保存状态（保留最近500条指纹，避免文件无限增长）
    state['notified'] = list(notified_set)[-500:]
    state['last_run'] = time.time()
    state['last_pushed'] = pushed
    if not dry_run:
        _save_state(state)

    pass  # [静默]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='武曲信号开单通知脚本')
    parser.add_argument('--dry',   action='store_true', help='空运行，只打印不推送')
    parser.add_argument('--stats', action='store_true', help='统计当前状态')
    args = parser.parse_args()
    run(dry_run=args.dry, stats_only=args.stats)
