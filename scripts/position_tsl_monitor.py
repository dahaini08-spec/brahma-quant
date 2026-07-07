"""
position_tsl_monitor.py — 追踪止损 + 关键压力位动态管理模块 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
设计院 × 六方联合 · 2026-06-25

修复三大系统性缺陷（BTW案例铁证）：
  ① 方案B：追踪止损TSL（价格到TP1自动锁利）
  ② 方案D：关键压力位动态反应（上涨/下跌乏力提前反应）
  ③ 分批止盈（CHOP/BEAR_RECOVERY体制TP1平50%，剩余追踪）

TSL阶梯规则：
  价格到达 entry→TP1 行程的 30% → 止损移至保本（break-even）
  价格到达 entry→TP1 行程的 60% → 止损移至 entry + risk×0.5（锁利+50%）
  价格到达 TP1                   → 止损移至 TP1（完全锁利）
  价格超过 TP1 继续延伸          → 止损追踪至 TP1 + ATR×0.5

关键压力位反应：
  - 4H收阴线 + 量能萎缩 + RSI出现顶背离 → 触发"乏力预警"，止损提前收紧
  - 连续2根反向K线 → 触发"反转预警"，分批止盈50%
  - TIMEOUT前4小时 + 浮盈>0 → 强制止盈离场，不等超时亏损

苏摩合规：
  - 离线脚本，不产生AI cron任务
  - 通过现有 binance_fapi.py 下单（已验证）
  - 状态持久化：data/tsl_state.json
  - 每次运行幂等，可重复执行

用法（集成到position_sl_monitor.py或独立cron）：
  python3 scripts/position_tsl_monitor.py --dry-run  # 测试
  python3 scripts/position_tsl_monitor.py            # 实盘
"""

import sys, os, json, time, argparse
import urllib.request
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



BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

DATA_DIR  = BASE / 'data'
TSL_STATE = DATA_DIR / 'tsl_state.json'
LOG_FILE  = BASE / 'logs' / 'position_tsl.log'
LOG_FILE.parent.mkdir(exist_ok=True)

# ── 日志 ─────────────────────────────────────────────────────────
def log(msg, level='INFO'):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] [{level}] {msg}'
    print(line, flush=True)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')


# ── 获取当前持仓 ──────────────────────────────────────────────────
def get_positions():
    """读取真实持仓（修复2026-07-04）:
    优先读 wuqu_positions.json（Binance实盘同步，唯一真相），
    brahma_state.positions 已证实包含幽灵数据，禁止再用。
    """
    wuqu_path = DATA_DIR / 'wuqu_positions.json'
    try:
        raw = json.load(open(wuqu_path))
        positions = []
        for p in raw:
            # 标准化字段名（wuqu用side/entry_price，TSL内部用direction/entry_price）
            pos = dict(p)
            pos['direction'] = pos.get('direction') or pos.get('side', '')
            pos['entry_price'] = float(pos.get('entry_price') or pos.get('entry', 0))
            pos['stop_loss'] = float(pos.get('stop_loss') or pos.get('sl_price', 0))
            pos['tp1'] = float(pos.get('take_profit') or pos.get('tp1') or pos.get('tp1_price', 0))
            pos['status'] = 'OPEN'
            positions.append(pos)
        return positions
    except Exception as e:
        log(f'读取wuqu_positions失败: {e}，降级尝试brahma_state', 'WARN')
        try:
            state = json.load(open(DATA_DIR / 'brahma_state.json'))
            return [p for p in state.get('positions', []) if p.get('status') == 'OPEN']
        except Exception as e2:
            log(f'读取brahma_state也失败: {e2}', 'ERROR')
            return []


def get_sl_state():
    if TSL_STATE.exists():
        return json.load(open(TSL_STATE))
    return {}


def save_sl_state(state: dict):
    TSL_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


# ── 获取实时价格 + K线 ────────────────────────────────────────────
def get_mark_price(symbol: str) -> float:
    try:
        url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}'
        r = json.loads(urllib.request.urlopen(url, timeout=5).read())
        return float(r['price'])
    except:
        return 0.0


def get_recent_klines(symbol: str, interval='1h', limit=10) -> list:
    try:
        url = f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}'
        raw = json.loads(urllib.request.urlopen(url, timeout=8).read())
        return [[float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])] for c in raw]
    except:
        return []


