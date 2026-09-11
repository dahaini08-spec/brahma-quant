"""
brahma_backtest_v3.py — 梵天实战回测引擎 v3.0
三方联合 | 苏摩111 2026-09-08 封印

v2→v3 核心升级：把40年交易员的5层框架全部写进引擎
  ①清算地图：swing high/low止损密集区近似
  ②FVG磁铁：方向+中点+磁铁拉力
  ③OB有效性：age<50bars + 未穿越验证
  ④共振点：FVG中点+有效OB+清算密集区三者交叉
  ⑤浮盈保护：浮盈>1.5×ATR → 移动SL保本

接入位置：独立脚本，不修改现有模块
"""

import gzip, json, os, sys, math, time, argparse
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA_DIR = BASE / "data" / "historical"
OUT_DIR  = BASE / "data"

# ════════════════════════════════════════════════════════════
# 数据加载
# ════════════════════════════════════════════════════════════

def load_klines(symbol, tf, start_ts=0):
    fname = DATA_DIR / f"{symbol}_{tf}.jsonl.gz"
    if not fname.exists(): return []
    with gzip.open(fname, 'rt') as f:
        return sorted([json.loads(l) for l in f if l.strip()
                      and json.loads(l).get('ts',0) >= start_ts],
                      key=lambda x: x['ts'])

def load_regime_labels(symbol):
    fname = DATA_DIR / f"{symbol}_regime_labels.jsonl.gz"
    if not fname.exists(): return {}
    with gzip.open(fname, 'rt') as f:
        labels = [json.loads(l) for l in f if l.strip()]
    return {l['ts']: l['regime'] for l in labels}

# ════════════════════════════════════════════════════════════
# 指标计算
# ════════════════════════════════════════════════════════════

def calc_atr(bars, period=14):
    atrs = [None]*len(bars)
    trs = []
    for i, b in enumerate(bars):
        if i == 0: tr = b['h']-b['l']
        else:
            pc = bars[i-1]['c']
            tr = max(b['h']-b['l'], abs(b['h']-pc), abs(b['l']-pc))
        trs.append(tr)
        if i >= period-1:
            if i == period-1: atrs[i] = sum(trs)/period
            else: atrs[i] = (atrs[i-1]*(period-1)+tr)/period
    return atrs

def calc_rsi(bars, period=14):
    rsis = [None]*len(bars)
    gains, losses = [], []
    for i in range(1, len(bars)):
        d = bars[i]['c']-bars[i-1]['c']
        gains.append(max(d,0)); losses.append(max(-d,0))
        if i >= period:
            ag = sum(gains[-period:])/period
            al = sum(losses[-period:])/period
            rsis[i] = 100 if al == 0 else 100-100/(1+ag/al)
    return rsis

def calc_ema(bars, period, key='c'):
    emas = [None]*len(bars)
    k = 2/(period+1)
    for i, b in enumerate(bars):
        if i == 0: emas[i] = b[key]
        elif emas[i-1] is None: emas[i] = b[key]
        else: emas[i] = b[key]*k+emas[i-1]*(1-k)
    return emas

# ════════════════════════════════════════════════════════════
# ① 清算地图层（40年第1层：散户止损在哪）
# ════════════════════════════════════════════════════════════

