"""
smc_engine.py · SMC智能资金结构引擎
brahma_brain · Phase 1

功能：
  - BOS / CHoCH 结构突破识别
  - Order Block (OB) 精确定位
  - FVG 公平价值缺口识别
  - 流动性猎杀点标注
  - Premium / Discount 区域判断
  - SMC综合评分（0~20分）
"""
from data_cache import get_klines, klines_to_ohlcv

# ═══════════════════════════════════════════════════════════════
# 一、结构分析（BOS / CHoCH）
# ═══════════════════════════════════════════════════════════════

def find_structure_points(highs: list, lows: list, lookback: int = 5) -> dict:
    """识别摆动高低点序列"""
    swings = []
    for i in range(lookback, len(highs) - lookback):
        if highs[i] >= max(highs[max(0,i-lookback):i]) and \
           highs[i] >= max(highs[i+1:i+lookback+1]):
            swings.append({'idx': i, 'type': 'HIGH', 'price': highs[i]})
        elif lows[i] <= min(lows[max(0,i-lookback):i]) and \
             lows[i] <= min(lows[i+1:i+lookback+1]):
            swings.append({'idx': i, 'type': 'LOW', 'price': lows[i]})

    # 提取序列
    sh = [s for s in swings if s['type'] == 'HIGH'][-6:]
    sl = [s for s in swings if s['type'] == 'LOW'][-6:]
    return {'highs': sh, 'lows': sl}

def detect_bos_choch(highs: list, lows: list, closes: list) -> dict:
    """识别BOS和CHoCH"""
    sp = find_structure_points(highs, lows)
    sh = sp['highs']
    sl = sp['lows']

    bos_list   = []
    choch_list = []

    price = closes[-1]

    if len(sh) >= 2:
        last_sh = sh[-1]['price']
        prev_sh = sh[-2]['price']

        # 上升BOS：当前价突破前高
        if price > last_sh and last_sh > prev_sh:
            bos_list.append({'type': 'BULL_BOS', 'level': last_sh,
                             'note': '突破前高，多头结构延续'})
        # 下降CHoCH：上升结构中价格跌破前HL（更高低点）
        if len(sl) >= 2:
            last_sl = sl[-1]['price']
            prev_sl = sl[-2]['price']
            if last_sl > prev_sl and price < last_sl:
                choch_list.append({'type': 'BEAR_CHOCH', 'level': last_sl,
                                   'note': '跌破前HL，多转空最早信号'})

    if len(sl) >= 2:
        last_sl = sl[-1]['price']
        prev_sl = sl[-2]['price']

        # 下降BOS：当前价跌破前低
        if price < last_sl and last_sl < prev_sl:
            bos_list.append({'type': 'BEAR_BOS', 'level': last_sl,
                             'note': '跌破前低，空头结构延续'})
        # 上升CHoCH：下降结构中价格突破前LH（更低高点）
        if len(sh) >= 2:
            last_sh = sh[-1]['price']
            prev_sh = sh[-2]['price']
            if last_sh < prev_sh and price > last_sh:
                choch_list.append({'type': 'BULL_CHOCH', 'level': last_sh,
                                   'note': '突破前LH，空转多最早信号'})

    # 市场结构判断
    if len(sh) >= 2 and len(sl) >= 2:
        if sh[-1]['price'] > sh[-2]['price'] and sl[-1]['price'] > sl[-2]['price']:
            structure = 'UPTREND'    # HH + HL
        elif sh[-1]['price'] < sh[-2]['price'] and sl[-1]['price'] < sl[-2]['price']:
            structure = 'DOWNTREND'  # LH + LL
        else:
            structure = 'RANGING'
    else:
        structure = 'UNKNOWN'

    return {
        'structure': structure,
        'bos':       bos_list,
        'choch':     choch_list,
        'swing_highs': sh,
        'swing_lows':  sl,
        'last_sh':   sh[-1]['price'] if sh else None,
        'last_sl':   sl[-1]['price'] if sl else None,
    }

# ═══════════════════════════════════════════════════════════════
# 二、Order Block 识别
# ═══════════════════════════════════════════════════════════════