# ── 技术指标 ─────────────────────────────────────────────────────
def calc_rsi(closes, period=14):
    import numpy as np
    c = np.array(closes)
    d = np.diff(c, prepend=c[0])
    g = np.where(d > 0, d, 0.); l = np.where(d < 0, -d, 0.)
    ag = g[:period+1].mean(); al = l[:period+1].mean()
    for i in range(period+1, len(c)):
        ag = (ag*(period-1)+g[i])/period
        al = (al*(period-1)+l[i])/period
    return 100 - 100/(1 + ag/(al+1e-10)) if al > 0 else 100.0


def detect_exhaustion(klines: list, direction: str) -> dict:
    """
    检测上涨/下跌乏力信号
    返回：{exhausted: bool, reason: str, confidence: float}
    """
    if len(klines) < 5:
        return {'exhausted': False, 'reason': 'insufficient_data', 'confidence': 0}

    closes = [c[3] for c in klines]
    highs  = [c[0] for c in klines]
    lows   = [c[1] for c in klines]
    vols   = [c[4] for c in klines]

    signals = []
    confidence = 0.0

    # 1. 量能萎缩（最近2根量 < 前3根均值×0.6）
    recent_vol  = sum(vols[-2:]) / 2
    hist_vol    = sum(vols[-5:-2]) / 3
    if hist_vol > 0 and recent_vol < hist_vol * 0.6:
        signals.append('量能萎缩')
        confidence += 0.3

    # 2. K线方向反转（最近2根与方向相反）
    if direction == 'LONG':
        reverse_count = sum(1 for i in range(-3, 0) if closes[i] < closes[i-1])
    else:
        reverse_count = sum(1 for i in range(-3, 0) if closes[i] > closes[i-1])
    if reverse_count >= 2:
        signals.append(f'连续{reverse_count}根反向K线')
        confidence += 0.4

    # 3. RSI背离（价格新高/低但RSI未创新高/低）
    rsi_vals = []
    for i in range(max(0, len(closes)-10), len(closes)):
        rsi_vals.append(calc_rsi(closes[max(0,i-14):i+1]))

    if len(rsi_vals) >= 4:
        if direction == 'LONG':
            price_new_high = closes[-1] > max(closes[-5:-1])
            rsi_not_new_high = rsi_vals[-1] < max(rsi_vals[-4:-1]) - 2
            if price_new_high and rsi_not_new_high:
                signals.append('RSI顶部背离')
                confidence += 0.35
        else:
            price_new_low = closes[-1] < min(closes[-5:-1])
            rsi_not_new_low = rsi_vals[-1] > min(rsi_vals[-4:-1]) + 2
            if price_new_low and rsi_not_new_low:
                signals.append('RSI底部背离')
                confidence += 0.35

    exhausted = confidence >= 0.4 and len(signals) >= 1

    return {
        'exhausted':  exhausted,
        'reason':     ' + '.join(signals) if signals else 'no_signal',
        'confidence': round(confidence, 2),
    }


# ── 修改止损（通过binance_fapi）────────────────────────────────────
def update_stop_loss(symbol: str, new_sl: float, position_side: str,
                     dry_run: bool = True) -> bool:
    """
    更新止损。
    账户为Portfolio Margin，STOP_MARKET需要Algo API。
    当前策略：直接更新 position_sl_state.json，
    ws_guardian每循环读取最新sl_price执行软止损。
    """
    if dry_run:
        log(f'  [DRY-RUN] {symbol} 更新止损 → {new_sl:.6g}', 'TSL')
        return True
    try:
        sl_state_path = DATA_DIR / 'position_sl_state.json'
        sl_state = json.loads(sl_state_path.read_text()) if sl_state_path.exists() else {}

        # 更新软止损状态（ws_guardian实时监控）
        if symbol in sl_state:
            old_sl = sl_state[symbol].get('sl_price', 0)
            sl_state[symbol]['sl_price']    = new_sl
            sl_state[symbol]['tsl_updated'] = datetime.now(timezone.utc).isoformat()
            sl_state[symbol]['tsl_old_sl']  = old_sl
            sl_state_path.write_text(json.dumps(sl_state, ensure_ascii=False, indent=2))
            log(f'  ✅ {symbol} 软止损已更新 {old_sl:.6g} → {new_sl:.6g} (ws_guardian持仓监控将生效)', 'TSL')
            # P0推送：止损移动通知
            import subprocess as _sp
            # [BUG-2 修复 2026-07-07] JARVIS_CHANNEL在旧system_config中不存在时就地定义
            try:
                from system_config import JARVIS_TARGET as _jt, JARVIS_CHANNEL as _jc
            except ImportError:
                from system_config import JARVIS_TARGET as _jt
                _jc = 'jarvis'
            _msg = f'🔒 TSL移动 {symbol}\n止损: {old_sl:.4f} → {new_sl:.4f}\n(ws_guardian软止损已更新)'
            _sp.Popen(['openclaw','message','send','--channel',_jc,'--to',_jt,'--message',_msg])
        else:
            # 新增软止损记录
            sl_state[symbol] = {
                'symbol':       symbol,
                'direction':    position_side,
                'sl_price':     new_sl,
                'tsl_updated':  datetime.now(timezone.utc).isoformat(),
            }
            sl_state_path.write_text(json.dumps(sl_state, ensure_ascii=False, indent=2))
            log(f'  ✅ {symbol} 软止损新增 → {new_sl:.6g}', 'TSL')
        return True
    except Exception as e:
        log(f'  ❌ 更新止损失败: {e}', 'ERROR')
        return False


