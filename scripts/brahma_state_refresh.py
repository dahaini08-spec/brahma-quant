#!/usr/bin/env python3
"""
brahma_state_refresh.py — 梵天体制状态刷新 + 信号路由器
设计院封印 2026-09-03 苏摩111

接入位置：supercronic */30 * * * *
流程：
  1. analyze(BTCUSDT) + analyze(ETHUSDT) → brahma_state.json
  2. 对每个符合条件的标的调用 BrahmaDecisionEngine.decide()
  3. decide() 返回 EXECUTE/WAIT_15M → 写入 auto_signal_queue.json
  4. paper_executor.py（每40min）从 auto_signal_queue 读取并纸面开仓
  5. auto_executor.py（每40min，实盘开关由 LIVE_MODE 控制）

修复：之前 state_refresh 只存 brahma_state.json，
     signal_dir/entry_lo/entry_hi/sl_price 全 None，
     auto_signal_queue.json 根本不存在，导致执行链路完全空转。
"""
import sys, json, time
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

STATE_FILE        = BASE / 'data' / 'brahma_state.json'
SIGNAL_QUEUE_FILE = BASE / 'data' / 'auto_signal_queue.json'

# 纸面模式下的评分门槛（比实盘宽松，先验证再收紧）
PAPER_SCORE_MIN   = 80
# 信号有效期（小时）
SIGNAL_TTL_HOURS  = 4
# 分析标的列表（方向由体制自动决定）
SYMBOLS = ['BTCUSDT', 'ETHUSDT']

# ── 体制 → 推荐方向映射 ──────────────────────────────────────────
REGIME_DIRECTION = {
    'BULL_TREND':    'LONG',
    'BULL_EARLY':    'LONG',
    'BEAR_RECOVERY': 'LONG',
    'BEAR_TREND':    'SHORT',
    'BEAR_EARLY':    'SHORT',
    'CHOP_MID':      None,    # CHOP不推方向，decision_engine自行判断
    'CHOP_LOW':      None,
}

def clean(d, depth=0):
    if depth > 10: return str(d)
    if isinstance(d, dict):            return {k: clean(v, depth+1) for k,v in d.items()}
    if isinstance(d, (list, tuple)):   return [clean(i, depth+1) for i in d]
    if isinstance(d, (int, float, str, bool, type(None))): return d
    return str(d)

def _load_queue() -> list:
    if SIGNAL_QUEUE_FILE.exists():
        try:
            d = json.loads(SIGNAL_QUEUE_FILE.read_text())
            return d if isinstance(d, list) else d.get('signals', [])
        except Exception:
            pass
    return []

def _save_queue(signals: list):
    SIGNAL_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SIGNAL_QUEUE_FILE.write_text(json.dumps(signals, ensure_ascii=False, indent=2))

def _is_dead_combo(regime: str, direction: str) -> bool:
    """体制死穴检查（同 auto_executor 铁律）
    注意：CHOP_MID 不在死穴内，由 chop_breakout_detector 单独判断
    """
    DEAD = {
        ('BEAR_TREND',   'LONG'),    # 铁律封票1: 熊市做多
        ('BULL_TREND',   'SHORT'),   # 铁律封票2: 牛市做空
    }
    return (regime, direction) in DEAD