def find_order_blocks(opens: list, highs: list, lows: list,
                      closes: list, lookback: int = 50) -> dict:
    """识别最近有效Order Block"""
    price   = closes[-1]
    bull_obs = []   # 看多OB（在当前价下方）
    bear_obs = []   # 看空OB（在当前价上方）

    start = max(0, len(closes) - lookback)

    for i in range(start, len(closes) - 3):
        # 看多OB：下跌K线 + 随后出现向上BOS
        if closes[i] < opens[i]:   # 阴线
            # 检查后续是否出现价格大幅上涨（BOS）
            future_high = max(highs[i+1:min(i+10, len(highs))])
            if future_high > highs[i] * 1.005:
                ob_high = highs[i]
                ob_low  = lows[i]
                if ob_low < price < ob_high * 1.05:   # 在OB附近或下方
                    bull_obs.append({
                        'type':   'BULL_OB',
                        'high':   round(ob_high, 8),
                        'low':    round(ob_low, 8),
                        'mid':    round((ob_high + ob_low) / 2, 8),
                        'idx':    i,
                        'dist_pct': round((price - ob_low) / ob_low * 100, 2),
                        'note':   f'看多OB区间 ${ob_low:.4f}~${ob_high:.4f}',
                    })

        # 看空OB：上涨K线 + 随后出现向下BOS
        if closes[i] > opens[i]:   # 阳线
            future_low = min(lows[i+1:min(i+10, len(lows))])
            if future_low < lows[i] * 0.995:
                ob_high = highs[i]
                ob_low  = lows[i]
                if ob_low * 0.95 < price < ob_high:
                    bear_obs.append({
                        'type':   'BEAR_OB',
                        'high':   round(ob_high, 8),
                        'low':    round(ob_low, 8),
                        'mid':    round((ob_high + ob_low) / 2, 8),
                        'idx':    i,
                        'dist_pct': round((ob_high - price) / price * 100, 2),
                        'note':   f'看空OB区间 ${ob_low:.4f}~${ob_high:.4f}',
                    })

    # 按距离排序，取最近的
    bull_obs.sort(key=lambda x: abs(x['dist_pct']))
    bear_obs.sort(key=lambda x: abs(x['dist_pct']))

    return {
        'bull_obs': bull_obs[:3],
        'bear_obs': bear_obs[:3],
        'nearest_bull_ob': bull_obs[0] if bull_obs else None,
        'nearest_bear_ob': bear_obs[0] if bear_obs else None,
    }

# ═══════════════════════════════════════════════════════════════
# 三、FVG 公平价值缺口识别
# ═══════════════════════════════════════════════════════════════