def partial_close(symbol: str, size_pct: float, direction: str,
                  reason: str, dry_run: bool = True) -> bool:
    """分批止盈：按比例平仓"""
    if dry_run:
        log(f'  [DRY-RUN] {symbol} 分批止盈 {size_pct*100:.0f}% ({reason})', 'TSL')
        return True
    try:
        _scripts = str(BASE / 'scripts')
        if _scripts not in sys.path:
            sys.path.insert(0, _scripts)
        import binance_fapi as _fapi

        pos_raw, _ = _fapi.get_positions(symbol)
        qty = 0.0
        if pos_raw:
            for p in pos_raw:
                if p.get('symbol') == symbol:
                    qty = abs(float(p.get('positionAmt', 0)))
                    break
        if qty <= 0:
            return False

        from hunter_sizer import get_symbol_info as get_precision
        prec = get_precision(symbol)
        qty_prec = prec.get('qty_precision', 3)
        close_qty = round(qty * size_pct, qty_prec)
        if close_qty <= 0:
            return False

        side = 'SELL' if direction == 'LONG' else 'BUY'
        result, err = _fapi.place_order(
            symbol=symbol, side=side,
            order_type='MARKET', qty=close_qty,
            reduce_only=True, qty_precision=qty_prec,
        )
        if err:
            log(f'  ❌ 分批止盈失败: {err}', 'ERROR')
            return False
        log(f'  ✅ {symbol} 分批止盈 {close_qty}张 ({reason}) orderId={result.get("orderId","?")}', 'TSL')
        return True
    except Exception as e:
        log(f'  ❌ 分批止盈失败: {e}', 'ERROR')
        return False