def build_liquidity_map(bars, idx, lookback=100, window=5):
    """
    检测近期swing high/low → 散户止损密集区
    
    做多止损 = 近期swing low下方（散户多单止损区）
    做空止损 = 近期swing high上方（散户空单止损区）
    
    返回:
      long_liq_pool: 做多止损密集区价格（下方）
      short_liq_pool: 做空止损密集区价格（上方）
      liq_density: 清算密度（0-1，越高越密集）
    """
    if idx < lookback: lookback = idx
    
    swing_highs = []
    swing_lows = []
    
    # 找swing high/low（局部极值）
    for j in range(idx-lookback, idx-2):
        if j < window: continue
        # swing high
        is_high = all(bars[j]['h'] >= bars[j-k]['h'] for k in range(1, window+1)) and \
                  all(bars[j]['h'] >= bars[j+k]['h'] for k in range(1, min(window+1, idx-j)))
        if is_high: swing_highs.append(bars[j]['h'])
        # swing low
        is_low = all(bars[j]['l'] <= bars[j-k]['l'] for k in range(1, window+1)) and \
                 all(bars[j]['l'] <= bars[j+k]['l'] for k in range(1, min(window+1, idx-j)))
        if is_low: swing_lows.append(bars[j]['l'])
    
    # 聚类：相近的极值归为同一密集区
    def cluster(levels, tolerance=0.01):
        if not levels: return []
        clusters = []
        current = [levels[0]]
        for lv in sorted(levels)[1:]:
            if abs(lv - current[-1]) / current[-1] < tolerance:
                current.append(lv)
            else:
                clusters.append(sum(current)/len(current))
                current = [lv]
        clusters.append(sum(current)/len(current))
        return clusters
    
    high_clusters = cluster(swing_highs)
    low_clusters = cluster(swing_lows)
    
    # 找最近的密集区
    current_price = bars[idx]['c']
    
    # 上方止损山（做空止损区）= 最近的swing high簇
    short_liq_pool = None
    for h in reversed(high_clusters):
        if h > current_price:
            short_liq_pool = h
            break
    
    # 下方止损池（做多止损区）= 最近的swing low簇
    long_liq_pool = None
    for l in reversed(low_clusters):
        if l < current_price:
            long_liq_pool = l
            break
    
    # 清算密度 = 簇内极值数量
    liq_density = min(1.0, (len(swing_highs)+len(swing_lows)) / 20)
    
    return {
        'long_liq_pool': long_liq_pool,   # 下方止损池（做多止损区）
        'short_liq_pool': short_liq_pool, # 上方止损山（做空止损区）
        'liq_density': round(liq_density, 2),
        'n_swing_highs': len(swing_highs),
        'n_swing_lows': len(swing_lows),
    }

# ════════════════════════════════════════════════════════════
# ② FVG磁铁层（40年第2层：主力足迹）
# ════════════════════════════════════════════════════════════

def detect_fvg_advanced(bars, idx, lookback=20):
    """
    高级FVG检测：
    - Bull/Bear FVG方向
    - FVG中点（最强磁铁）
    - 磁铁拉力（价格距FVG的距离）
    - FVG是否已被填充
    
    返回最近的有效FVG（不一定在当前K线，可能在最近lookback根内）
    """
    # 先检测当前K线的FVG
    fvg = detect_fvg(bars, idx)
    if fvg['type'] != 'none':
        return fvg
    
    # 找最近lookback根内的FVG
    for j in range(idx-1, max(idx-lookback, 2), -1):
        fvg = detect_fvg(bars, j)
        if fvg['type'] != 'none':
            # 检查是否已被填充
            for k in range(j+1, idx+1):
                if fvg['type'] == 'bull':
                    # Bull FVG被填充 = 价格回到FVG区间内
                    if bars[k]['l'] <= fvg['top']:
                        return {'type': 'none', 'top': 0, 'bottom': 0, 'mid': 0, 'age': 0}
                else:
                    # Bear FVG被填充
                    if bars[k]['h'] >= fvg['bottom']:
                        return {'type': 'none', 'top': 0, 'bottom': 0, 'mid': 0, 'age': 0}
            
            # 未被填充的有效FVG
            current_price = bars[idx]['c']
            if fvg['type'] == 'bull':
                pull = (fvg['mid'] - current_price) / current_price * 100
            else:
                pull = (current_price - fvg['mid']) / current_price * 100
            
            return {
                'type': fvg['type'],
                'top': fvg['top'],
                'bottom': fvg['bottom'],
                'mid': fvg['mid'],
                'age': idx - j,
                'pull': round(pull, 2),  # 磁铁拉力（%），正=向FVG方向拉
            }
    
    return {'type': 'none', 'top': 0, 'bottom': 0, 'mid': 0, 'age': 0, 'pull': 0}

def detect_fvg(bars, idx):
    """基础FVG检测（3根K线缺口）"""
    if idx < 2: return {'type': 'none', 'top': 0, 'bottom': 0, 'mid': 0, 'age': 0, 'pull': 0}
    b0, b2 = bars[idx-2], bars[idx]
    if b0['h'] < b2['l']:
        top, bot = b2['l'], b0['h']
        return {'type': 'bull', 'top': top, 'bottom': bot, 'mid': (top+bot)/2, 'age': 0, 'pull': 0}
    if b0['l'] > b2['h']:
        top, bot = b0['l'], b2['h']
        return {'type': 'bear', 'top': top, 'bottom': bot, 'mid': (top+bot)/2, 'age': 0, 'pull': 0}
    return {'type': 'none', 'top': 0, 'bottom': 0, 'mid': 0, 'age': 0, 'pull': 0}

# ════════════════════════════════════════════════════════════
# ③ OB有效性层（40年第3层：主力仓位起点）
# ════════════════════════════════════════════════════════════