def find_fvg(highs: list, lows: list, closes: list, lookback: int = 50) -> dict:
    """识别FVG（公平价值缺口）"""
    price    = closes[-1]
    bull_fvg = []   # 看多FVG（K1高 < K3低）
    bear_fvg = []   # 看空FVG（K1低 > K3高）

    start = max(0, len(closes) - lookback)

    for i in range(start, len(closes) - 2):
        k1_high = highs[i]
        k1_low  = lows[i]
        k3_high = highs[i+2]
        k3_low  = lows[i+2]

        # 看多FVG：K1高点 < K3低点（向上跳空）
        if k1_high < k3_low:
            gap_size = k3_low - k1_high
            gap_pct  = gap_size / k1_high * 100
            if gap_pct > 0.3:   # [设计院 2026-05-30] 0.1→0.3% 过滤micro-FVG噪音
                # [A1修复] filled=价格已完全穿越FVG（不是「在FVG内」）
                # 现价在FVG内 = actively approaching，不算filled
                filled = (price > k3_low)  # 牛市FVG: 价格已涨过FVG顶部 → filled
                bull_fvg.append({
                    'type':     'BULL_FVG',
                    'top':      round(k3_low, 8),
                    'bottom':   round(k1_high, 8),
                    'mid':      round((k3_low + k1_high) / 2, 8),
                    'gap_pct':  round(gap_pct, 3),
                    'filled':   filled,
                    'idx':      i,
                    'note':     f'看多FVG ${k1_high:.4f}~${k3_low:.4f} ({gap_pct:.2f}%)',
                })

        # 看空FVG：K1低点 > K3高点（向下跳空）
        if k1_low > k3_high:
            gap_size = k1_low - k3_high
            gap_pct  = gap_size / k3_high * 100
            if gap_pct > 0.3:   # [设计院 2026-05-30] 0.1→0.3% 过滤micro-FVG噪音
                # [A1修复] 熊市FVG: 价格已跌穿FVG底部 → filled
                filled = (price < k3_high)  # 价格已跌穿熊市FVG底部 → filled
                bear_fvg.append({
                    'type':     'BEAR_FVG',
                    'top':      round(k1_low, 8),
                    'bottom':   round(k3_high, 8),
                    'mid':      round((k1_low + k3_high) / 2, 8),
                    'gap_pct':  round(gap_pct, 3),
                    'filled':   filled,
                    'idx':      i,
                    'note':     f'看空FVG ${k3_high:.4f}~${k1_low:.4f} ({gap_pct:.2f}%)',
                })

    # 只保留未填补的FVG，按距离排序
    bull_fvg = [f for f in bull_fvg if not f['filled']]
    bear_fvg = [f for f in bear_fvg if not f['filled']]

    # [设计院 2026-05-30] 方向约束：做空FVG必须在当前价上方，做多FVG必须在下方
    # 原排序「按距离最近」会选出当前价旁的micro-FVG，导致入场区逻辑倒置
    # 修复：先过滤方向，再按距离排序
    bull_fvg_valid = [f for f in bull_fvg if f['mid'] < price * 0.999]   # [A2修复] 做多：FVG在价格下方0.1%+(原0.3%过严)
    bear_fvg_valid = [f for f in bear_fvg if f['mid'] > price * 1.001]   # [A2修复] 做空：FVG在价格上方0.1%+(原0.3%过严)

    # 有效FVG按距离排序，降级用原始列表
    bull_fvg_valid.sort(key=lambda x: abs(x['mid'] - price))
    bear_fvg_valid.sort(key=lambda x: abs(x['mid'] - price))
    bull_fvg.sort(key=lambda x: abs(x['mid'] - price))
    bear_fvg.sort(key=lambda x: abs(x['mid'] - price))

    nearest_bull = bull_fvg_valid[0] if bull_fvg_valid else None   # 有效FVG优先
    nearest_bear = bear_fvg_valid[0] if bear_fvg_valid else None   # 有效FVG优先

    return {
        'bull_fvg':     bull_fvg[:3],
        'bear_fvg':     bear_fvg[:3],
        'nearest_bull': nearest_bull,
        'nearest_bear': nearest_bear,
        'magnet_up':    nearest_bear['mid'] if nearest_bear and nearest_bear['mid'] > price else None,
        'magnet_down':  nearest_bull['mid'] if nearest_bull and nearest_bull['mid'] < price else None,
    }

# ═══════════════════════════════════════════════════════════════
# 四、流动性猎杀点识别
# ═══════════════════════════════════════════════════════════════

def find_liquidity_pools(highs: list, lows: list, closes: list,
                         tolerance: float = 0.003) -> dict:
    """识别等高点/等低点（流动性猎杀池）"""
    price = closes[-1]
    lookback = min(len(highs), 100)

    # 寻找等高点（做空止损聚集）
    equal_highs = []
    for i in range(len(highs) - lookback, len(highs) - 5):
        for j in range(i+3, len(highs) - 2):
            diff = abs(highs[i] - highs[j]) / highs[i]
            if diff < tolerance:
                level = (highs[i] + highs[j]) / 2
                if level > price:
                    equal_highs.append({
                        'level':    round(level, 8),
                        'dist_pct': round((level - price) / price * 100, 2),
                        'note':     f'等高点（做空止损池） ${level:.4f}',
                    })

    # 寻找等低点（做多止损聚集）
    equal_lows = []
    for i in range(len(lows) - lookback, len(lows) - 5):
        for j in range(i+3, len(lows) - 2):
            diff = abs(lows[i] - lows[j]) / lows[i]
            if diff < tolerance:
                level = (lows[i] + lows[j]) / 2
                if level < price:
                    equal_lows.append({
                        'level':    round(level, 8),
                        'dist_pct': round((price - level) / price * 100, 2),
                        'note':     f'等低点（做多止损池） ${level:.4f}',
                    })

    # 去重并排序（取最近的）
    seen_h, seen_l = set(), set()
    unique_eh, unique_el = [], []
    for eh in sorted(equal_highs, key=lambda x: x['dist_pct']):
        key = round(eh['level'], 2)
        if key not in seen_h:
            seen_h.add(key); unique_eh.append(eh)
    for el in sorted(equal_lows, key=lambda x: x['dist_pct']):
        key = round(el['level'], 2)
        if key not in seen_l:
            seen_l.add(key); unique_el.append(el)

    return {
        'equal_highs':  unique_eh[:3],   # 上方流动性池（做空止损聚集）
        'equal_lows':   unique_el[:3],   # 下方流动性池（做多止损聚集）
        'nearest_above': unique_eh[0] if unique_eh else None,
        'nearest_below': unique_el[0] if unique_el else None,
    }

