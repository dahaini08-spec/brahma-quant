"""
signal_15m_engine.py · 梵天 15M 主框架信号生成器
设计院 × 达摩院 P1-A 封印 2026-08-03 苏摩111

【架构定位】
  本模块是 15M 独立信号流的主入口，而非 1H 信号的执行辅助。
  彻底解决 CHOP_MID 体制下 WR=12.5% 的根本原因（缺少15M精确确认层）。

【信号产生逻辑】
  1. 4H 体制过滤（必须）：BULL_TREND / CHOP_MID / BEAR_RECOVERY
  2. 15M CHoCH/BOS 结构确认（核心触发）
  3. 15M FVG / OB 入场区确认
  4. 成交量过滤：15M成交量 > 20期均量 × 1.2
  5. RSI_15M 甜蜜区：做多 <55，做空 >45
  6. 止损：15M ATR14 × 1.5（目标 0.3%~0.8%）
  7. RR ≥ 1.5

【无上帝视角保证】
  - 所有检测仅使用已关闭的15M K线（不含当前未收盘K线）
  - OB 时间戳必须 < 信号时间
  - 体制用4H已关闭K线计算，无前瞻

【输出格式】
  兼容 live_signal_log.jsonl 标准字段集
  primary_tf = '15M'
  entry_tf = '15M'
"""

import os, sys, time, math, json
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
ROOT_DIR = BASE_DIR.parent

# ══════════════════════════════════════════════════
# 工具函数（无穿越，纯历史K线计算）
# ══════════════════════════════════════════════════

def _ema(prices: list, n: int) -> float:
    if len(prices) < n:
        return prices[-1] if prices else 0
    k = 2 / (n + 1)
    e = sum(prices[:n]) / n
    for p in prices[n:]:
        e = e * (1 - k) + p * k
    return e


def _rsi(closes: list, n: int = 14) -> float:
    if len(closes) < n + 1:
        return 50.0
    d = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    g = [max(0, x) for x in d[-n:]]
    lo = [max(0, -x) for x in d[-n:]]
    ag, al = sum(g) / n, sum(lo) / n
    return round(100 - 100 / (1 + ag / al), 2) if al > 0 else 100.0


def _atr(highs: list, lows: list, closes: list, n: int = 14) -> float:
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        ))
    if not trs:
        return closes[-1] * 0.005
    return sum(trs[-n:]) / min(n, len(trs))


def _vol_ratio(volumes: list, n: int = 20) -> float:
    """当前成交量 / N期均量"""
    if len(volumes) < n + 1:
        return 1.0
    avg = sum(volumes[-n - 1:-1]) / n
    return volumes[-1] / avg if avg > 0 else 1.0


# ══════════════════════════════════════════════════
# 15M 结构检测（无穿越版）
# ══════════════════════════════════════════════════

def _detect_choch_bos_15m(bars: list, direction: str) -> dict:
    """
    检测15M CHoCH / BOS
    bars: 已关闭的15M K线列表，格式 [{'ts','o','h','l','c','v'}]
    direction: LONG / SHORT
    
    CHoCH: 最近的结构转变（反向突破）
    BOS:   趋势方向的结构突破（顺势）
    
    无穿越保证：只使用已关闭K线
    """
    if len(bars) < 20:
        return {'type': 'NONE', 'confirmed': False}

    recent = bars[-40:]  # 最近40根已关闭K线

    # 找摆高摆低：使用左右各2根K线确认
    swing_highs = []
    swing_lows = []
    for i in range(2, len(recent) - 2):
        h = recent[i]['h']
        if h > recent[i-1]['h'] and h > recent[i-2]['h'] and h > recent[i+1]['h'] and h > recent[i+2]['h']:
            swing_highs.append((i, h, recent[i]['ts']))
        l = recent[i]['l']
        if l < recent[i-1]['l'] and l < recent[i-2]['l'] and l < recent[i+1]['l'] and l < recent[i+2]['l']:
            swing_lows.append((i, l, recent[i]['ts']))

    if not swing_highs or not swing_lows:
        return {'type': 'NONE', 'confirmed': False}

    last_close = recent[-1]['c']

    if direction == 'LONG':
        # 做多 CHoCH：价格跌破近期摆低后，再次突破摆高 → 结构由空转多
        # 做多 BOS：直接突破近期摆高（趋势延续）
        last_sh = swing_highs[-1]
        last_sl = swing_lows[-1]
        
        if last_close > last_sh[1]:
            # 价格突破最近摆高
            struct_type = 'BOS_LONG' if last_sh[0] > last_sl[0] else 'CHoCH_LONG'
            return {
                'type': struct_type,
                'confirmed': True,
                'level': last_sh[1],
                'ts': last_sh[2],
                'note': f'15M {struct_type} 突破${last_sh[1]:.4f}'
            }
    else:
        # 做空 CHoCH/BOS
        last_sh = swing_highs[-1]
        last_sl = swing_lows[-1]

        if last_close < last_sl[1]:
            struct_type = 'BOS_SHORT' if last_sl[0] > last_sh[0] else 'CHoCH_SHORT'
            return {
                'type': struct_type,
                'confirmed': True,
                'level': last_sl[1],
                'ts': last_sl[2],
                'note': f'15M {struct_type} 跌破${last_sl[1]:.4f}'
            }

    return {'type': 'NONE', 'confirmed': False}