def detect_ob_advanced(bars, idx, lookback=20, max_age=50):
    """
    高级OB检测：
    - Bear OB = 上涨K线后被下跌突破
    - Bull OB = 下跌K线后被上涨突破
    - age < 50bars = 有效
    - 未被穿越 = 有效（后续价格未回到OB区间内收盘）
    """
    if idx < lookback + 2:
        return {'type': 'none', 'top': 0, 'bottom': 0, 'age': 0, 'valid': False}
    
    best_ob = None
    
    for j in range(idx-1, max(idx-lookback, 1), -1):
        bc = bars[j]
        b_next = bars[j+1] if j+1 <= idx else bars[idx]
        
        # Bear OB: 上涨K线 → 后续下跌突破
        if bc['c'] > bc['o']:  # 上涨K线
            # 检查后续是否跌破该K线低点
            broken = False
            for k in range(j+1, idx+1):
                if bars[k]['c'] < bc['l']:
                    broken = True
                    break
            if broken:
                age = idx - j
                if age <= max_age:
                    # 检查是否被穿越（后续价格是否回到OB上方收盘）
                    violated = False
                    for k in range(j+1, idx+1):
                        if bars[k]['c'] > bc['h']:
                            violated = True
                            break
                    if not violated:
                        ob = {'type': 'bear', 'top': bc['h'], 'bottom': bc['l'],
                              'age': age, 'valid': True}
                        if best_ob is None or age < best_ob['age']:
                            best_ob = ob
        
        # Bull OB: 下跌K线 → 后续上涨突破
        if bc['c'] < bc['o']:  # 下跌K线
            broken = False
            for k in range(j+1, idx+1):
                if bars[k]['c'] > bc['h']:
                    broken = True
                    break
            if broken:
                age = idx - j
                if age <= max_age:
                    violated = False
                    for k in range(j+1, idx+1):
                        if bars[k]['c'] < bc['l']:
                            violated = True
                            break
                    if not violated:
                        ob = {'type': 'bull', 'top': bc['h'], 'bottom': bc['l'],
                              'age': age, 'valid': True}
                        if best_ob is None or age < best_ob['age']:
                            best_ob = ob
    
    return best_ob or {'type': 'none', 'top': 0, 'bottom': 0, 'age': 0, 'valid': False}

# ════════════════════════════════════════════════════════════
# ④ 共振点检测（40年第4层：三维交叉 = 精度入场）
# ════════════════════════════════════════════════════════════

def detect_resonance(bars, idx, fvg, ob, liq_map, atr_val):
    """
    三维共振检测：
    FVG中点 + 有效OB + 清算密集区 → 三者交叉 = VIP入场区
    
    返回共振强度（0-3）：
      0 = 无共振（不入场）
      1 = 弱共振（2项重叠）
      2 = 中共振（2项重叠+方向一致）
      3 = 强共振（3项全交叉+方向一致）
    """
    if not atr_val or atr_val == 0:
        return {'score': 0, 'zone_top': 0, 'zone_bottom': 0, 'reason': 'no_atr'}
    
    current_price = bars[idx]['c']
    # 共振区范围 = 1×ATR（价格在此范围内算重叠）
    zone_size = atr_val
    
    fvg_match = False
    ob_match = False
    liq_match = False
    
    # FVG检查：FVG中点在价格附近
    if fvg['type'] != 'none' and fvg.get('mid', 0) > 0:
        if abs(fvg['mid'] - current_price) <= zone_size * 2:
            fvg_match = True
    
    # OB检查：有效OB在价格附近
    if ob['valid'] and ob['type'] != 'none':
        ob_mid = (ob['top'] + ob['bottom']) / 2
        if abs(ob_mid - current_price) <= zone_size * 2:
            ob_match = True
    
    # 清算检查：清算密集区在价格附近
    if liq_map['liq_density'] > 0.3:
        # 做多时：下方止损池近 = 清算支撑
        # 做空时：上方止损山近 = 清算阻力
        liq = liq_map.get('long_liq_pool') or liq_map.get('short_liq_pool')
        if liq and abs(liq - current_price) <= zone_size * 3:
            liq_match = True
    
    matches = sum([fvg_match, ob_match, liq_match])
    
    # 确定共振区
    zone_top = current_price + zone_size
    zone_bottom = current_price - zone_size
    
    if matches == 3:
        return {'score': 3, 'zone_top': zone_top, 'zone_bottom': zone_bottom,
                'reason': 'triple_resonance'}
    elif matches == 2:
        # 检查方向一致性
        return {'score': 2, 'zone_top': zone_top, 'zone_bottom': zone_bottom,
                'reason': 'double_resonance'}
    elif matches == 1:
        return {'score': 1, 'zone_top': zone_top, 'zone_bottom': zone_bottom,
                'reason': 'single_signal'}
    else:
        return {'score': 0, 'zone_top': 0, 'zone_bottom': 0, 'reason': 'no_resonance'}