# ═══════════════════════════════════════════════════════════════
# 五、Premium / Discount 区域
# ═══════════════════════════════════════════════════════════════

def calc_premium_discount(high: float, low: float, price: float) -> dict:
    """计算Premium/Discount区域"""
    mid = (high + low) / 2
    pos = (price - low) / (high - low) if high != low else 0.5

    if pos > 0.618:
        zone = 'PREMIUM'
        bias = 'SHORT'
        note = '溢价区（>61.8%），机构倾向出货，适合做空'
    elif pos < 0.382:
        zone = 'DISCOUNT'
        bias = 'LONG'
        note = '折价区（<38.2%），机构倾向建仓，适合做多'
    elif pos > 0.5:
        zone = 'MILD_PREMIUM'
        bias = 'NEUTRAL_SHORT'
        note = '轻微溢价区（50%~61.8%），偏空'
    else:
        zone = 'MILD_DISCOUNT'
        bias = 'NEUTRAL_LONG'
        note = '轻微折价区（38.2%~50%），偏多'

    return {
        'zone':     zone,
        'bias':     bias,
        'position': round(pos, 3),
        'mid':      round(mid, 8),
        'note':     note,
    }

# ═══════════════════════════════════════════════════════════════
# 六、SMC综合评分（0~20分，供共振评分器使用）
# ═══════════════════════════════════════════════════════════════

def smc_score(structure: dict, obs: dict, fvgs: dict,
              liquidity: dict, pd_zone: dict, signal_dir: str) -> dict:
    """
    计算SMC评分（0~20分）
    signal_dir: 'LONG' or 'SHORT'
    """
    score = 0
    details = []

    # CHoCH确认（+6分）
    for ch in structure.get('choch', []):
        if signal_dir == 'LONG' and 'BULL' in ch['type']:
            score += 6; details.append(f'CHoCH看多确认 +6')
        elif signal_dir == 'SHORT' and 'BEAR' in ch['type']:
            score += 6; details.append(f'CHoCH看空确认 +6')

    # BOS方向一致（+4分）
    for b in structure.get('bos', []):
        if signal_dir == 'LONG' and 'BULL' in b['type']:
            score += 4; details.append(f'BOS看多 +4')
        elif signal_dir == 'SHORT' and 'BEAR' in b['type']:
            score += 4; details.append(f'BOS看空 +4')

    # OB回踩（+6分）
    if signal_dir == 'LONG' and obs.get('nearest_bull_ob'):
        ob = obs['nearest_bull_ob']
        if abs(ob['dist_pct']) < 1.0:
            score += 6; details.append(f'看多OB精确回踩 +6')
        elif abs(ob['dist_pct']) < 2.0:
            score += 3; details.append(f'看多OB附近 +3')
    if signal_dir == 'SHORT' and obs.get('nearest_bear_ob'):
        ob = obs['nearest_bear_ob']
        if abs(ob['dist_pct']) < 1.0:
            score += 6; details.append(f'看空OB精确回踩 +6')
        elif abs(ob['dist_pct']) < 2.0:
            score += 3; details.append(f'看空OB附近 +3')

    # FVG磁吸方向（+4分）
    if signal_dir == 'LONG' and fvgs.get('magnet_down'):
        score += 2; details.append(f'FVG向下磁吸支撑 +2')
    if signal_dir == 'SHORT' and fvgs.get('magnet_up'):
        score += 2; details.append(f'FVG向上磁吸目标 +2')

    # FVG回填入场（+4分额外）
    if signal_dir == 'LONG' and fvgs.get('nearest_bull'):
        score += 2; details.append(f'看多FVG区域 +2')
    if signal_dir == 'SHORT' and fvgs.get('nearest_bear'):
        score += 2; details.append(f'看空FVG区域 +2')

    # 流动性猎杀后反转（+7分）
    if signal_dir == 'LONG' and liquidity.get('nearest_below'):
        liq = liquidity['nearest_below']
        if liq['dist_pct'] < 0.5:
            score += 7; details.append(f'流动性猎杀后看多反转 +7')
    if signal_dir == 'SHORT' and liquidity.get('nearest_above'):
        liq = liquidity['nearest_above']
        if liq['dist_pct'] < 0.5:
            score += 7; details.append(f'流动性猎杀后看空反转 +7')

    # Premium/Discount区（+3分）
    if signal_dir == 'LONG' and pd_zone['bias'] in ('LONG', 'NEUTRAL_LONG'):
        score += 3; details.append(f'Discount区做多 +3')
    if signal_dir == 'SHORT' and pd_zone['bias'] in ('SHORT', 'NEUTRAL_SHORT'):
        score += 3; details.append(f'Premium区做空 +3')

    score = min(score, 20)

    return {
        'score':   score,
        'max':     20,
        'details': details,
        'grade':   '优' if score >= 15 else ('良' if score >= 10 else ('中' if score >= 5 else '差')),
    }