# ── 核心：单个持仓TSL检查 ─────────────────────────────────────────
def check_position_tsl(pos: dict, tsl_state: dict,
                       dry_run: bool = True) -> dict:
    """
    对单个持仓执行TSL逻辑
    返回更新后的tsl_state[symbol]
    """
    symbol    = pos.get('symbol', '')
    direction = pos.get('direction', '')
    entry     = float(pos.get('entry_price') or pos.get('entry', 0))
    sl_orig   = float(pos.get('stop_loss') or pos.get('sl_price', 0))
    tp1       = float(pos.get('tp1') or pos.get('tp1_price', 0))
    tp2       = float(pos.get('tp2') or pos.get('tp2_price', 0))
    open_ts   = pos.get('open_ts', '')

    if not all([symbol, direction, entry > 0, sl_orig > 0, tp1 > 0]):
        return tsl_state.get(symbol, {})

    cur_price = get_mark_price(symbol)
    if cur_price <= 0:
        return tsl_state.get(symbol, {})

    # 读取该持仓的TSL状态
    st = tsl_state.get(symbol, {
        'symbol':       symbol,
        'entry':        entry,
        'sl_orig':      sl_orig,
        'sl_current':   sl_orig,
        'tp1':          tp1,
        'tp2':          tp2,
        'direction':    direction,
        'tsl_stage':    0,           # 0=初始 1=保本 2=锁利50% 3=锁利TP1 4=超额追踪
        'partial_done': False,       # 是否已分批止盈50%
        'last_update':  '',
        'actions_log':  [],
    })

    risk     = abs(entry - sl_orig)
    progress = 0.0  # 当前价格在entry→TP1的行程比例

    if direction == 'LONG':
        if tp1 > entry:
            progress = (cur_price - entry) / (tp1 - entry)
    else:
        if tp1 < entry:
            progress = (entry - cur_price) / (entry - tp1)

    actions = []

    # ── TSL 阶梯逻辑 ─────────────────────────────────────────────
    cur_stage = st.get('tsl_stage', 0)
    sl_current = float(st.get('sl_current', sl_orig))
    new_sl = sl_current

    # Stage 1：行程≥30% → 保本
    if cur_stage < 1 and progress >= 0.30:
        new_sl = entry * 1.001 if direction == 'LONG' else entry * 0.999
        if ((direction == 'LONG' and new_sl > sl_current) or
                (direction == 'SHORT' and new_sl < sl_current)):
            cur_stage = 1
            actions.append(f'TSL Stage1: 行程{progress*100:.0f}% → 止损移至保本{new_sl:.6g}')
            log(f'  🔐 {symbol} {direction} Stage1 保本: {sl_current:.6g} → {new_sl:.6g}', 'TSL')

    # Stage 2：行程≥60% → 锁利50%风险
    if cur_stage < 2 and progress >= 0.60:
        if direction == 'LONG':
            new_sl = entry + risk * 0.5
        else:
            new_sl = entry - risk * 0.5
        if ((direction == 'LONG' and new_sl > sl_current) or
                (direction == 'SHORT' and new_sl < sl_current)):
            cur_stage = 2
            actions.append(f'TSL Stage2: 行程{progress*100:.0f}% → 锁利50% SL={new_sl:.6g}')
            log(f'  💰 {symbol} {direction} Stage2 锁利50%: {sl_current:.6g} → {new_sl:.6g}', 'TSL')

    # Stage 3：到达TP1 → 止损移至TP1
    if cur_stage < 3 and progress >= 1.0:
        new_sl = tp1
        cur_stage = 3
        actions.append(f'TSL Stage3: 到达TP1 → 止损移至TP1={tp1:.6g}')
        log(f'  🏆 {symbol} {direction} Stage3 TP1锁利: SL={tp1:.6g}', 'TSL')

    # Stage 4：超越TP1 → 追踪止损（ATR动态）
    if cur_stage >= 3 and tp2 > 0:
        kl_1h = get_recent_klines(symbol, '1h', 20)
        if kl_1h:
            import numpy as np
            arr = np.array(kl_1h)
            atr = float(np.mean(arr[-14:, 0] - arr[-14:, 1]))  # high-low近似ATR
            if direction == 'LONG':
                trail_sl = cur_price - atr * 1.5
                if trail_sl > new_sl:
                    new_sl = trail_sl
                    cur_stage = 4
                    actions.append(f'TSL Stage4: 追踪 SL={new_sl:.6g}(ATR={atr:.4g})')
            else:
                trail_sl = cur_price + atr * 1.5
                if trail_sl < new_sl:
                    new_sl = trail_sl
                    cur_stage = 4
                    actions.append(f'TSL Stage4: 追踪 SL={new_sl:.6g}(ATR={atr:.4g})')

    # 执行止损更新
    if new_sl != sl_current and actions:
        update_stop_loss(symbol, new_sl, direction, dry_run)
        st['sl_current'] = new_sl
        st['tsl_stage']  = cur_stage

    # ── 分批止盈逻辑（CHOP/BEAR_RECOVERY体制）─────────────────────
    regime = pos.get('regime', '')
    partial_regimes = ('CHOP_MID', 'CHOP_LOW', 'CHOP_HIGH', 'BEAR_RECOVERY', 'BULL_CORRECTION')
    if regime in partial_regimes and not st.get('partial_done') and progress >= 0.85:
        partial_close(symbol, 0.5, direction, f'体制{regime}到达TP1 85%分批', dry_run)
        st['partial_done'] = True
        actions.append(f'分批止盈50%: 体制{regime} 行程{progress*100:.0f}%')

    # ── 关键压力位反应（方案D）──────────────────────────────────────
    if progress >= 0.4 and not st.get('partial_done'):
        kl_1h = get_recent_klines(symbol, '1h', 10)
        if kl_1h:
            exh = detect_exhaustion(kl_1h, direction)
            if exh['exhausted'] and exh['confidence'] >= 0.6:
                import numpy as np
                arr = np.array(kl_1h)
                atr = float(np.mean(arr[-5:, 0] - arr[-5:, 1]))
                if direction == 'LONG':
                    tighten_sl = cur_price - atr * 0.8
                    # LONG止损必须低于当前价且高于原止损
                    if tighten_sl > sl_current and tighten_sl < cur_price * 0.998:
                        update_stop_loss(symbol, tighten_sl, direction, dry_run)
                        st['sl_current'] = tighten_sl
                        actions.append(f'乏力收紧SL: {exh["reason"]} conf={exh["confidence"]} SL={tighten_sl:.6g}')
                        log(f'  ⚡ {symbol} 乏力收紧SL: {exh["reason"]}', 'TSL')
                else:
                    tighten_sl = cur_price + atr * 0.8
                    # SHORT止损必须高于当前价且低于原止损
                    if tighten_sl < sl_current and tighten_sl > cur_price * 1.002:
                        update_stop_loss(symbol, tighten_sl, direction, dry_run)
                        st['sl_current'] = tighten_sl
                        actions.append(f'乏力收紧SL: {exh["reason"]} conf={exh["confidence"]} SL={tighten_sl:.6g}')
                        log(f'  ⚡ {symbol} 乏力收紧SL: {exh["reason"]}', 'TSL')

    # ── TIMEOUT前4小时浮盈保护 ──────────────────────────────────────
    if open_ts:
        try:
            from datetime import datetime, timezone, timedelta
            open_dt = datetime.fromisoformat(str(open_ts).replace('Z', '+00:00'))
            ttl_h = pos.get('ttl_hours', 48)
            expire_dt = open_dt + timedelta(hours=ttl_h)
            now_dt = datetime.now(timezone.utc)
            hours_left = (expire_dt - now_dt).total_seconds() / 3600
            float_pnl = (cur_price - entry) if direction == 'LONG' else (entry - cur_price)
            if hours_left <= 4 and float_pnl > 0:
                actions.append(f'TIMEOUT保护: {hours_left:.1f}h剩余，浮盈{float_pnl:.4g}，强制锁利')
                log(f'  ⏰ {symbol} TIMEOUT前{hours_left:.1f}h浮盈保护触发', 'TSL')
                partial_close(symbol, 1.0, direction, 'TIMEOUT浮盈保护', dry_run)
        except Exception:
            pass

    # 更新日志
    if actions:
        from datetime import datetime, timezone as _tz
        st['actions_log'] = (st.get('actions_log', []) + actions)[-20:]
        st['last_update'] = datetime.now(_tz.utc).isoformat()

    return st