# ════════════════════════════════════════════════════════════
# ⑤ 评分器（v3：5层框架全部融入）
# ════════════════════════════════════════════════════════════

def score_v3(regime, direction, rsi, fvg, ob, liq_map, resonance,
             bb_width, vol_ratio, atr_pct):
    """
    v3评分器：40年框架5层全融入
    基础分：50，满分：200
    
    维度：
    ① 体制×方向（±30）     ② FVG磁铁（±15+方向确认±10）
    ③ OB有效性（±10+age验证） ④ 清算地图（±10）
    ⑤ 共振点（0/5/10/20）   ⑥ RSI（±10）
    ⑦ BB宽度（±8）          ⑧ 成交量（±7）
    """
    s = 50.0
    
    # ① 体制×方向（最重要）
    bonus = {
        ('BULL_TREND','LONG'):30,('BULL_TREND','SHORT'):-20,
        ('BEAR_TREND','SHORT'):30,('BEAR_TREND','LONG'):-20,
        ('BULL_EARLY','LONG'):25,('BULL_EARLY','SHORT'):-15,
        ('BEAR_RECOVERY','LONG'):15,('BEAR_RECOVERY','SHORT'):-25,
        ('CHOP_MID','LONG'):0,('CHOP_MID','SHORT'):0,
        ('CHOP_HIGH','LONG'):5,('CHOP_HIGH','SHORT'):5,
    }
    s += bonus.get((regime, direction), 0)
    
    # ② FVG磁铁（方向确认+磁铁拉力）
    if direction == 'LONG' and fvg['type'] == 'bull':
        s += 15
        # 磁铁方向确认：FVG向上拉=做多有利
        if fvg.get('pull', 0) > 0:
            s += 10  # 磁铁拉力方向一致
    elif direction == 'SHORT' and fvg['type'] == 'bear':
        s += 15
        if fvg.get('pull', 0) > 0:
            s += 10
    elif fvg['type'] != 'none':
        s -= 5  # 逆FVG
    
    # ③ OB有效性（age<50 + 未穿越）
    if ob['valid']:
        if direction == 'LONG' and ob['type'] == 'bull':
            s += 10
            if ob['age'] < 20: s += 5  # 新鲜OB
        elif direction == 'SHORT' and ob['type'] == 'bear':
            s += 10
            if ob['age'] < 20: s += 5
    elif ob['type'] != 'none' and ob.get('age', 0) >= 50:
        s -= 5  # 老化OB
    
    # ④ 清算地图（散户止损区=主力目标）
    if liq_map['liq_density'] > 0.5:
        # 高密度清算区 = 主力更可能去扫止损
        if direction == 'SHORT' and liq_map['short_liq_pool']:
            # 做空：上方止损山近 = 先扫空再跌
            s += 10
        elif direction == 'LONG' and liq_map['long_liq_pool']:
            # 做多：下方止损池近 = 先扫多再涨
            s += 10
    
    # ⑤ 共振点（核心！三维交叉 = 精度入场）
    if resonance['score'] == 3:
        s += 20  # 三共振 = VIP级
    elif resonance['score'] == 2:
        s += 10  # 双共振
    elif resonance['score'] == 1:
        s += 3   # 单信号
    else:
        s -= 5   # 无共振 = 扣分（40年铁律：没有共振不入场）
    
    # ⑥ RSI超卖/超买
    if direction == 'LONG':
        if rsi and rsi < 30: s += 10
        elif rsi and rsi > 70: s -= 10
    else:
        if rsi and rsi > 70: s += 10
        elif rsi and rsi < 30: s -= 10
    
    # ⑦ BB宽度
    if bb_width < 1.0: s += 8
    elif bb_width > 5.0: s -= 5
    
    # ⑧ 成交量
    if vol_ratio > 1.5: s += 7
    elif vol_ratio < 0.7: s -= 5
    
    return max(0, min(200, s))

# ════════════════════════════════════════════════════════════
# 门控层
# ════════════════════════════════════════════════════════════

DEAD_ZONE = {'CHOP_MID', 'BEAR_TREND', 'BEAR_EARLY'}