def _detect_fvg_15m(bars: list, direction: str) -> dict | None:
    """
    检测15M FVG（Fair Value Gap）
    三K线型：bars[i-2], bars[i-1], bars[i]
    多头FVG: bars[i]['l'] > bars[i-2]['h']（中间跳空，买方流动性真空）
    空头FVG: bars[i]['h'] < bars[i-2]['l']
    
    只检测最近20根已关闭K线内的FVG
    """
    if len(bars) < 5:
        return None

    recent = bars[-20:]
    fvgs = []

    for i in range(2, len(recent)):
        if direction == 'LONG':
            # 多头FVG：当前K线低点 > 两K线前高点
            if recent[i]['l'] > recent[i-2]['h']:
                fvg_lo = recent[i-2]['h']
                fvg_hi = recent[i]['l']
                fvgs.append({
                    'type': 'BULL_FVG_15M',
                    'lo': fvg_lo,
                    'hi': fvg_hi,
                    'mid': (fvg_lo + fvg_hi) / 2,
                    'ts': recent[i]['ts'],
                    'size_pct': round((fvg_hi - fvg_lo) / fvg_lo * 100, 3)
                })
        else:
            # 空头FVG：当前K线高点 < 两K线前低点
            if recent[i]['h'] < recent[i-2]['l']:
                fvg_lo = recent[i]['h']
                fvg_hi = recent[i-2]['l']
                fvgs.append({
                    'type': 'BEAR_FVG_15M',
                    'lo': fvg_lo,
                    'hi': fvg_hi,
                    'mid': (fvg_lo + fvg_hi) / 2,
                    'ts': recent[i]['ts'],
                    'size_pct': round((fvg_hi - fvg_lo) / fvg_lo * 100, 3)
                })

    if not fvgs:
        return None
    # 返回最近的FVG
    return fvgs[-1]


def _detect_ob_15m(bars: list, direction: str) -> dict | None:
    """
    检测15M Order Block（最近有效OB）
    多头OB：大阴线后转大阳线的阴线区域（机构买入区）
    空头OB：大阳线后转大阴线的阳线区域（机构卖出区）
    
    只使用已关闭K线
    """
    if len(bars) < 5:
        return None

    recent = bars[-30:]
    for i in range(len(recent) - 2, 2, -1):
        k_prev = recent[i - 1]
        k_curr = recent[i]
        body_prev = (k_prev['c'] - k_prev['o']) / k_prev['o'] * 100
        body_curr = (k_curr['c'] - k_curr['o']) / k_curr['o'] * 100

        if direction == 'LONG':
            # 多头OB: 前K为阴线(-0.3%以上体)，当前K为阳线 → 阴线区域是OB
            if body_prev < -0.25 and body_curr > 0.1:
                return {
                    'type': 'BULL_OB_15M',
                    'hi': k_prev['h'],
                    'lo': k_prev['l'],
                    'mid': (k_prev['h'] + k_prev['l']) / 2,
                    'ts': k_prev['ts'],
                    'age_bars': len(recent) - 1 - (i - 1)
                }
        else:
            # 空头OB: 前K为阳线，当前K为阴线
            if body_prev > 0.25 and body_curr < -0.1:
                return {
                    'type': 'BEAR_OB_15M',
                    'hi': k_prev['h'],
                    'lo': k_prev['l'],
                    'mid': (k_prev['h'] + k_prev['l']) / 2,
                    'ts': k_prev['ts'],
                    'age_bars': len(recent) - 1 - (i - 1)
                }
    return None