# ── 主流程 ────────────────────────────────────────────────────────
def run(dry_run: bool = True):
    log(f'=== TSL Monitor 启动 | dry_run={dry_run} ===')

    positions = get_positions()
    if not positions:
        log('无持仓，退出')
        return

    tsl_state = get_sl_state()
    log(f'持仓数: {len(positions)}')

    for pos in positions:
        symbol = pos.get('symbol', '')
        direction = pos.get('direction', '')
        entry = float(pos.get('entry_price') or pos.get('entry', 0))
        sl = float(pos.get('stop_loss') or pos.get('sl_price', 0))
        tp1 = float(pos.get('tp1') or pos.get('tp1_price', 0))

        cur = get_mark_price(symbol)
        if cur <= 0:
            continue

        if direction == 'LONG':
            progress = (cur - entry) / max(tp1 - entry, 1e-9) if tp1 > entry else 0
            float_pnl = (cur - entry) / max(entry, 1e-9) * 100
        else:
            progress = (entry - cur) / max(entry - tp1, 1e-9) if tp1 < entry else 0
            float_pnl = (entry - cur) / max(entry, 1e-9) * 100

        log(f'  {symbol} {direction} 入场={entry:.4g} 当前={cur:.4g} '
            f'浮动={float_pnl:+.2f}% 行程={progress*100:.0f}% SL={sl:.4g} TP1={tp1:.4g}')

        st = check_position_tsl(pos, tsl_state, dry_run)
        tsl_state[symbol] = st

    save_sl_state(tsl_state)
    log('TSL状态已保存')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='TSL追踪止损监控')
    parser.add_argument('--dry-run', action='store_true', default=False)
    parser.add_argument('--live',    action='store_true')
    args = parser.parse_args()

    dry = not args.live
    run(dry_run=dry)