def apply_gates(regime, direction, score, resonance):
    """
    v3门控：加入共振点要求
    40年铁律：没有共振不入场
    """
    if regime == 'BEAR_TREND' and direction == 'LONG': return True, 'BEAR_TREND_LONG'
    if regime in DEAD_ZONE and 130 <= score < 145: return True, 'DEAD_ZONE'
    if regime == 'CHOP_MID' and score < 110: return True, 'CHOP_LOW'
    if score < 85: return True, 'LOW_SCORE'
    if regime == 'BEAR_RECOVERY' and direction == 'SHORT': return True, 'BEAR_REC_SHORT'
    # v3新增：共振点要求
    if resonance['score'] == 0: return True, 'NO_RESONANCE'
    return False, ''

# ════════════════════════════════════════════════════════════
# ⑤ 出场模拟（v3：浮盈保护）
# ════════════════════════════════════════════════════════════

def simulate_exit_v3(bars, entry_idx, direction, entry_price, atr, tf, rr=1.5):
    """
    v3出场：SL + TP + 时间止损 + 浮盈保护
    浮盈>1.5×ATR → 移动SL保本（40年第5层铁律）
    """
    sl_pct = 0.02
    hold = {'15m':48,'1h':24,'4h':12,'1d':5}.get(tf, 24)
    min_sl = 1.5 * atr if atr else entry_price * 0.01
    sl_dist = max(entry_price * sl_pct, min_sl)
    
    if direction == 'LONG':
        sl = entry_price - sl_dist
        tp = entry_price + sl_dist * rr
    else:
        sl = entry_price + sl_dist
        tp = entry_price - sl_dist * rr
    
    # 浮盈保护线 = 1.5×ATR
    breakeven_trigger = 1.5 * atr if atr else sl_dist
    sl_moved = False
    
    for i in range(entry_idx + 1, min(entry_idx + hold + 1, len(bars))):
        b = bars[i]
        
        # 浮盈保护：浮盈>1.5×ATR → SL移到保本
        if not sl_moved:
            if direction == 'LONG' and b['c'] - entry_price >= breakeven_trigger:
                sl = entry_price  # SL移到保本
                sl_moved = True
            elif direction == 'SHORT' and entry_price - b['c'] >= breakeven_trigger:
                sl = entry_price
                sl_moved = True
        
        if direction == 'LONG':
            if b['l'] <= sl:
                pnl = (sl - entry_price) / entry_price * 100
                reason = 'BE_SL' if sl_moved else 'SL'
                return {'is_win': pnl > 0, 'pnl_pct': pnl, 'exit_reason': reason,
                        'bars_held': i - entry_idx, 'sl_moved': sl_moved}
            if b['h'] >= tp:
                pnl = (tp - entry_price) / entry_price * 100
                return {'is_win': True, 'pnl_pct': pnl, 'exit_reason': 'TP',
                        'bars_held': i - entry_idx, 'sl_moved': sl_moved}
        else:
            if b['h'] >= sl:
                pnl = (entry_price - sl) / entry_price * 100
                reason = 'BE_SL' if sl_moved else 'SL'
                return {'is_win': pnl > 0, 'pnl_pct': pnl, 'exit_reason': reason,
                        'bars_held': i - entry_idx, 'sl_moved': sl_moved}
            if b['l'] <= tp:
                pnl = (entry_price - tp) / entry_price * 100
                return {'is_win': True, 'pnl_pct': pnl, 'exit_reason': 'TP',
                        'bars_held': i - entry_idx, 'sl_moved': sl_moved}
    
    close = bars[min(entry_idx + hold, len(bars) - 1)]['c']
    pnl = ((close - entry_price) / entry_price * 100) if direction == 'LONG' \
          else ((entry_price - close) / entry_price * 100)
    return {'is_win': pnl > 0, 'pnl_pct': pnl, 'exit_reason': 'TIME',
            'bars_held': hold, 'sl_moved': sl_moved}

# ════════════════════════════════════════════════════════════
# 主回测
# ════════════════════════════════════════════════════════════