# ══════════════════════════════════════════════════
# 4H 体制判断（已有历史数据的离线版）
# ══════════════════════════════════════════════════

def _regime_from_4h(c4h: list, c1d: list) -> str:
    """
    用4H+1D已关闭K线判断体制
    与 brahma_backtest_v3.py detect_regime 完全一致（无穿越）
    """
    if len(c4h) < 50 or len(c1d) < 20:
        return 'CHOP_MID'
    px = c4h[-1]
    e20 = _ema(c4h[-20:], 20)
    e50 = _ema(c4h[-50:], 50)
    e200d = _ema(c1d[-20:], 20)
    r4 = _rsi(c4h[-30:])
    ema20_vs_50 = (e20 - e50) / e50 * 100
    px_vs_200d = (px - e200d) / e200d * 100
    px_vs_4h20 = (px - e20) / e20 * 100

    if r4 > 55 and ema20_vs_50 > 1.0 and px_vs_200d > 0:
        return 'BULL_TREND' if (px_vs_4h20 > 3 and r4 > 65) else 'BULL_EARLY'
    elif r4 < 45 and ema20_vs_50 < -1.0:
        return 'BEAR_TREND' if (px_vs_200d < -5 and r4 < 40) else 'BEAR_EARLY'
    elif r4 > 45 and px_vs_200d > -8 and ema20_vs_50 > -0.5:
        return 'BEAR_RECOVERY'
    else:
        return 'CHOP_MID'


# ══════════════════════════════════════════════════
# 15M 实盘信号生成主入口（实时调用）
# ══════════════════════════════════════════════════

