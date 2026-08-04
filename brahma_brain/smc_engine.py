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


def _merge_fvg_list(fvg_list: list, gap_pct_threshold: float = 0.8) -> list:
    """[设计院升级 2026-08-04] 相邻FVG聚合：间距<gap_pct_threshold%时合并为大框
    解决图表显示「一个大蓝框」而梵天显示「10个碎片」的视觉差异
    """
    if not fvg_list:
        return []
    fvg_list = sorted(fvg_list, key=lambda x: x['bottom'])
    merged = [dict(fvg_list[0])]
    for f in fvg_list[1:]:
        last = merged[-1]
        gap = (f['bottom'] - last['top']) / last['top'] * 100
        if gap < gap_pct_threshold:
            last['top']     = max(last['top'], f['top'])
            last['bottom']  = min(last['bottom'], f['bottom'])
            last['mid']     = round((last['top'] + last['bottom']) / 2, 8)
            last['gap_pct'] = round((last['top'] - last['bottom']) / last['bottom'] * 100, 3)
            last['note']    = f'合并FVG ${last["bottom"]:.4f}~${last["top"]:.4f} ({last["gap_pct"]:.2f}%)'
            last['merged']  = True
        else:
            merged.append(dict(f))
    return merged


# ═══════════════════════════════════════════════════════════════
# 二、Order Block 识别
# ═══════════════════════════════════════════════════════════════