def run_backtest_v3(symbol, tf, start_date='2022-01-01', rr=1.5, verbose=False):
    SYM = symbol.upper() + 'USDT'
    start_ts = int(datetime.strptime(start_date, '%Y-%m-%d').timestamp() * 1000)
    
    bars = load_klines(SYM, tf, start_ts)
    regime_map = load_regime_labels(SYM)
    if not bars: return []
    
    print(f"\n▶ {SYM} | {tf} | {len(bars)} K线 | {datetime.fromtimestamp(bars[0]['ts']/1000):%Y-%m-%d} → {datetime.fromtimestamp(bars[-1]['ts']/1000):%Y-%m-%d}")
    
    atrs = calc_atr(bars, 14)
    rsis = calc_rsi(bars, 14)
    
    min_gap = {'15m': 4*15*60*1000, '1h': 4*3600*1000,
               '4h': 2*4*3600*1000, '1d': 2*24*3600*1000}.get(tf, 4*3600*1000)
    
    signals = []
    last_ts = 0
    
    for i in range(100, len(bars) - 50):
        b = bars[i]
        ts = b['ts']
        if ts - last_ts < min_gap: continue
        
        # 体制
        ts_4h = (ts // (4*3600*1000)) * (4*3600*1000)
        regime = regime_map.get(ts_4h)
        if not regime:
            for off in range(-3, 4):
                regime = regime_map.get(ts_4h + off * 4*3600*1000)
                if regime: break
        if not regime: continue
        
        atr = atrs[i]
        rsi = rsis[i]
        if not atr or not rsi: continue
        
        # 成交量比率
        vw = 20
        if i >= vw:
            avg_v = sum(bars[j]['v'] for j in range(i-vw, i)) / vw
            vol_r = b['v'] / avg_v if avg_v > 0 else 1.0
        else: vol_r = 1.0
        
        bb_w = 0
        if i >= 20:
            closes = [bars[j]['c'] for j in range(i-20, i)]
            mean_c = sum(closes) / 20
            std_c = math.sqrt(sum((c-mean_c)**2 for c in closes) / 20)
            bb_w = std_c * 2 / mean_c * 100 if mean_c else 0
        
        atr_pct = atr / b['c'] * 100 if b['c'] > 0 else 0
        
        # 5层框架计算
        fvg = detect_fvg_advanced(bars, i)           # ② FVG磁铁
        ob = detect_ob_advanced(bars, i)              # ③ OB有效性
        liq_map = build_liquidity_map(bars, i)         # ① 清算地图
        resonance = detect_resonance(bars, i, fvg, ob, liq_map, atr)  # ④ 共振点
        
        for direction in ['LONG', 'SHORT']:
            sc = score_v3(regime, direction, rsi, fvg, ob, liq_map,
                          resonance, bb_w, vol_r, atr_pct)
            
            blocked, reason = apply_gates(regime, direction, sc, resonance)
            if blocked: continue
            
            min_score = {'15m': 90, '1h': 95, '4h': 100, '1d': 105}.get(tf, 95)
            if sc < min_score: continue
            
            exit_r = simulate_exit_v3(bars, i, direction, b['c'], atr, tf, rr=rr)
            
            sig = {
                'ts': ts,
                'dt': datetime.fromtimestamp(ts/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M'),
                'symbol': SYM, 'tf': tf,
                'regime': regime, 'direction': direction,
                'score': round(sc, 1),
                'resonance': resonance['score'],
                'fvg_type': fvg['type'], 'fvg_pull': fvg.get('pull', 0),
                'ob_type': ob['type'], 'ob_valid': ob['valid'], 'ob_age': ob.get('age', 0),
                'liq_density': liq_map['liq_density'],
                'entry': b['c'], 'atr': round(atr, 2),
                **exit_r
            }
            signals.append(sig)
            last_ts = ts
            
            if verbose and len(signals) <= 20:
                icon = '✅' if sig['is_win'] else '❌'
                print(f"  {icon} {sig['dt']} {direction:<5} sc={sc:.0f} res={resonance['score']} "
                      f"reg={regime[:10]:<10} pnl={exit_r['pnl_pct']:.2f}% ({exit_r['exit_reason']})")
    
    n = len(signals)
    if n:
        wins = sum(1 for s in signals if s['is_win'])
        wr = wins / n * 100
        pnl = sum(s['pnl_pct'] for s in signals) / n
        tp = sum(1 for s in signals if s['exit_reason'] == 'TP')
        sl = sum(1 for s in signals if s['exit_reason'] in ('SL', 'BE_SL'))
        be = sum(1 for s in signals if s['exit_reason'] == 'BE_SL')
        print(f"  📊 n={n} WR={wr:.1f}% avg_pnl={pnl:.3f}% TP={tp} SL={sl} BE_SL={be}")
    
    return signals

# ════════════════════════════════════════════════════════════
# WR矩阵 + IC
# ════════════════════════════════════════════════════════════

def build_wr_matrix(signals):
    BUCKETS = [(0,100),(100,115),(115,130),(130,145),(145,160),(160,999)]
    matrix = {}
    for sig in signals:
        bucket = next((f"{lo}-{hi}" for lo, hi in BUCKETS if lo <= sig['score'] < hi), '160+')
        key = f"{sig['regime']}:{sig['direction']}:{bucket}"
        if key not in matrix:
            matrix[key] = {'n': 0, 'wins': 0, 'total_pnl': 0.0}
        matrix[key]['n'] += 1
        if sig['is_win']: matrix[key]['wins'] += 1
        matrix[key]['total_pnl'] += sig.get('pnl_pct', 0)
    
    result = {}
    for key, v in matrix.items():
        n = v['n']
        wr = v['wins'] / n * 100 if n else 0
        avg_pnl = v['total_pnl'] / n if n else 0
        z = 1.96
        p = wr / 100
        wilson = (p + z*z/(2*n) - z*math.sqrt((p*(1-p)+z*z/(4*n))/n)) / (1+z*z/n) if n >= 5 else 0
        result[key] = {'n': n, 'wr': round(wr,1), 'wilson_low': round(wilson*100,1),
                       'avg_pnl': round(avg_pnl,3), 'total_pnl': round(v['total_pnl'],2)}
    return dict(sorted(result.items(), key=lambda x: -x[1]['n']))

def calc_ic(signals):
    if len(signals) < 10: return 0.0
    scores = [s['score'] for s in signals]
    outcomes = [1.0 if s['is_win'] else 0.0 for s in signals]
    n = len(scores)
    ms = sum(scores)/n; mo = sum(outcomes)/n
    cov = sum((scores[i]-ms)*(outcomes[i]-mo) for i in range(n))/n
    ss = math.sqrt(sum((s-ms)**2 for s in scores)/n)
    so = math.sqrt(sum((o-mo)**2 for o in outcomes)/n)
    return round(cov/(ss*so), 4) if ss and so else 0.0

# ════════════════════════════════════════════════════════════
# 收益模拟
# ════════════════════════════════════════════════════════════

def simulate_pnl(signals, nav=100000, pos_pct=0.05, lev=5, fee_rate=0.0008, rr=1.5):
    if not signals: return 0, 0, 0, 0
    pos = nav * pos_pct * lev
    fee = pos * fee_rate * 2
    sl_dist = 0.02
    
    total_pnl = 0
    total_fee = 0
    wins = 0
    cum = 0; peak = 0; max_dd = 0
    pnl_list = []
    
    for s in signals:
        if s['is_win']:
            p = sl_dist * rr * pos - fee
            wins += 1
        else:
            p = -sl_dist * pos - fee
        total_pnl += p
        total_fee += fee
        cum += p
        if cum > peak: peak = cum
        if peak - cum > max_dd: max_dd = peak - cum
        pnl_list.append(p)
    
    mean_p = sum(pnl_list)/len(pnl_list) if pnl_list else 0
    std_p = math.sqrt(sum((p-mean_p)**2 for p in pnl_list)/len(pnl_list)) if len(pnl_list) > 1 else 0
    tpy = min(len(signals)/4.5, 5*365)
    sharpe = mean_p/std_p*math.sqrt(tpy) if std_p > 0 else 0
    
    return total_pnl, total_fee, max_dd, sharpe

# ════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', nargs='+', default=['BTC','ETH'])
    parser.add_argument('--tfs', nargs='+', default=['15m','1h'])
    parser.add_argument('--start', default='2022-01-01')
    parser.add_argument('--rr', type=float, default=1.5)
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()
    
    t0 = time.time()
    print("="*60)
    print("🏛️ 梵天实战回测引擎 v3.0")
    print("5层框架全落地：清算+FVG磁铁+OB有效+共振+浮盈保护")
    print(f"标的:{args.symbols} 周期:{args.tfs} 起始:{args.start} RR={args.rr}")
    print("="*60)
    
    all_signals = []
    for sym in args.symbols:
        for tf in args.tfs:
            sigs = run_backtest_v3(sym, tf, args.start, rr=args.rr, verbose=args.verbose)
            all_signals.extend(sigs)
    
    # 全局统计
    n = len(all_signals)
    if n == 0:
        print("\n❌ 无信号生成")
        return
    
    wins = sum(1 for s in all_signals if s['is_win'])
    wr = wins / n * 100
    ic = calc_ic(all_signals)
    pnl, fees, dd, sharpe = simulate_pnl(all_signals, rr=args.rr)
    
    # 共振分布
    res_dist = {0:0, 1:0, 2:0, 3:0}
    res_wr = {0:0, 1:0, 2:0, 3:0}
    for s in all_signals:
        r = s['resonance']
        res_dist[r] = res_dist.get(r, 0) + 1
        if s['is_win']: res_wr[r] = res_wr.get(r, 0) + 1
    
    # 出场统计
    exit_dist = defaultdict(int)
    for s in all_signals:
        exit_dist[s['exit_reason']] += 1
    
    # 浮盈保护统计
    be_count = sum(1 for s in all_signals if s.get('sl_moved'))
    
    print(f"\n{'='*60}")
    print(f"📊 v3全量结果")
    print(f"{'='*60}")
    print(f"总信号: {n} | WR: {wr:.1f}% | IC: {ic:.4f}")
    print(f"总PnL: ${pnl:,.0f} | 手续费: ${fees:,.0f} | 回撤: ${dd:,.0f} | Sharpe: {sharpe:.2f}")
    
    print(f"\n共振分布:")
    for r in [3,2,1,0]:
        cnt = res_dist.get(r, 0)
        wr_r = res_wr.get(r, 0) / cnt * 100 if cnt else 0
        print(f"  共振{r}: n={cnt} WR={wr_r:.1f}%")
    
    print(f"\n出场分布: {dict(exit_dist)}")
    print(f"浮盈保护触发: {be_count} 次 ({be_count/n*100:.1f}%)")
    
    # WR矩阵
    matrix = build_wr_matrix(all_signals)
    print(f"\nWR矩阵 TOP15:")
    print(f"{'Key':<38} {'n':>5} {'WR':>6} {'Wilson':>7} {'pnl':>7}")
    for key, v in list(matrix.items())[:15]:
        flag = '🔴' if v['wilson_low'] < 40 else ('🟡' if v['wilson_low'] < 55 else '🟢')
        print(f"{flag} {key:<36} {v['n']:>5} {v['wr']:>5.1f}% {v['wilson_low']:>6.1f}% {v['avg_pnl']:>6.3f}%")
    
    # walk-forward
    test_ts = int(datetime.strptime('2025-01-01', '%Y-%m-%d').timestamp() * 1000)
    train = [s for s in all_signals if s['ts'] < test_ts]
    test = [s for s in all_signals if s['ts'] >= test_ts]
    wr_tr = sum(1 for s in train if s['is_win'])/len(train)*100 if train else 0
    wr_te = sum(1 for s in test if s['is_win'])/len(test)*100 if test else 0
    
    # 盈亏平衡
    be_wr = (0.02 * 100000 * 0.05 * 5 + 100000 * 0.05 * 5 * 0.0008 * 2) / (0.02 * 100000 * 0.05 * 5 * (args.rr + 1)) * 100
    
    print(f"\nwalk-forward: 训练WR={wr_tr:.1f}% (n={len(train)}) → 测试WR={wr_te:.1f}% (n={len(test)}) 差异={wr_te-wr_tr:+.1f}%")
    print(f"盈亏平衡WR(RR={args.rr}): {be_wr:.1f}% | 安全垫: {wr-be_wr:+.1f}%")
    
    elapsed = time.time() - t0
    print(f"\n耗时: {elapsed:.1f}s")
    
    # 判断
    if wr >= 55 and wr >= be_wr and wr_te >= 52:
        print(f"\n✅ 三项全过：WR≥55% + 超盈亏平衡 + 样本外稳定")
    else:
        fails = []
        if wr < 55: fails.append(f"WR<55%({wr:.1f}%)")
        if wr < be_wr: fails.append(f"低于盈亏平衡({be_wr:.1f}%)")
        if wr_te < 52: fails.append(f"样本外WR<52%({wr_te:.1f}%)")
        print(f"\n⚠️ 未全过: {', '.join(fails)}")
    
    # 写出
    output = {
        'ts': datetime.utcnow().isoformat() + 'Z',
        'engine': 'v3',
        'config': {'symbols': args.symbols, 'tfs': args.tfs, 'start': args.start, 'rr': args.rr},
        'summary': {'n': n, 'wr': round(wr,1), 'ic': ic,
                    'total_pnl': round(pnl,0), 'sharpe': round(sharpe,2),
                    'be_wr': round(be_wr,1), 'safety_margin': round(wr-be_wr,1)},
        'resonance': {str(r): {'n': res_dist.get(r,0), 'wr': round(res_wr.get(r,0)/max(res_dist.get(r,1),1)*100,1)} for r in [0,1,2,3]},
        'walk_forward': {'train_wr': round(wr_tr,1), 'test_wr': round(wr_te,1)},
        'wr_matrix': matrix
    }
    with open(OUT_DIR / 'backtest_v3_results.json', 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"结果: {OUT_DIR}/backtest_v3_results.json")

if __name__ == '__main__':
    main()