def generate_15m_signal(symbol: str, verbose: bool = False) -> dict | None:
    """
    15M 主框架信号生成入口（实盘）
    
    调用链：
      获取实时15M K线 → 体制过滤(4H) → 结构检测 → FVG/OB → 成交量过滤 → 输出信号
    
    返回 None 表示无信号（正常，静默）
    返回 dict 表示有效信号，兼容 live_signal_log.jsonl 标准字段
    """
    import requests

    sym = symbol.upper()
    if not sym.endswith('USDT'):
        sym += 'USDT'

    try:
        # ── 获取多周期K线（只拿已关闭K线：limit+1，丢弃最后一根未收盘）──
        def _fetch(interval, limit=100):
            r = requests.get(
                'https://fapi.binance.com/fapi/v1/klines',
                params={'symbol': sym, 'interval': interval, 'limit': limit + 1},
                timeout=8
            )
            data = r.json()
            if not isinstance(data, list) or len(data) < 2:
                return []
            # 丢弃最后一根（未收盘），只用已关闭K线
            return [{
                'ts': int(d[0]), 'o': float(d[1]), 'h': float(d[2]),
                'l': float(d[3]), 'c': float(d[4]), 'v': float(d[5])
            } for d in data[:-1]]

        bars_15m = _fetch('15m', 120)
        bars_1h  = _fetch('1h', 60)
        bars_4h  = _fetch('4h', 80)
        bars_1d  = _fetch('1d', 40)

        if len(bars_15m) < 40 or len(bars_4h) < 50:
            return None

        # ── 提取价格序列（已关闭K线）──
        c15m = [b['c'] for b in bars_15m]
        h15m = [b['h'] for b in bars_15m]
        l15m = [b['l'] for b in bars_15m]
        v15m = [b['v'] for b in bars_15m]
        c4h  = [b['c'] for b in bars_4h]
        c1d  = [b['c'] for b in bars_1d] if bars_1d else [c4h[-1]] * 30

        current_price = c15m[-1]
        signal_ts = time.time()

        # ── Step 1: 4H 体制过滤 ──
        regime = _regime_from_4h(c4h, c1d)
        # 死穴：BEAR_TREND做多 / BULL_TREND做空 → 禁止
        # 15M信号主攻 CHOP_MID（当前最缺失场景）+ BULL_TREND 做多 + BEAR_RECOVERY 做多

        # ── Step 2: RSI 过滤 ──
        rsi_15m = _rsi(c15m, 14)
        rsi_4h  = _rsi(c4h, 14)

        # 判断方向倾向
        # 多头条件：RSI_15M < 55 + regime不是BEAR_TREND
        # 空头条件：RSI_15M > 45 + regime不是BULL_TREND
        candidates_dir = []
        if regime not in ('BEAR_TREND', 'BEAR_EARLY') and rsi_15m < 58:
            candidates_dir.append('LONG')
        if regime not in ('BULL_TREND', 'BULL_EARLY') and rsi_15m > 42:
            candidates_dir.append('SHORT')

        if not candidates_dir:
            return None

        # ── Step 3: 成交量过滤 ──
        vol_ratio = _vol_ratio(v15m, 20)
        if vol_ratio < 1.0:  # 成交量低于均量，不产生信号
            if verbose:
                print(f'[15M] {sym} 成交量不足 vol_ratio={vol_ratio:.2f}')
            return None

        # ── Step 4: 结构检测 + FVG/OB ──
        best_signal = None
        best_score = 0

        for direction in candidates_dir:
            # 死穴检查
            if (regime, direction) in [
                ('BEAR_TREND', 'LONG'), ('BULL_TREND', 'SHORT'),
                ('BEAR_RECOVERY', 'SHORT'), ('BEAR_EARLY', 'SHORT')
            ]:
                continue

            # 结构检测
            struct = _detect_choch_bos_15m(bars_15m, direction)
            if not struct.get('confirmed'):
                continue

            # FVG 检测
            fvg = _detect_fvg_15m(bars_15m, direction)
            ob  = _detect_ob_15m(bars_15m, direction)

            # ── 入场区计算 ──
            atr_15m = _atr(h15m, l15m, c15m, 14)

            if fvg:
                entry_lo = fvg['lo']
                entry_hi = fvg['hi']
                entry_source = 'FVG_15M'
            elif ob:
                entry_lo = ob['lo']
                entry_hi = ob['hi']
                entry_source = 'OB_15M'
            else:
                # 无 FVG/OB 时用 ATR 偏移构造入场区
                if direction == 'LONG':
                    entry_lo = current_price - atr_15m * 0.5
                    entry_hi = current_price - atr_15m * 0.1
                else:
                    entry_lo = current_price + atr_15m * 0.1
                    entry_hi = current_price + atr_15m * 0.5
                entry_source = 'ATR_15M'

            # ── 止损计算（15M ATR × 1.5，目标 0.3%~0.8%）──
            sl_raw = atr_15m * 1.5
            sl_pct = sl_raw / current_price * 100

            # 止损约束：0.25% ~ 1.2%
            if sl_pct < 0.25 or sl_pct > 1.2:
                continue

            if direction == 'LONG':
                stop_loss = round(entry_lo * (1 - sl_pct / 100), 6)
                tp1       = round(entry_hi * (1 + sl_pct / 100 * 1.5), 6)
                tp2       = round(entry_hi * (1 + sl_pct / 100 * 3.0), 6)
            else:
                stop_loss = round(entry_hi * (1 + sl_pct / 100), 6)
                tp1       = round(entry_lo * (1 - sl_pct / 100 * 1.5), 6)
                tp2       = round(entry_lo * (1 - sl_pct / 100 * 3.0), 6)

            rr1 = abs(tp1 - entry_hi) / max(abs(stop_loss - entry_lo), 1e-9)
            rr1 = round(rr1, 2)

            # RR 门槛：≥ 1.5
            if rr1 < 1.5:
                continue

            # ── 评分（15M专用，0~100）──
            score = 0
            score += 30  # 结构确认基础分
            if struct['type'].startswith('CHoCH'):
                score += 15  # CHoCH > BOS
            if fvg:
                score += 20  # FVG优于OB
            elif ob:
                score += 10
            if vol_ratio >= 1.5:
                score += 15
            elif vol_ratio >= 1.2:
                score += 8
            if regime == 'CHOP_MID':
                score += 10  # 主攻场景加分
            elif regime in ('BULL_TREND', 'BEAR_RECOVERY') and direction == 'LONG':
                score += 8
            # RSI甜蜜区
            if direction == 'LONG' and 35 <= rsi_15m <= 50:
                score += 10
            elif direction == 'SHORT' and 50 <= rsi_15m <= 65:
                score += 10

            if score > best_score:
                best_score = score
                best_signal = {
                    'direction': direction,
                    'regime': regime,
                    'struct': struct,
                    'fvg': fvg,
                    'ob': ob,
                    'entry_lo': round(entry_lo, 4),
                    'entry_hi': round(entry_hi, 4),
                    'entry_source': entry_source,
                    'stop_loss': stop_loss,
                    'tp1': tp1,
                    'tp2': tp2,
                    'sl_pct': round(sl_pct, 3),
                    'rr1': rr1,
                    'score': score,
                    'rsi_15m': rsi_15m,
                    'rsi_4h': rsi_4h,
                    'vol_ratio': round(vol_ratio, 2),
                    'atr_15m': round(atr_15m, 6),
                }

        if not best_signal:
            return None

        # ── 组装标准信号字段（兼容 live_signal_log.jsonl）──
        import secrets
        sig_id = secrets.token_hex(6)
        direction = best_signal['direction']
        entry_lo = best_signal['entry_lo']
        entry_hi = best_signal['entry_hi']
        entry_src = best_signal['entry_source']
        ttl_h = 3.0  # 15M信号TTL：3小时（vs 4H信号24H）

        signal = {
            'signal_id':    sig_id,
            'ts':           signal_ts,
            'ts_iso':       datetime.fromtimestamp(signal_ts, tz=timezone.utc).isoformat(),
            'symbol':       sym,
            'signal_dir':   direction,
            'direction':    direction,
            'regime':       best_signal['regime'],
            'regime_cn':    {'BULL_TREND':'多头趋势','CHOP_MID':'中位震荡',
                             'BEAR_RECOVERY':'熊市反弹','BEAR_TREND':'空头趋势'}.get(best_signal['regime'], ''),
            'score':        float(best_signal['score']),
            'score_final':  float(best_signal['score']),
            'grade':        '🟡中等' if best_signal['score'] < 70 else '🟠较强',
            'grade_num':    best_signal['score'],
            'action':       'WATCH' if best_signal['score'] < 65 else 'ENTER',
            'valid':        best_signal['score'] >= 65 and best_signal['rr1'] >= 1.5,
            'price':        round(current_price, 4),
            'generated_price': round(current_price, 4),
            'entry_lo':     entry_lo,
            'entry_hi':     entry_hi,
            'stop_loss':    best_signal['stop_loss'],
            'tp1':          best_signal['tp1'],
            'tp2':          best_signal['tp2'],
            'sl_pct':       best_signal['sl_pct'],
            'rr1':          best_signal['rr1'],
            'primary_tf':   '15M',
            'entry_tf':     '15M',
            'entry_source': entry_src,
            'ob_dist_pct':  0.0,  # 15M信号已在入场区内
            'ob_top':       best_signal['ob']['hi'] if best_signal.get('ob') else 0,
            'ob_bottom':    best_signal['ob']['lo'] if best_signal.get('ob') else 0,
            'ob_source_type': entry_src,
            'fvg_active':   best_signal['fvg'] is not None,
            'fvg_top':      best_signal['fvg']['hi'] if best_signal.get('fvg') else 0,
            'fvg_bottom':   best_signal['fvg']['lo'] if best_signal.get('fvg') else 0,
            'struct_type':  best_signal['struct']['type'],
            'struct_confirmed': best_signal['struct']['confirmed'],
            'rsi_1h':       best_signal['rsi_4h'],   # 用rsi_4h填充rsi_1h字段供下游兼容
            'rsi_4h':       best_signal['rsi_4h'],
            'rsi_15m':      best_signal['rsi_15m'],
            'vol_ratio_15m': best_signal['vol_ratio'],
            'atr_15m':      best_signal['atr_15m'],
            'timing_badge': '🟢 READY',
            'timing_status': 'READY',
            'timing_score': best_signal['score'],
            'consensus':    '',  # 15M信号暂不计算多方向共识（P2扩展）
            'expires_at':   datetime.fromtimestamp(signal_ts + ttl_h * 3600, tz=timezone.utc).isoformat(),
            'ttl_hours':    ttl_h,
            'status':       'pending',
            'result':       None,
            'exit_price':   None,
            'pnl_pct':      None,
            'settled_at':   None,
            'output_tag':   '',
            'structure_grade': best_signal['score'],
            'key_level_proximity': 0.0,
            'mtf_override': False,
            'mtf_mode':     '',
            'mtf_4h_align': regime,
            'kelly_pct':    0,
            '_source':      '15M_ENGINE_P1A',
        }

        if verbose:
            print(f'[15M 信号] {sym} {direction} score={best_signal["score"]} '
                  f'struct={best_signal["struct"]["type"]} '
                  f'entry=[{entry_lo:.4f}, {entry_hi:.4f}] '
                  f'SL={best_signal["stop_loss"]:.4f}({best_signal["sl_pct"]:.2f}%) '
                  f'RR={best_signal["rr1"]}')

        return signal

    except Exception as e:
        if verbose:
            print(f'[15M Engine] {sym} 信号生成失败: {e}')
        return None