# ═══════════════════════════════════════════════════════════════
# 七、主入口
# ═══════════════════════════════════════════════════════════════

def analyze_smc(symbol: str, signal_dir: str = 'LONG',
                interval: str = '1h', lookback: int = 200) -> dict:
    """完整SMC分析"""
    raw  = get_klines(symbol, interval, lookback)
    ohlc = klines_to_ohlcv(raw)

    if not ohlc['c']:
        return {'error': f'无法获取{symbol}数据'}

    o, h, l, c = ohlc['o'], ohlc['h'], ohlc['l'], ohlc['c']
    price = c[-1]

    # 各模块分析
    structure  = detect_bos_choch(h, l, c)
    obs        = find_order_blocks(o, h, l, c)
    fvgs       = find_fvg(h, l, c)
    liquidity  = find_liquidity_pools(h, l, c)

    # Premium/Discount（用近期100根高低点）
    n = min(len(h), 100)
    pd_zone = calc_premium_discount(max(h[-n:]), min(l[-n:]), price)

    # SMC评分
    score = smc_score(structure, obs, fvgs, liquidity, pd_zone, signal_dir)

    return {
        'symbol':      symbol,
        'price':       price,
        'signal_dir':  signal_dir,
        'structure':   structure,
        'order_blocks': obs,
        'fvg':         fvgs,
        'liquidity':   liquidity,
        'pd_zone':     pd_zone,
        'score':       score,
    }

# ─── 快速测试 ────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'ETHUSDT'
    direction = sys.argv[2] if len(sys.argv) > 2 else 'SHORT'
    print(f'[SMC] 分析 {sym} · 方向={direction}')
    r = analyze_smc(sym, direction)

    if 'error' in r:
        print(f'错误: {r["error"]}')
    else:
        print(f'\n=== SMC结构分析 {sym} ===')
        print(f'价格:    ${r["price"]:,.4f}')
        print(f'结构:    {r["structure"]["structure"]}')
        print(f'BOS:     {[b["note"] for b in r["structure"]["bos"]]}')
        print(f'CHoCH:   {[c["note"] for c in r["structure"]["choch"]]}')
        print(f'Premium: {r["pd_zone"]["zone"]} pos={r["pd_zone"]["position"]}')
        print(f'         {r["pd_zone"]["note"]}')

        if r['order_blocks']['nearest_bull_ob']:
            ob = r['order_blocks']['nearest_bull_ob']
            print(f'看多OB:  ${ob["low"]:.4f}~${ob["high"]:.4f}  距={ob["dist_pct"]:+.2f}%')
        if r['order_blocks']['nearest_bear_ob']:
            ob = r['order_blocks']['nearest_bear_ob']
            print(f'看空OB:  ${ob["low"]:.4f}~${ob["high"]:.4f}  距={ob["dist_pct"]:+.2f}%')

        if r['fvg']['nearest_bull']:
            fvg = r['fvg']['nearest_bull']
            print(f'看多FVG: ${fvg["bottom"]:.4f}~${fvg["top"]:.4f}')
        if r['fvg']['nearest_bear']:
            fvg = r['fvg']['nearest_bear']
            print(f'看空FVG: ${fvg["bottom"]:.4f}~${fvg["top"]:.4f}')

        if r['liquidity']['nearest_above']:
            print(f'上方流动性池: ${r["liquidity"]["nearest_above"]["level"]:.4f}  '
                  f'距={r["liquidity"]["nearest_above"]["dist_pct"]:+.2f}%')
        if r['liquidity']['nearest_below']:
            print(f'下方流动性池: ${r["liquidity"]["nearest_below"]["level"]:.4f}  '
                  f'距={r["liquidity"]["nearest_below"]["dist_pct"]:.2f}%')

        sc = r['score']
        print(f'\nSMC评分: {sc["score"]}/20 ({sc["grade"]})')
        for d in sc['details']:
            print(f'  + {d}')