def main():
    now_ts = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()

    # ── Step 1: 加载已有队列，清理过期信号 ──────────────────────
    existing = _load_queue()
    active = []
    for s in existing:
        exp = s.get('expires_at', 0)
        if isinstance(exp, str):
            try:
                from datetime import datetime as _dt
                exp = _dt.fromisoformat(exp.replace('Z','+00:00')).timestamp()
            except Exception:
                exp = 0
        if exp > now_ts:
            active.append(s)
    expired_count = len(existing) - len(active)

    # ── Step 2: 分析 + 决策 ──────────────────────────────────────
    try:
        from brahma_bus import BrahmaEventBus
        BrahmaEventBus()
    except Exception:
        pass

    try:
        from brahma_core import analyze
    except Exception as e:
        print(f'[state_refresh] ❌ brahma_core导入失败: {e}')
        sys.exit(1)

    try:
        from brahma_brain.brahma_decision_engine import decide as _decide
        _decision_ok = True
    except Exception as e:
        print(f'[state_refresh] ⚠️  decision_engine导入失败: {e}，跳过信号生成')
        _decision_ok = False

    all_states = {}
    new_signals = []

    for sym in SYMBOLS:
        try:
            r = analyze(sym)
            cleaned = clean(r)
            all_states[sym] = cleaned

            regime    = cleaned.get('regime', '')
            score     = float(cleaned.get('score_final') or cleaned.get('score') or 0)
            grade_raw = cleaned.get('effective_grade') or cleaned.get('grade') or 100
            try:
                grade = float(str(grade_raw).split()[0])
            except Exception:
                grade = 100.0

            print(f'[state_refresh] {sym}: score={score:.1f} regime={regime} grade={grade:.0f}')

            # 评分不达纸面门槛，跳过
            # ⚠️ CHOP体制例外：由chop_breakout_detector独立判断，不走score门槛
            if score < PAPER_SCORE_MIN and 'CHOP' not in regime:
                print(f'[state_refresh] {sym}: score={score:.1f} < {PAPER_SCORE_MIN}，跳过信号生成')
                continue

            # 确定分析方向
            direction = REGIME_DIRECTION.get(regime)
            if direction is None:
                # CHOP体制：两个方向都尝试，让decision_engine自行决断
                directions_to_try = ['LONG', 'SHORT']
            else:
                directions_to_try = [direction]

            if not _decision_ok:
                continue

            for direction in directions_to_try:
                # 死穴直接跳过
                if _is_dead_combo(regime, direction):
                    print(f'[state_refresh] {sym} {direction}: 死穴 {regime}×{direction}，跳过')
                    continue

                # CHOP体制：走专属突破检测器
                if 'CHOP' in regime:
                    try:
                        from brahma_brain.chop_breakout_detector import detect_chop_breakout
                        chop_result = detect_chop_breakout(r, sym + 'USDT')
                        chop_signal = chop_result.get('signal', 'NONE')
                        if chop_signal == 'NONE':
                            print(f'[state_refresh] {sym} {direction}: CHOP条件不足({chop_result["score"]}/7)，跳过')
                            continue
                        elif chop_signal == 'WATCH':
                            print(f'[state_refresh] {sym} {direction}: CHOP_WATCH({chop_result["score"]}/7)，发预警不入场')
                            continue  # 观察阶段只推送不入场
                        # READY/EXECUTE：允许以小仓进入队列
                        print(f'[state_refresh] {sym} {direction}: CHOP_BREAKOUT_{chop_signal}({chop_result["score"]}/7)，解锁小仓')
                        # 覆盖nav_pct为CHOP限定仓位
                        _chop_nav_override = chop_result.get('nav_pct', 0.01)
                    except Exception as ce:
                        print(f'[state_refresh] {sym} {direction}: chop_breakout_detector失败: {ce}，保持封禁')
                        continue
                else:
                    _chop_nav_override = None

                # 构造 decision_engine 输入
                signal_in = {
                    'symbol':    sym,
                    'direction': direction,
                    'regime':    regime,
                    'score':     score,
                    'grade':     grade,
                    'price':     cleaned.get('price'),
                    'sl_pct':    2.0,   # 默认SL 2%，decision_engine会按ATR调整
                    'timing':    cleaned.get('timing', ''),
                    # 注入核心指标供decision_engine用
                    'rsi_1h':    (cleaned.get('momentum') or {}).get('rsi_1h', 50),
                    'long_ratio': cleaned.get('long_ratio', 50),
                    'funding_rate': cleaned.get('funding_rate', 0),
                    'score_final': score,
                }

                try:
                    decision = _decide(signal_in)
                except Exception as e:
                    print(f'[state_refresh] {sym} {direction}: decision_engine错误: {e}')
                    continue

                action = decision.get('action', 'SKIP')
                reason = decision.get('reason', '')
                ep     = decision.get('entry_plan', {})

                print(f'[state_refresh] {sym} {direction}: action={action} reason={reason[:60]}')

                if action not in ('EXECUTE', 'WAIT_15M'):
                    continue

                # 检查队列中是否已有该标的同方向信号（去重）
                dup = any(
                    s.get('symbol') == sym and s.get('signal_dir') == direction
                    for s in active
                )
                if dup:
                    print(f'[state_refresh] {sym} {direction}: 队列中已有信号，跳过')
                    continue

                # ── 构造标准信号 ──────────────────────────────────
                expires_ts  = now_ts + SIGNAL_TTL_HOURS * 3600
                expires_iso = datetime.fromtimestamp(expires_ts, tz=timezone.utc).isoformat()

                price_now = ep.get('price') or cleaned.get('price', 0)
                sl_price  = ep.get('sl_price', 0)
                tp1_price = ep.get('tp1_price', 0)
                tp2_price = ep.get('tp2_price', 0)
                sl_pct    = ep.get('sl_pct', 2.0)
                rr        = ep.get('rr', 0)

                # entry区间：在当前价附近±0.3%
                entry_lo = round(price_now * (0.997 if direction == 'LONG' else 1.000), 2)
                entry_hi = round(price_now * (1.000 if direction == 'LONG' else 1.003), 2)

                sig = {
                    'signal_id':   f'{sym}_{direction}_{int(now_ts)}',
                    'symbol':      sym,
                    'signal_dir':  direction,
                    'direction':   direction,
                    'regime':      regime,
                    'score_final': round(score, 1),
                    'score':       round(score, 1),
                    'grade':       grade,
                    'grade_num':   grade,
                    'price':       price_now,
                    'entry_lo':    entry_lo,
                    'entry_hi':    entry_hi,
                    'sl_price':    round(sl_price, 2),
                    'tp1':         round(tp1_price, 2),
                    'tp2':         round(tp2_price, 2),
                    'sl_pct':      round(sl_pct, 2),
                    'rr':          round(rr, 2),
                    'rr1':         round(rr, 2),
                    'valid':       True,
                    'action':      action,
                    'catalysts':   ep.get('catalysts', []),
                    'source':      'brahma_state_refresh',
                    'created_at':  now_iso,
                    'expires_at':  expires_iso,
                    # CHOP解锁信号额外字段
                    'chop_unlock': _chop_nav_override is not None,
                    'nav_pct_override': _chop_nav_override,  # None=正常仳位, float=CHOP限制仓位
                    # 供 paper_executor / auto_executor 判断用
                    'paper_only':  True,   # 纸面优先，实盘切换时改False
                }

                new_signals.append(sig)
                print(f'[state_refresh] ✅ 新信号入队: {sym} {direction} score={score:.1f} '
                      f'entry={entry_lo}~{entry_hi} SL={sl_price:.2f} TP1={tp1_price:.2f} RR={rr:.2f}x')

        except Exception as e:
            print(f'[state_refresh] ❌ {sym} 分析失败: {e}')

    # ── Step 3: 保存 brahma_state.json ──────────────────────────
    try:
        # 保存第一个标的的完整state（兼容原有读者）
        first_state = all_states.get('BTCUSDT') or (list(all_states.values())[0] if all_states else {})
        STATE_FILE.write_text(json.dumps(first_state, ensure_ascii=False))
    except Exception as e:
        print(f'[state_refresh] ⚠️  brahma_state.json写入失败: {e}')

    # ── Step 4: 保存 auto_signal_queue.json ─────────────────────
    final_queue = active + new_signals
    _save_queue(final_queue)

    print(f'[state_refresh] 队列: 保留{len(active)}个有效 | 过期清理{expired_count}个 | 新增{len(new_signals)}个 | 合计{len(final_queue)}个')

if __name__ == '__main__':
    main()