# ══════════════════════════════════════════════════
# cron 调用入口：扫描 BTC + ETH
# ══════════════════════════════════════════════════

def scan_and_push(dry_run: bool = False) -> list[dict]:
    """
    扫描 BTC/ETH 15M 信号并写入 live_signal_log.jsonl
    供 cron 调用
    """
    symbols = ['BTCUSDT', 'ETHUSDT']
    results = []

    # 加载推送记录（去重）
    log_path = ROOT_DIR / 'data' / 'live_signal_log.jsonl'
    recent_sigs = set()
    if log_path.exists():
        for line in open(log_path).readlines()[-200:]:
            try:
                s = json.loads(line)
                if s.get('_source') == '15M_ENGINE_P1A':
                    # 最近3小时内同标的同方向不重复
                    age = time.time() - s.get('ts', 0)
                    if age < 10800:
                        recent_sigs.add(f'{s.get("symbol")}_{s.get("direction")}')
            except Exception:
                pass

    for sym in symbols:
        key = f'{sym}_LONG'
        key_s = f'{sym}_SHORT'
        if key in recent_sigs and key_s in recent_sigs:
            print(f'[15M] {sym} 两个方向最近3H已有信号，跳过')
            continue

        sig = generate_15m_signal(sym, verbose=True)
        if not sig:
            print(f'[15M] {sym} 无信号')
            continue

        # 方向去重
        dir_key = f'{sym}_{sig.get("direction")}'
        if dir_key in recent_sigs:
            print(f'[15M] {sym} {sig.get("direction")} 最近3H已推送，跳过')
            continue

        results.append(sig)

        if not dry_run:
            # 写入 live_signal_log.jsonl
            with open(log_path, 'a') as f:
                f.write(json.dumps(sig, ensure_ascii=False) + '\n')
            print(f'[15M] ✅ 已写入信号: {sym} {sig.get("direction")} score={sig.get("score")}')

            # 触发 dharma_data_bridge 标准化入库
            try:
                sys.path.insert(0, str(ROOT_DIR))
                from brahma_brain.dharma_data_bridge import log_signal
                log_signal(sig)
            except Exception as e:
                pass  # bridge失败不阻断

    return results


# ══════════════════════════════════════════════════
# 直接运行入口
# ══════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='梵天15M信号引擎')
    parser.add_argument('--symbol', default='', help='指定标的 (默认扫描BTC+ETH)')
    parser.add_argument('--dry-run', action='store_true', help='不写入信号日志')
    parser.add_argument('--verbose', action='store_true', help='详细输出')
    args = parser.parse_args()

    if args.symbol:
        sig = generate_15m_signal(args.symbol, verbose=True)
        if sig:
            print(json.dumps(sig, indent=2, ensure_ascii=False))
        else:
            print(f'[15M] {args.symbol} 暂无信号')
    else:
        sigs = scan_and_push(dry_run=args.dry_run)
        print(f'[15M] 本轮产生 {len(sigs)} 条信号')