def find_order_blocks(opens: list, highs: list, lows: list,
                      closes: list, lookback: int = 200) -> dict:
    """识别最近有效Order Block
    [设计院升级 2026-08-04] lookback 50→200，覆盖完整结构历史
    """
    price   = closes[-1]
    bull_obs = []   # 看多OB（在当前价下方）
    bear_obs = []   # 看空OB（在当前价上方）

    start = max(0, len(closes) - lookback)

    total_bars = len(closes)
    for i in range(start, total_bars - 3):
        age_bars = total_bars - 1 - i  # 距当前K棒的距离（新鲜度核心字段）

        # [P0修复 2026-07-24] age_bars注入：OB新鲜度分层依赖此字段
        # MEMORY.md封印：age≤3=1.0x / 4-6=0.75x / 7-10=0.5x / >10=0.3x / broken=0x
        # 修复前：age_bars字段缺失 → brahma_core_entry.py的_ob_freshness_mult()始终取age=0 → 全部×1.0
        # 修复后：age_bars正确传入 → 过期OB自动降权

        # 看多OB：下跌K线 + 随后出现向上BOS
        if closes[i] < opens[i]:   # 阴线
            # 检查后续是否出现价格大幅上涨（BOS）
            future_high = max(highs[i+1:min(i+10, len(highs))])
            if future_high > highs[i] * 1.005:
                ob_high = highs[i]
                ob_low  = lows[i]
                if ob_low < price < ob_high * 1.05:   # 在OB附近或下方
                    # [P0修复] OB被价格穿越(broken)时直接过滤 — 失效OB不应参与评分
                    # Bull OB被穿越 = 价格曾经收盘在OB下方后重新进入 → 视为broken
                    # 简化判断：若当前价格已在OB上沿1.5%以上，说明已经穿越走远，非回踩
                    is_broken = (price > ob_high * 1.015)  # 穿越上方太远=已出OB范围
                    bull_obs.append({
                        'type':     'BULL_OB',
                        'high':     round(ob_high, 8),
                        'low':      round(ob_low, 8),
                        'mid':      round((ob_high + ob_low) / 2, 8),
                        'idx':      i,
                        'age_bars': age_bars,  # [P0修复] 新鲜度字段
                        'broken':   is_broken,
                        'dist_pct': round((price - ob_low) / ob_low * 100, 2),
                        'note':     f'看多OB区间 ${ob_low:.4f}~${ob_high:.4f} age={age_bars}bars',
                    })

        # 看空OB：上涨K线 + 随后出现向下BOS
        if closes[i] > opens[i]:   # 阳线
            future_low = min(lows[i+1:min(i+10, len(lows))])
            if future_low < lows[i] * 0.995:
                ob_high = highs[i]
                ob_low  = lows[i]
                if ob_low * 0.95 < price < ob_high:
                    is_broken = (price < ob_low * 0.985)  # 穿越下方太远=已出OB范围
                    bear_obs.append({
                        'type':     'BEAR_OB',
                        'high':     round(ob_high, 8),
                        'low':      round(ob_low, 8),
                        'mid':      round((ob_high + ob_low) / 2, 8),
                        'idx':      i,
                        'age_bars': age_bars,  # [P0修复] 新鲜度字段
                        'broken':   is_broken,
                        'dist_pct': round((ob_high - price) / price * 100, 2),
                        'note':     f'看空OB区间 ${ob_low:.4f}~${ob_high:.4f} age={age_bars}bars',
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

def find_fvg(highs: list, lows: list, closes: list, lookback: int = 500) -> dict:
    """识别FVG（公平价值缺口）
    [设计院升级 2026-08-04] lookback 50→500，全量扫描历史级别FVG
    """
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

    # [P1修复 2026-07-24] FVG填充方向阐检测：如果价格正在FVG区间内回落，说明在填充FVG，应标注确实填充目标
    # Bull FVG填充方向标注：如果价格在FVG内且向下运动 → active_fill=True，目标是FVG底部
    for f in bull_fvg:
        if not f['filled'] and f['bottom'] < closes[-1] <= f['top']:
            # 价格在FVG内，且最近4根收盘均在中为阴线 = 正在向下填充
            recent_4_closes = closes[-4:]
            down_count = sum(1 for j in range(1, len(recent_4_closes)) if recent_4_closes[j] < recent_4_closes[j-1])
            f['active_fill_down'] = (down_count >= 2)  # 连续下跌→填充警示
            f['fill_target'] = f['bottom']  # 填充目标 = FVG底部

    # 只保留未填补的FVG
    bull_fvg = [f for f in bull_fvg if not f['filled']]
    bear_fvg = [f for f in bear_fvg if not f['filled']]

    # [设计院升级 2026-08-04] FVG聚合：合并相邻小FVG为大框，与图表视觉对齐
    bull_fvg = _merge_fvg_list(bull_fvg, gap_pct_threshold=0.8)
    bear_fvg = _merge_fvg_list(bear_fvg, gap_pct_threshold=0.8)

    # [P3升级 2026-08-04 苏摩111批准] 历史FVG过滤
    # 距当前价>50%的FVG标记为historical_only，不参与nearest/supply/demand逻辑
    # 解决BTC日线扫到2020年$9k级FVG干扰当前交易决策的问题
    for f in bull_fvg:
        dist_from_price = abs(f['mid'] - price) / price
        f['historical_only'] = dist_from_price > 0.50
    for f in bear_fvg:
        dist_from_price = abs(f['mid'] - price) / price
        f['historical_only'] = dist_from_price > 0.50

    # 过滤掉historical_only再进行分类（保留原始列表用于完整性输出）
    bull_fvg_active = [f for f in bull_fvg if not f.get('historical_only')]
    bear_fvg_active = [f for f in bear_fvg if not f.get('historical_only')]

    # [FIX 2026-08-02 设计院自主] FVG双语义分类
    # BEAR FVG分两类：
    #   supply_bear = FVG在价格上方（供给区/阻力，做空最优入场区）
    #   target_bear = FVG在价格下方（未填充磁铁目标，做空TP参考）
    # BULL FVG同理（demand_bull=下方支撑，target_bull=上方磁铁）
    # nearest_bear 优先选supply（价格上方），无供给时退化为全局最近

    supply_bear  = [f for f in bear_fvg_active if f['mid'] > price * 1.001]   # 供给区（上方阻力）
    target_bear  = [f for f in bear_fvg_active if f['mid'] < price * 0.999]   # 目标区（下方磁铁）
    demand_bull  = [f for f in bull_fvg_active if f['mid'] < price * 0.999]   # 需求区（下方支撑）
    target_bull  = [f for f in bull_fvg_active if f['mid'] > price * 1.001]   # 目标区（上方磁铁）

    # [P0修复 2026-08-02 设计院] 价格在FVG内部时的边界盲区修复
    # 当price在FVG区间内（bottom < price < top），mid可能处于price附近±0.1%内
    # 导致既不进supply也不进target → nearest_bear=None BUG
    # 修复：将「价格穿行中的BEAR FVG」归入supply_bear（回测供给区，做空最高优先级）
    # 同理BULL FVG穿行中归入demand_bull
    for f in bear_fvg_active:
        if f['bottom'] < price < f['top'] and f not in supply_bear and f not in target_bear:
            f['active_retest'] = True  # 标注为「价格回测供给区」
            supply_bear.append(f)
    for f in bull_fvg_active:
        if f['bottom'] < price < f['top'] and f not in demand_bull and f not in target_bull:
            f['active_retest'] = True  # 标注为「价格回测需求区」
            demand_bull.append(f)

    for lst in (supply_bear, target_bear, demand_bull, target_bull, bull_fvg, bear_fvg):
        lst.sort(key=lambda x: abs(x['mid'] - price))

    # nearest_bear：优先供给区（上方+穿行中），无则取最近已穿越目标
    nearest_bear = supply_bear[0] if supply_bear else (target_bear[0] if target_bear else (bear_fvg[0] if bear_fvg else None))
    nearest_bull = demand_bull[0] if demand_bull else (target_bull[0] if target_bull else (bull_fvg[0] if bull_fvg else None))

    return {
        'bull_fvg':      bull_fvg_active[:3],   # 近期有效（已过滤历史级别）
        'bear_fvg':      bear_fvg_active[:3],   # 近期有效（已过滤历史级别）
        'bull_fvg_all':  bull_fvg[:5],          # 全量含historical_only
        'bear_fvg_all':  bear_fvg[:5],          # 全量含historical_only
        'supply_bear':   supply_bear[:2],   # 上方供给FVG（做空入场区）
        'target_bear':   target_bear[:2],   # 下方目标FVG（做空TP参考）
        'demand_bull':   demand_bull[:2],   # 下方需求FVG（做多入场区）
        'target_bull':   target_bull[:2],   # 上方目标FVG（做多TP参考）
        'nearest_bull':  nearest_bull,
        'nearest_bear':  nearest_bear,
        'magnet_up':     target_bull[0]['mid'] if target_bull else (nearest_bear['mid'] if nearest_bear and nearest_bear['mid'] > price else None),
        'magnet_down':   target_bear[0]['mid'] if target_bear else (nearest_bull['mid'] if nearest_bull and nearest_bull['mid'] < price else None),
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
# 七、清算集群精度升级（P2升级 设计院 2026-08-04 苏摩111批准）
# 旧法：简单等高/等低点配对（lookback=100，仅两两比较）
# 新法：多重摆动高/低点聚类 → 密度评分 → 分级猎杀目标
# ═══════════════════════════════════════════════════════════════

def find_liquidity_clusters(highs: list, lows: list, closes: list,
                             lookback: int = 200,
                             cluster_eps_pct: float = 0.8) -> dict:
    """[P2升级 设计院 2026-08-04 苏摩111批准] 清算集群精度算法
    
    原理：
      机构猎杀止损 = 把价格推到止损密集区
      前高附近  = 空头止损密集 → 价格突破前高=猎空止损
      前低附近  = 多头止损密集 → 价格跌破前低=猎多止损
      多个前高/前低聚集在同一价格区域 → 密度越高 = 猎杀价值越大
    
    算法：
      1. 识别所有摆动高/低点（严格定义：左右各2根）
      2. 按价格聚类（eps=cluster_eps_pct%）
      3. 计算每个集群的密度（点数/区间宽度）
      4. 按密度排序输出猎杀优先级
    
    Returns:
        {
          'short_liq': [...],    # 上方空头止损集群（猎空目标）
          'long_liq': [...],     # 下方多头止损集群（猎多目标）
          'nearest_short_liq': {...},
          'nearest_long_liq': {...},
          'hunt_score_up': float,   # 上方猎杀吸引力 0~10
          'hunt_score_down': float, # 下方猎杀吸引力 0~10
        }
    """
    price = closes[-1]
    n     = len(closes)
    start = max(4, n - lookback)

    # ── Step1: 识别摆动高/低点 ──
    s_highs, s_lows = [], []
    for i in range(start + 2, n - 2):
        if (highs[i] > highs[i-1] and highs[i] > highs[i-2] and
                highs[i] > highs[i+1] and highs[i] > highs[i+2]):
            s_highs.append({'price': highs[i], 'idx': i, 'age': n - 1 - i})
        if (lows[i] < lows[i-1] and lows[i] < lows[i-2] and
                lows[i] < lows[i+1] and lows[i] < lows[i+2]):
            s_lows.append({'price': lows[i], 'idx': i, 'age': n - 1 - i})

    # ── Step2: 价格聚类（eps = cluster_eps_pct% 内的点合并） ──
    def cluster_points(pts: list, eps_pct: float) -> list:
        """按价格聚类，返回集群列表"""
        if not pts:
            return []
        pts = sorted(pts, key=lambda x: x['price'])
        clusters = []
        cur = [pts[0]]
        for p in pts[1:]:
            gap = abs(p['price'] - cur[-1]['price']) / cur[-1]['price'] * 100
            if gap <= eps_pct:
                cur.append(p)
            else:
                clusters.append(cur)
                cur = [p]
        clusters.append(cur)
        return clusters

    # ── Step3: 计算集群属性 ──
    def calc_cluster(cluster: list, direction: str, price: float) -> dict:
        prices = [c['price'] for c in cluster]
        lo = min(prices)
        hi = max(prices)
        mid = (lo + hi) / 2
        n_pts = len(prices)
        width_pct = (hi - lo) / lo * 100 if lo > 0 else 0

        # 密度：点数越多、区间越窄 = 密度越高 = 猎杀价值越大
        density = n_pts / max(width_pct, 0.01)

        # 新鲜度：近期形成的集群权重更高
        avg_age = sum(c['age'] for c in cluster) / n_pts
        freshness = max(0.0, 1.0 - avg_age / lookback)

        # 距离
        dist_pct = (mid - price) / price * 100

        # 综合评分 0~10
        score = min(10.0, round(
            n_pts * 1.5            # 点数加成
            + density * 0.5        # 密度加成
            + freshness * 2.0      # 新鲜度加成
            + (1.0 if n_pts >= 3 else 0)  # 三重以上+1
        , 2))

        return {
            'lo':         round(lo, 2),
            'hi':         round(hi, 2),
            'mid':        round(mid, 2),
            'n_pts':      n_pts,
            'density':    round(density, 3),
            'freshness':  round(freshness, 3),
            'avg_age':    round(avg_age, 1),
            'score':      score,
            'dist_pct':   round(dist_pct, 2),
            'direction':  direction,
            'note':       (
                f'{"🔴空头止损集群" if direction=="SHORT" else "🟢多头止损集群"} '
                f'${lo:.2f}~${hi:.2f}  n={n_pts}  score={score}'
            ),
        }

    # ── Step4: 分方向聚类 ──
    # 上方：前高聚集 = 空头止损集群（猎空目标）
    above_highs = [p for p in s_highs if p['price'] > price]
    below_lows  = [p for p in s_lows  if p['price'] < price]

    sh_clusters_raw = cluster_points(above_highs, cluster_eps_pct)
    sl_clusters_raw = cluster_points(below_lows,  cluster_eps_pct)

    short_liq = []
    for cl in sh_clusters_raw:
        if cl:
            short_liq.append(calc_cluster(cl, 'SHORT', price))

    long_liq = []
    for cl in sl_clusters_raw:
        if cl:
            long_liq.append(calc_cluster(cl, 'LONG', price))

    # 按评分降序排列（猎杀价值最高的在前）
    short_liq.sort(key=lambda x: x['score'], reverse=True)
    long_liq.sort(key=lambda x:  x['score'], reverse=True)

    # ── Step5: 猎杀方向吸引力评分 ──
    # 上方猎杀吸引力 = 最近上方集群评分 × 密度加成
    nearest_short = min(short_liq, key=lambda x: abs(x['dist_pct'])) if short_liq else None
    nearest_long  = min(long_liq,  key=lambda x: abs(x['dist_pct'])) if long_liq  else None

    hunt_up   = nearest_short['score'] if nearest_short else 0
    hunt_down = nearest_long['score']  if nearest_long  else 0

    # 三重以上集群标记为「高价值猎杀目标」
    prime_short = [c for c in short_liq if c['n_pts'] >= 3]
    prime_long  = [c for c in long_liq  if c['n_pts'] >= 3]

    return {
        'short_liq':        short_liq[:5],     # 上方空头止损集群
        'long_liq':         long_liq[:5],      # 下方多头止损集群
        'prime_short':      prime_short[:3],   # 高密度空头集群（≥3重）
        'prime_long':       prime_long[:3],    # 高密度多头集群（≥3重）
        'nearest_short_liq': nearest_short,
        'nearest_long_liq':  nearest_long,
        'hunt_score_up':    round(hunt_up, 2),
        'hunt_score_down':  round(hunt_down, 2),
        'n_sh_swings':      len(s_highs),
        'n_sl_swings':      len(s_lows),
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



def find_ob_smc_standard(opens: list, highs: list, lows: list,
                          closes: list, lookback: int = 200) -> dict:
    """[P1升级 设计院 2026-08-04 苏摩111批准] SMC公认标准OB定义
    
    与 find_order_blocks() 的核心差异：
    - 旧法：反向K线 + 后续价格反转（近似定义）
    - 新法：找摆动高/低点 → 往前找「结构突破前最后一根反向K线」
            这才是机构真正建仓的位置（ICT/SMC公认标准）
    
    BULL OB = 上涨BOS前，最后一根下跌K线（阴线）的high~low区间
    BEAR OB = 下跌BOS前，最后一根上涨K线（阳线）的high~low区间
    """
    price = closes[-1]
    bull_obs = []
    bear_obs = []

    n = len(closes)
    start = max(4, n - lookback)

    # Step1: 找摆动高点（swing high）和摆动低点（swing low）
    swing_highs = []
    swing_lows  = []
    for i in range(start + 2, n - 2):
        # 摆动高点：左右各2根都低于它
        if (highs[i] > highs[i-1] and highs[i] > highs[i-2] and
                highs[i] > highs[i+1] and highs[i] > highs[i+2]):
            swing_highs.append({'idx': i, 'price': highs[i]})
        # 摆动低点：左右各2根都高于它
        if (lows[i] < lows[i-1] and lows[i] < lows[i-2] and
                lows[i] < lows[i+1] and lows[i] < lows[i+2]):
            swing_lows.append({'idx': i, 'price': lows[i]})

    # Step2: 对每个摆动高点，找其后发生BOS向上的情况
    # BOS向上 = 后续价格突破该摆动高点 → 找摆动高点前最后一根阴线 = BULL OB
    for sh in swing_highs[-12:]:
        sh_idx   = sh['idx']
        sh_price = sh['price']
        # 在sh_idx之后寻找BOS（价格收盘突破sh_price）
        bos_confirmed = False
        for j in range(sh_idx + 1, min(sh_idx + 25, n)):
            if closes[j] > sh_price:
                bos_confirmed = True
                break
        if not bos_confirmed:
            continue
        # 找sh_idx前最后一根下跌K线（阴线）作为BULL OB
        for k in range(sh_idx, max(sh_idx - 8, start), -1):
            if closes[k] < opens[k]:  # 阴线
                ob_lo = round(lows[k], 8)
                ob_hi = round(highs[k], 8)
                age   = n - 1 - k
                # 未被缓解（price还在OB上方或内部）
                is_broken = price < ob_lo * 0.99
                if not is_broken:
                    bull_obs.append({
                        'type':      'BULL_OB_SMC',
                        'high':      ob_hi,
                        'low':       ob_lo,
                        'mid':       round((ob_hi + ob_lo) / 2, 8),
                        'age_bars':  age,
                        'broken':    is_broken,
                        'dist_pct':  round((price - ob_lo) / ob_lo * 100, 2),
                        'note':      f'SMC标准BULL_OB ${ob_lo:.4f}~${ob_hi:.4f} age={age}',
                        'method':    'smc_standard',
                    })
                break

    # Step3: 对每个摆动低点，找其后发生BOS向下的情况
    # BOS向下 = 后续价格跌破该摆动低点 → 找摆动低点前最后一根阳线 = BEAR OB
    for sl in swing_lows[-12:]:
        sl_idx   = sl['idx']
        sl_price = sl['price']
        bos_confirmed = False
        for j in range(sl_idx + 1, min(sl_idx + 25, n)):
            if closes[j] < sl_price:
                bos_confirmed = True
                break
        if not bos_confirmed:
            continue
        for k in range(sl_idx, max(sl_idx - 8, start), -1):
            if closes[k] > opens[k]:  # 阳线
                ob_lo = round(lows[k], 8)
                ob_hi = round(highs[k], 8)
                age   = n - 1 - k
                is_broken = price > ob_hi * 1.01
                if not is_broken:
                    bear_obs.append({
                        'type':      'BEAR_OB_SMC',
                        'high':      ob_hi,
                        'low':       ob_lo,
                        'mid':       round((ob_hi + ob_lo) / 2, 8),
                        'age_bars':  age,
                        'broken':    is_broken,
                        'dist_pct':  round((ob_hi - price) / price * 100, 2),
                        'note':      f'SMC标准BEAR_OB ${ob_lo:.4f}~${ob_hi:.4f} age={age}',
                        'method':    'smc_standard',
                    })
                break

    # 去重 + 按距离排序
    seen_b, seen_s = set(), set()
    bull_unique, bear_unique = [], []
    for ob in sorted(bull_obs, key=lambda x: abs(x['dist_pct'])):
        k = (round(ob['low'], 0), round(ob['high'], 0))
        if k not in seen_b:
            seen_b.add(k)
            bull_unique.append(ob)
    for ob in sorted(bear_obs, key=lambda x: abs(x['dist_pct'])):
        k = (round(ob['low'], 0), round(ob['high'], 0))
        if k not in seen_s:
            seen_s.add(k)
            bear_unique.append(ob)

    return {
        'bull_obs_smc': bull_unique[:4],
        'bear_obs_smc': bear_unique[:4],
        'nearest_bull_smc': bull_unique[0] if bull_unique else None,
        'nearest_bear_smc': bear_unique[0] if bear_unique else None,
    }


# ═══════════════════════════════════════════════════════════════
# 六、多周期共振层（P1升级 设计院 2026-08-04 苏摩111批准）
# 核心逻辑：同一价格区域在日线/4H/1H同时存在FVG/OB → 权重叠加
# 日线FVG权重×3，4H权重×2，1H权重×1
# 三层共振 = 机构核心操作区，信号质量最高
# ═══════════════════════════════════════════════════════════════

def _zones_overlap(lo1: float, hi1: float, lo2: float, hi2: float) -> bool:
    """判断两个区间是否重叠"""
    return lo1 < hi2 and lo2 < hi1


def _overlap_ratio(lo1: float, hi1: float, lo2: float, hi2: float) -> float:
    """计算重叠比例（相对于较小区间）"""
    overlap = max(0.0, min(hi1, hi2) - max(lo1, lo2))
    smaller = min(hi1 - lo1, hi2 - lo2)
    return overlap / smaller if smaller > 0 else 0.0


def calc_confluence(multi_results: dict, signal_dir: str = 'LONG') -> dict:
    """[设计院 P1升级 2026-08-04] 多周期FVG/OB共振评分
    
    Args:
        multi_results: {'1d': smc_result, '4h': smc_result, '1h': smc_result}
        signal_dir: 'LONG' or 'SHORT'
    
    Returns:
        {
          'confluence_zones': [...],  # 共振区列表，含权重
          'top_zone': {...},          # 最强共振区
          'score': float,             # 总共振评分 0~10
          'grade': str,               # S/A/B/C/D
          'detail': [...]             # 评分明细
        }
    """
    weights = {'1d': 3.0, '4h': 2.0, '1h': 1.0}
    all_zones = []

    for tf, result in multi_results.items():
        if not result or 'error' in result:
            continue
        w = weights.get(tf, 1.0)
        price = result.get('price', 0)

        # FVG层
        fvg = result.get('fvg', {})
        if signal_dir == 'LONG':
            fvg_list = fvg.get('bull_fvg', []) + fvg.get('demand_bull', [])
        else:
            fvg_list = fvg.get('bear_fvg', []) + fvg.get('supply_bear', [])

        seen_fvg = set()
        for f in fvg_list:
            key = (round(f['bottom'], 0), round(f['top'], 0))
            if key in seen_fvg:
                continue
            seen_fvg.add(key)
            in_zone = f['bottom'] <= price <= f['top']
            all_zones.append({
                'tf': tf, 'type': 'FVG', 'dir': signal_dir,
                'lo': f['bottom'], 'hi': f['top'],
                'mid': f['mid'], 'weight': w,
                'in_zone': in_zone,
                'gap_pct': f.get('gap_pct', 0),
                'merged': f.get('merged', False),
            })

        # OB层
        obs = result.get('order_blocks', {})
        if signal_dir == 'LONG':
            ob_list = [obs['nearest_bull_ob']] if obs.get('nearest_bull_ob') else []
            ob_list += obs.get('bull_obs', [])
        else:
            ob_list = [obs['nearest_bear_ob']] if obs.get('nearest_bear_ob') else []
            ob_list += obs.get('bear_obs', [])

        seen_ob = set()
        for ob in ob_list:
            if not ob:
                continue
            key = (round(ob['low'], 0), round(ob['high'], 0))
            if key in seen_ob:
                continue
            seen_ob.add(key)
            in_zone = ob['low'] <= price <= ob['high']
            all_zones.append({
                'tf': tf, 'type': 'OB', 'dir': signal_dir,
                'lo': ob['low'], 'hi': ob['high'],
                'mid': ob['mid'], 'weight': w * 0.8,  # OB权重略低于FVG
                'in_zone': in_zone,
                'age_bars': ob.get('age_bars', 0),
                'broken': ob.get('broken', False),
            })

    # ── 聚类：找重叠区域，叠加权重 ──
    confluence_zones = []
    used = [False] * len(all_zones)

    for i, z in enumerate(all_zones):
        if used[i]:
            continue
        cluster = [z]
        total_weight = z['weight']
        tfs_hit = {z['tf']}
        types_hit = {z['type']}

        for j, z2 in enumerate(all_zones):
            if i == j or used[j]:
                continue
            if _zones_overlap(z['lo'], z['hi'], z2['lo'], z2['hi']):
                ratio = _overlap_ratio(z['lo'], z['hi'], z2['lo'], z2['hi'])
                if ratio >= 0.3:  # 至少30%重叠才算共振
                    cluster.append(z2)
                    total_weight += z2['weight']
                    tfs_hit.add(z2['tf'])
                    types_hit.add(z2['type'])
                    used[j] = True

        used[i] = True

        # 共振区边界取集群的并集
        lo = min(c['lo'] for c in cluster)
        hi = max(c['hi'] for c in cluster)
        mid = (lo + hi) / 2

        # 多周期加成：覆盖周期数决定等级
        tf_bonus = (len(tfs_hit) - 1) * 1.5  # 每多一个周期+1.5分
        fvg_ob_bonus = 1.0 if len(types_hit) > 1 else 0.0  # FVG+OB同时命中+1

        confluence_zones.append({
            'lo': round(lo, 2),
            'hi': round(hi, 2),
            'mid': round(mid, 2),
            'weight': round(total_weight + tf_bonus + fvg_ob_bonus, 2),
            'tfs': sorted(tfs_hit),
            'types': sorted(types_hit),
            'n_tfs': len(tfs_hit),
            'n_zones': len(cluster),
            'tf_bonus': tf_bonus,
            'any_in_zone': any(c.get('in_zone') for c in cluster),
        })

    # 按权重排序
    confluence_zones.sort(key=lambda x: x['weight'], reverse=True)

    # 评分归一化 0~10
    max_possible = weights['1d'] + weights['4h'] + weights['1h'] + 3.0 + 1.0  # 9.0
    top_w = confluence_zones[0]['weight'] if confluence_zones else 0
    score = round(min(top_w / max_possible * 10, 10.0), 2)

    # 等级
    if score >= 8.0:   grade = 'S（三周期共振）'
    elif score >= 6.0: grade = 'A（双周期共振）'
    elif score >= 4.0: grade = 'B（单周期有效）'
    elif score >= 2.0: grade = 'C（弱信号）'
    else:              grade = 'D（无共振）'

    detail = []
    for cz in confluence_zones[:3]:
        detail.append(
            f'[{"+".join(cz["tfs"])}] {cz["lo"]}~{cz["hi"]}  '
            f'w={cz["weight"]}  tfs={cz["n_tfs"]}  types={"+".join(cz["types"])}'
        )

    return {
        'confluence_zones': confluence_zones[:5],
        'top_zone': confluence_zones[0] if confluence_zones else None,
        'score': score,
        'grade': grade,
        'detail': detail,
    }


def analyze_smc_multi(symbol: str, signal_dir: str = 'LONG') -> dict:
    """[P1升级 设计院 2026-08-04 苏摩111批准] 三周期联合SMC分析
    
    替代单一 analyze_smc()，提供日线+4H+1H三层融合视角：
      1D → 宏观结构 + 历史级FVG/OB（权重×3）
      4H → 中期结构 + 核心FVG/OB（权重×2）
      1H → 入场精确FVG/OB（权重×1）
    
    核心价值：多周期共振评分，三层叠加=机构核心操作区
    """
    tfs_config = [
        ('1d', 365),
        ('4h', 400),
        ('1h', 500),
    ]
    results = {}
    for tf, limit in tfs_config:
        try:
            results[tf] = analyze_smc(symbol, signal_dir, tf, limit)
        except Exception as e:
            results[tf] = {'error': str(e)}

    # 多周期共振评分
    confluence = calc_confluence(results, signal_dir)

    # 主结构取1H（入场精度），宏观背景取1D
    primary   = results.get('1h', {})
    macro     = results.get('1d', {})
    mid_term  = results.get('4h', {})

    # 综合价格地图：收集所有周期的FVG，按与当前价的距离分层
    price = primary.get('price', 0) or mid_term.get('price', 0) or macro.get('price', 0)
    all_fvg_map = []
    for tf, res in results.items():
        if 'error' in res or not res:
            continue
        fvg = res.get('fvg', {})
        w = {'1d': 3, '4h': 2, '1h': 1}[tf]
        for f in fvg.get('bull_fvg', [])[:4]:
            all_fvg_map.append({**f, 'tf': tf, 'direction': 'LONG', 'weight': w})
        for f in fvg.get('bear_fvg', [])[:4]:
            all_fvg_map.append({**f, 'tf': tf, 'direction': 'SHORT', 'weight': w})
    all_fvg_map.sort(key=lambda x: abs(x['mid'] - price) if price else 0)

    return {
        'symbol':       symbol,
        'price':        price,
        'signal_dir':   signal_dir,
        '1d':           macro,
        '4h':           mid_term,
        '1h':           primary,
        'confluence':   confluence,
        'fvg_map':      all_fvg_map[:12],   # 全周期FVG价格地图
        'top_zone':     confluence.get('top_zone'),
        'smc_grade':    confluence.get('grade', 'D'),
        'smc_score':    confluence.get('score', 0),
    }


# ─── 快速测试 ────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'ETHUSDT'
    direction = sys.argv[2] if len(sys.argv) > 2 else 'SHORT'
    pass  # [静默]
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
# P3封印标记 2026-08-04T08:34:06Z
