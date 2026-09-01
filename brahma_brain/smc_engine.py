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
# ponytail: SMC引擎1107行，教科书级结构识别，行数合理
# 可优化: BOS/CHoCH检测可提取为独立模块复用
from data_cache import get_klines, klines_to_ohlcv

# 一、结构分析（BOS / CHoCH）

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


# 二、Order Block 识别

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

# 三、FVG 公平价值缺口识别

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

# 四、流动性猎杀点识别

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


# 七、清算集群精度升级（P2升级 设计院 2026-08-04 苏摩111批准）
# 旧法：简单等高/等低点配对（lookback=100，仅两两比较）
# 新法：多重摆动高/低点聚类 → 密度评分 → 分级猎杀目标

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


# 五、Premium / Discount 区域

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

# 六、SMC综合评分（0~20分，供共振评分器使用）

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

# 七、主入口

def analyze_smc(symbol: str, signal_dir: str = 'LONG',
                interval: str = '1h', lookback: int = 200) -> dict:
    """完整SMC分析
    [设计院升级 2026-08-25 苏摩111]
    15m周期自动扩展到500根（覆盖~5天宏观结构），1h保持200根
    """
    # 15m周期自动扩展lookback，覆盖更多历史FVG和流动性池
    if interval == '15m' and lookback < 500:
        lookback = 500
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


# 六、多周期共振层（P1升级 设计院 2026-08-04 苏摩111批准）
# 核心逻辑：同一价格区域在日线/4H/1H同时存在FVG/OB → 权重叠加
# 日线FVG权重×3，4H权重×2，1H权重×1
# 三层共振 = 机构核心操作区，信号质量最高

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


# ── 多级别FVG叠加分析 [设计院升级 2026-08-25 苏摩111] ─────────────
def analyze_smc_multi_tf(symbol: str, signal_dir: str = 'LONG',
                          base_tf: str = '15m') -> dict:
    """
    多级别SMC分析 — 15m精确入场 + 1H宏观结构叠加
    
    设计目标：
      解决「梵天只看近期200根，宏观FVG/EQH看不到」的问题
      ① base_tf(15m): lookback=500根，近期精确结构
      ② higher_tf(1H): lookback=200根，宏观FVG权重更大
    
    返回额外字段：
      htf_fvg       : 1H级别FVG（宏观支撑/压力）
      htf_liquidity : 1H级别流动性池（宏观EQH/EQL）
      multi_tf_zones: 两级别合并的关键价位列表
    """
    # base TF分析
    base = analyze_smc(symbol, signal_dir, interval=base_tf, lookback=500)
    price = base.get('price', 0)

    # Higher TF (1H) 分析
    higher_tf = '1h' if base_tf == '15m' else '4h'
    try:
        htf_raw  = get_klines(symbol, higher_tf, 200)
        htf_ohlc = klines_to_ohlcv(htf_raw)
        h1h = htf_ohlc['h']
        l1h = htf_ohlc['l']
        c1h = htf_ohlc['c']
        htf_fvg    = find_fvg(h1h, l1h, c1h, lookback=500)
        htf_liq    = find_liquidity_pools(h1h, l1h, c1h)
        htf_struct = detect_bos_choch(h1h, l1h, c1h)
    except Exception as e:
        htf_fvg  = {'bull_fvg': [], 'bear_fvg': []}
        htf_liq  = {'equal_highs': [], 'equal_lows': []}
        htf_struct = {'structure': 'UNKNOWN', 'bos': [], 'choch': []}

    # 合并关键价位（按距当前价由近到远排序）
    zones = []

    # 15m FVG
    for fvg_item in base.get('fvg', {}).get('bull_fvg', [])[:3]:
        zones.append({'level': fvg_item['mid'], 'type': 'BULL_FVG_15m',
                      'range': (fvg_item['bottom'], fvg_item['top']), 'weight': 1.0})
    for fvg_item in base.get('fvg', {}).get('bear_fvg', [])[:3]:
        zones.append({'level': fvg_item['mid'], 'type': 'BEAR_FVG_15m',
                      'range': (fvg_item['bottom'], fvg_item['top']), 'weight': 1.0})

    # 1H FVG（权重更高）
    for fvg_item in htf_fvg.get('bull_fvg', [])[:3]:
        zones.append({'level': fvg_item['mid'], 'type': 'BULL_FVG_1H',
                      'range': (fvg_item['bottom'], fvg_item['top']), 'weight': 1.5})
    for fvg_item in htf_fvg.get('bear_fvg', [])[:3]:
        zones.append({'level': fvg_item['mid'], 'type': 'BEAR_FVG_1H',
                      'range': (fvg_item['bottom'], fvg_item['top']), 'weight': 1.5})

    # 1H EQH/EQL（宏观流动性猎杀位）
    for eq in htf_liq.get('equal_highs', [])[:3]:
        zones.append({'level': eq['level'], 'type': 'EQH_1H', 'weight': 2.0})
    for eq in htf_liq.get('equal_lows', [])[:3]:
        zones.append({'level': eq['level'], 'type': 'EQL_1H', 'weight': 2.0})

    # 按距当前价排序
    if price:
        zones.sort(key=lambda z: abs(z['level'] - price))

    # 整合结果
    result = dict(base)
    result['htf_interval']  = higher_tf
    result['htf_fvg']       = htf_fvg
    result['htf_liquidity'] = htf_liq
    result['htf_structure'] = htf_struct
    result['multi_tf_zones'] = zones[:12]  # 最近12个关键价位
    result['base_tf']        = base_tf

    return result
# [设计院升级 2026-08-25 苏摩111 multi-tf-fvg封印]


# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/smc_resonance.py ══
"""
smc_resonance.py — 梵天强制前置链路模块
2026-08-31 苏摩111封印

职责：
  P1: FVG磁铁→OB有效性→清算地图→共振点 四步强制链路
  P2: 姓赵不宣VIP模版自动生成

接入位置：brahma_full_report.run_full_analysis() 精度执行层之后
"""

from typing import Optional


# ─────────────────────────────────────────────
# P1: 强制前置链路四步分析
# ─────────────────────────────────────────────

def run_smc_resonance(r: dict) -> dict:
    """
    强制走FVG→OB→清算→共振点四步链路
    返回结构化结果供VIP模版使用
    """
    price    = r.get('price', 0)
    symbol   = r.get('symbol', '')
    smc      = r.get('smc', {})
    ob_data  = smc.get('order_blocks', {})
    fvg_data = smc.get('fvg', {})
    liq_heat = r.get('_liq_heatmap', {})
    clusters = liq_heat.get('clusters', []) if isinstance(liq_heat, dict) else []
    atr1h    = r.get('momentum', {}).get('atr_1h', price * 0.01)
    min_sl   = atr1h * 1.5

    result = {
        'price': price,
        'symbol': symbol,
        'atr1h': round(atr1h, 2),
        'min_sl_dist': round(min_sl, 2),
        # Step1
        'bull_fvg': [],
        'bear_fvg': [],
        'fvg_magnet_dir': None,
        'fvg_magnet_target': None,
        # Step2
        'valid_bear_obs': [],
        'valid_bull_obs': [],
        # Step3
        'liq_up': [],
        'liq_dn': [],
        # Step4
        'resonance_short': None,
        'resonance_long': None,
        'verdict': 'WAIT',  # WAIT / SHORT / LONG
        'verdict_reason': '',
    }

    # ── Step1: FVG磁铁 ──
    bull_fvgs = fvg_data.get('bull_fvg', [])
    bear_fvgs = fvg_data.get('bear_fvg', [])

    for f in bull_fvgs:
        lo = f.get('bottom', 0); hi = f.get('top', 0); mid = f.get('mid', 0)
        filled = f.get('filled', False)
        if not filled and hi > 0:
            result['bull_fvg'].append({'lo': lo, 'hi': hi, 'mid': mid, 'gap_pct': f.get('gap_pct', 0)})

    for f in bear_fvgs:
        lo = f.get('bottom', 0); hi = f.get('top', 0); mid = f.get('mid', 0)
        filled = f.get('filled', False)
        if not filled and hi > 0:
            result['bear_fvg'].append({'lo': lo, 'hi': hi, 'mid': mid, 'gap_pct': f.get('gap_pct', 0)})

    # 磁铁方向判断
    bull_above = [f for f in result['bull_fvg'] if f['lo'] > price]
    bear_below = [f for f in result['bear_fvg'] if f['hi'] < price]
    bull_contain = [f for f in result['bull_fvg'] if f['lo'] <= price <= f['hi']]

    if bull_contain:
        # 价格在Bull FVG内 = 磁铁往上拉至中点
        nearest = sorted(bull_contain, key=lambda x: abs(x['mid'] - price))[0]
        result['fvg_magnet_dir'] = 'UP'
        result['fvg_magnet_target'] = nearest['mid']
    elif bull_above:
        nearest = sorted(bull_above, key=lambda x: x['lo'])[0]
        result['fvg_magnet_dir'] = 'UP'
        result['fvg_magnet_target'] = nearest['lo']
    elif bear_below:
        nearest = sorted(bear_below, key=lambda x: x['hi'], reverse=True)[0]
        result['fvg_magnet_dir'] = 'DOWN'
        result['fvg_magnet_target'] = nearest['hi']

    # ── Step2: OB有效性 ──
    for ob in ob_data.get('bear_obs', []):
        age = ob.get('age_bars', 999)
        broken = ob.get('broken', False)
        if age < 50 and not broken and ob.get('high', 0) > price:
            result['valid_bear_obs'].append({
                'lo': round(ob['low'], 2),
                'hi': round(ob['high'], 2),
                'age': age,
                'mid': round((ob['low'] + ob['high']) / 2, 2),
            })

    for ob in ob_data.get('bull_obs', []):
        age = ob.get('age_bars', 999)
        broken = ob.get('broken', False)
        if age < 50 and not broken and ob.get('low', 0) < price:
            result['valid_bull_obs'].append({
                'lo': round(ob['low'], 2),
                'hi': round(ob['high'], 2),
                'age': age,
                'mid': round((ob['low'] + ob['high']) / 2, 2),
            })

    # ── Step3: 清算地图 ──
    for c in clusters:
        if not isinstance(c, dict): continue
        cp = c.get('price', 0); cnt = c.get('count', 1); sz = c.get('size', 0)
        if cp > price:
            result['liq_up'].append({'price': cp, 'count': cnt, 'size': sz})
        else:
            result['liq_dn'].append({'price': cp, 'count': cnt, 'size': sz})

    result['liq_up'] = sorted(result['liq_up'], key=lambda x: x['count'], reverse=True)
    result['liq_dn'] = sorted(result['liq_dn'], key=lambda x: x['count'], reverse=True)

    # ── Step4: 共振点识别 ──
    # 做空共振：FVG中点（上方）+ 有效Bear OB + 上方清算山
    short_resonance = _find_short_resonance(
        price, result['bull_fvg'], result['valid_bear_obs'], result['liq_up'], min_sl
    )
    # 做多共振：FVG中点（下方）+ 有效Bull OB + 下方清算池
    long_resonance = _find_long_resonance(
        price, result['bear_fvg'], result['valid_bull_obs'], result['liq_dn'], min_sl
    )

    result['resonance_short'] = short_resonance
    result['resonance_long']  = long_resonance

    # 裁决
    if short_resonance and short_resonance.get('score', 0) >= 2:
        result['verdict'] = 'SHORT'
        result['verdict_reason'] = short_resonance.get('reason', '')
    elif long_resonance and long_resonance.get('score', 0) >= 2:
        result['verdict'] = 'LONG'
        result['verdict_reason'] = long_resonance.get('reason', '')
    else:
        result['verdict'] = 'WAIT'
        missing = []
        if not short_resonance: missing.append('做空无共振点')
        if not long_resonance:  missing.append('做多无共振点')
        result['verdict_reason'] = '，'.join(missing) or '等待结构确认'

    return result


def _find_short_resonance(price, bull_fvgs, valid_bear_obs, liq_up, min_sl):
    """寻找做空共振点：FVG中点/上沿 + 有效Bear OB + 清算山"""
    candidates = []

    # FVG提供的做空目标位
    fvg_targets = []
    for f in bull_fvgs:
        if f['mid'] > price:
            fvg_targets.append(f['mid'])   # FVG中点
        if f['hi'] > price:
            fvg_targets.append(f['hi'])    # FVG上沿

    # OB提供的做空目标位
    ob_targets = [ob['hi'] for ob in valid_bear_obs if ob['hi'] > price]

    # 清算山提供的做空目标位
    liq_targets = [c['price'] for c in liq_up if c['count'] >= 3]

    if not (fvg_targets or ob_targets):
        return None

    # 找三者交叉点（在容忍范围内）
    tol = min_sl * 0.8  # 容忍区间
    best = None
    best_score = 0

    all_targets = set()
    for t in fvg_targets + ob_targets + liq_targets:
        all_targets.add(round(t, 0))

    for target in sorted(all_targets):
        if target <= price: continue
        score = 0
        reasons = []

        # FVG命中
        for f in bull_fvgs:
            if abs(f['mid'] - target) <= tol:
                score += 1; reasons.append(f"FVG中点${target:,.2f}")
            if abs(f['hi'] - target) <= tol:
                score += 1; reasons.append(f"FVG上沿${target:,.2f}")

        # OB命中
        for ob in valid_bear_obs:
            if abs(ob['hi'] - target) <= tol or (ob['lo'] <= target <= ob['hi']):
                score += 1; reasons.append(f"Bear OB(age={ob['age']}bars)")

        # 清算命中
        for c in liq_up:
            if abs(c['price'] - target) <= tol:
                score += 1; reasons.append(f"清算山×{c['count']} ${c['size']/1e6:.0f}M")

        if score > best_score:
            best_score = score
            best = {
                'entry': round(target, 2),
                'score': score,
                'reason': ' + '.join(reasons[:3]),
                'sl': round(target + max(min_sl, tol) + 0.5, 2),
                'wait_dist': round(target - price, 2),
            }

    return best if best and best_score >= 2 else None


def _find_long_resonance(price, bear_fvgs, valid_bull_obs, liq_dn, min_sl):
    """寻找做多共振点：Bear FVG中点 + 有效Bull OB + 清算池"""
    candidates = []

    fvg_targets = []
    for f in bear_fvgs:
        if f['mid'] < price:
            fvg_targets.append(f['mid'])
        if f['lo'] < price:
            fvg_targets.append(f['lo'])

    ob_targets = [ob['lo'] for ob in valid_bull_obs if ob['lo'] < price]
    liq_targets = [c['price'] for c in liq_dn if c['count'] >= 3]

    if not (fvg_targets or ob_targets):
        return None

    tol = min_sl * 0.8
    best = None
    best_score = 0

    all_targets = set()
    for t in fvg_targets + ob_targets + liq_targets:
        all_targets.add(round(t, 0))

    for target in sorted(all_targets, reverse=True):
        if target >= price: continue
        score = 0
        reasons = []

        for f in bear_fvgs:
            if abs(f['mid'] - target) <= tol:
                score += 1; reasons.append(f"Bear FVG中点${target:,.2f}")
            if abs(f['lo'] - target) <= tol:
                score += 1; reasons.append(f"Bear FVG下沿${target:,.2f}")

        for ob in valid_bull_obs:
            if abs(ob['lo'] - target) <= tol or (ob['lo'] <= target <= ob['hi']):
                score += 1; reasons.append(f"Bull OB(age={ob['age']}bars)")

        for c in liq_dn:
            if abs(c['price'] - target) <= tol:
                score += 1; reasons.append(f"清算池×{c['count']} ${c['size']/1e6:.0f}M")

        if score > best_score:
            best_score = score
            best = {
                'entry': round(target, 2),
                'score': score,
                'reason': ' + '.join(reasons[:3]),
                'sl': round(target - max(min_sl, tol) - 0.5, 2),
                'wait_dist': round(price - target, 2),
            }

    return best if best and best_score >= 2 else None


# ─────────────────────────────────────────────
# P2: 姓赵不宣VIP模版自动生成
# ─────────────────────────────────────────────

def format_vip_card(r: dict, res: dict) -> str:
    """
    自动生成姓赵不宣VIP卡片格式
    严格按照截图模版，不自创格式
    """
    import requests as _req
    from datetime import datetime

    price   = res['price']
    symbol  = res['symbol']
    verdict = res['verdict']

    # 24H涨跌幅
    try:
        r24 = _req.get('https://fapi.binance.com/fapi/v1/ticker/24hr',
            params={'symbol': symbol}, timeout=5).json()
        chg_pct = float(r24['priceChangePercent'])
        chg_str = f"+{chg_pct:.2f}%" if chg_pct >= 0 else f"{chg_pct:.2f}%"
    except:
        chg_str = "N/A"

    # 标的简写
    sym_short = symbol.replace('USDT', '')
    ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')

    lines = []
    lines.append(f"🌿 **姓赵不宣 丨 {sym_short} ({chg_str})** 今日布局")

    rs  = res.get('resonance_short')
    rl  = res.get('resonance_long')
    liq_dn = res.get('liq_dn', [])
    liq_up = res.get('liq_up', [])

    # ── 空单 ──
    if rs:
        entry = rs['entry']
        sl    = rs['sl']
        sl_dist = sl - entry

        # TP = 下方清算池
        tps = sorted([c['price'] for c in liq_dn if c['price'] < entry], reverse=True)
        tp1 = tps[0] if len(tps) > 0 else round(entry * 0.98, 2)
        tp2 = tps[1] if len(tps) > 1 else round(entry * 0.97, 2)
        tp3 = tps[2] if len(tps) > 2 else round(entry * 0.96, 2)

        # 杠杆判断（100X清算位 = entry×1.0095，若SL>清算位 → 降杠杆）
        liq_100x = round(entry * 1.0095, 2)
        lev = '20x' if liq_100x < sl else '100x'
        nav = '2%'

        lines.append(
            f"🔴 **空单** 等 **${entry:,.2f}** 反弹入场 "
            f"止损 **${sl:,.2f}** "
            f"目标 ${tp1:,.2f} / ${tp2:,.2f} / ${tp3:,.2f} "
            f"杠杆{lev} 仓{nav}"
        )
    else:
        lines.append(f"🔴 **空单** 暂无共振点，等待结构")

    # ── 多单 ──
    if rl:
        entry_l = rl['entry']
        sl_l    = rl['sl']

        tps_up = sorted([c['price'] for c in liq_up if c['price'] > entry_l])
        tp1_l = tps_up[0] if len(tps_up) > 0 else round(entry_l * 1.02, 2)
        tp2_l = tps_up[1] if len(tps_up) > 1 else round(entry_l * 1.03, 2)

        # 做多方式：猎杀被扫后接
        liq_near = sorted([c for c in liq_dn if c['price'] > sl_l and c['price'] < entry_l],
                          key=lambda x: x['price'])
        if liq_near:
            scan_price = liq_near[0]['price']
            zone_lo = round(scan_price - res['atr1h'] * 0.3, 2)
            zone_hi = round(scan_price + res['atr1h'] * 0.3, 2)
            lines.append(
                f"🟢 **多单** 等 **${scan_price:,.2f}** 猎杀被扫后 "
                f"${zone_lo:,.2f}~${zone_hi:,.2f} 接 "
                f"止损 **${sl_l:,.2f}** "
                f"目标 ${tp1_l:,.2f} / ${tp2_l:,.2f} "
                f"杠杆20x 仓1%"
            )
        else:
            lines.append(
                f"🟢 **多单** 等 **${entry_l:,.2f}** 接 "
                f"止损 **${sl_l:,.2f}** "
                f"目标 ${tp1_l:,.2f} / ${tp2_l:,.2f} "
                f"杠杆20x 仓1%"
            )
    else:
        lines.append(f"🟢 **多单** 暂无共振点，等待结构")

    # ── 主方向逻辑 ──
    if verdict == 'SHORT' and rs:
        core = f"主方向做空，{rs['reason']}，等触及+15M顶背离再入"
    elif verdict == 'LONG' and rl:
        core = f"主方向做多，{rl['reason']}，等猎杀确认+15M企稳再入"
    else:
        core = f"等待，无共振点（{res['verdict_reason']}），不操作"

    lines.append(f"⚠️ {core}")
    lines.append(f"*{ts} | price_ts实时 ${price:,.2f}*")

    return '\n'.join(lines)


# ─────────────────────────────────────────────
# 主入口：输出完整强制链路板块
# ─────────────────────────────────────────────

def format_smc_block(r: dict) -> str:
    """完整输出强制前置链路 + VIP卡片"""
    lines = []
    lines.append('')
    lines.append('╬══════════════════════════════════════════════════════════')
    lines.append('  🏛️ 强制前置链路 FVG→OB→清算→共振')
    lines.append('╬══════════════════════════════════════════════════════════')

    try:
        res = run_smc_resonance(r)
        price = res['price']

        # Step1 FVG
        lines.append(f"  【Step1 FVG磁铁】")
        for f in res['bull_fvg'][:2]:
            lines.append(f"  Bull FVG: ${f['lo']:,.2f}~${f['hi']:,.2f} 中点=${f['mid']:,.2f} gap={f['gap_pct']:.2f}% 磁铁↑")
        for f in res['bear_fvg'][:2]:
            lines.append(f"  Bear FVG: ${f['lo']:,.2f}~${f['hi']:,.2f} 中点=${f['mid']:,.2f} gap={f['gap_pct']:.2f}% 磁铁↓")
        if res['fvg_magnet_dir']:
            lines.append(f"  磁铁方向: {res['fvg_magnet_dir']} → 目标 ${res['fvg_magnet_target']:,.2f}")
        else:
            lines.append(f"  磁铁方向: 无明确FVG")

        # Step2 OB
        lines.append(f"  【Step2 OB有效性】")
        if res['valid_bear_obs']:
            for ob in res['valid_bear_obs'][:2]:
                lines.append(f"  Bear OB: ${ob['lo']:,.2f}~${ob['hi']:,.2f} age={ob['age']}bars ✅有效")
        else:
            lines.append(f"  Bear OB: 无有效（已穿越或age>50）")
        if res['valid_bull_obs']:
            for ob in res['valid_bull_obs'][:2]:
                lines.append(f"  Bull OB: ${ob['lo']:,.2f}~${ob['hi']:,.2f} age={ob['age']}bars ✅有效")
        else:
            lines.append(f"  Bull OB: 无有效")

        # Step3 清算
        lines.append(f"  【Step3 清算地图】")
        if res['liq_up']:
            lines.append(f"  上方止损山: " + " | ".join([f"${c['price']:,.2f}×{c['count']} ${c['size']/1e6:.0f}M" for c in res['liq_up'][:3]]))
        if res['liq_dn']:
            lines.append(f"  下方止损池: " + " | ".join([f"${c['price']:,.2f}×{c['count']} ${c['size']/1e6:.0f}M" for c in res['liq_dn'][:3]]))
        lines.append(f"  100X清算: 空头${price*1.0095:,.2f} / 多头${price*0.9905:,.2f}")

        # Step4 共振
        lines.append(f"  【Step4 共振点识别】")
        rs = res['resonance_short']
        rl = res['resonance_long']
        if rs:
            lines.append(f"  做空共振: ${rs['entry']:,.2f} score={rs['score']} ({rs['reason']}) 等+${rs['wait_dist']:.2f}")
        else:
            lines.append(f"  做空共振: ❌无")
        if rl:
            lines.append(f"  做多共振: ${rl['entry']:,.2f} score={rl['score']} ({rl['reason']}) 等-${rl['wait_dist']:.2f}")
        else:
            lines.append(f"  做多共振: ❌无")

        # 裁决
        lines.append(f"  【裁决】{res['verdict']} — {res['verdict_reason']}")
        lines.append('')

        # VIP卡片
        lines.append('╬══════════════════════════════════════════════════════════')
        lines.append('  🌿 VIP策略（姓赵不宣格式）')
        lines.append('╬══════════════════════════════════════════════════════════')
        vip = format_vip_card(r, res)
        lines.append(vip)

    except Exception as e:
        lines.append(f"  ⚠️ 强制链路异常: {e}")

    lines.append('╬══════════════════════════════════════════════════════════')
    return '\n'.join(lines)

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/structure_quality_engine.py ══
#!/usr/bin/env python3
# ponytail: structure_quality_engine 383行，独立计算引擎，功能内聚，拆分条件: 单引擎>3000行且有完整测试
"""
structure_quality_engine.py — 结构质量引擎 v1.0
设计院 · 2026-05-31

核心哲学（十年交易视角）：
  好信号不是「评分高」，是「入场区有真实价格结构支撑」
  无结构入场 = 赌博。有结构入场 = 交易。

五级结构评分：
  S (90-100): FVG + OB双重确认，结构完美
  A (70-89):  FVG或强OB，单重确认
  B (50-69):  摆动高低点或Fib黄金位
  C (30-49):  弱结构（Fib普通位 / 远期OB）
  X (0-29):   无结构，入场区≈现价，拒绝
"""

import math
from typing import Optional

# ── 结构等级定义 ─────────────────────────────────────────────────────────────
GRADE_S = 90   # FVG+OB双重
GRADE_A = 70   # 单重强结构
GRADE_B = 50   # 弱结构
GRADE_C = 30   # 极弱
GRADE_X = 0    # 无结构 → 拒绝

def evaluate_structure_quality(
    symbol: str,
    signal_dir: str,         # 'SHORT' or 'LONG'
    price: float,
    entry_lo: float,
    entry_hi: float,
    smc: dict,               # brahma_brain SMC数据
    swing_4h: dict,          # 4H摆动结构
    key_levels: dict,        # Fib/关键位
    momentum: dict,          # ATR等动量数据
    **kwargs,                # v24.3: trigger_confidence等扩展参数
) -> dict:
    """
    评估入场区的结构质量。
    返回: {'grade': int, 'label': str, 'sources': [...], 'reject': bool, 'reason': str}
    """
    sources  = []
    score    = 0
    entry_mid = (entry_lo + entry_hi) / 2 if entry_lo and entry_hi else price

    # ── 1. 入场区是否有偏离（基础条件）─────────────────────────────────────
    entry_gap_pct = abs(entry_mid - price) / price * 100 if price > 0 else 0

    # 无结构：入场区≈现价（gap<0.1%，几乎重合才拒绝）[v24.3: 0.2%→0.1%]
    # 原逻辑0.2%过严：价格在入场区内时gap≈0，正常信号被误杀
    # 当价格在入场区内(gap<0.2%)且有15M触发时，应继续评分而非直接拒绝
    _trigger_conf = kwargs.get('trigger_confidence', 0) or 0
    if entry_gap_pct < 0.1 and _trigger_conf < 40:
        return {
            'grade': GRADE_X, 'label': 'X-无结构',
            'sources': ['入场区≈现价(gap<0.1%)'],
            'reject': True,
            'reason': f'入场区距现价仅{entry_gap_pct:.2f}%，无结构锚点，拒绝',
            'entry_gap_pct': entry_gap_pct,
        }
    # 价格在入场区内（gap<0.2%）但有15M触发 → 给予基础分继续评分
    _in_entry_zone = entry_gap_pct < 0.2

    # ── 动态对齐阈值（按体制/入场区距离自适应）[2026-06-03 根治修复] ─────────
    # 根因: BEAR_TREND下入场区距现价2-4%是正常的，OB/FVG锚点也在同等距离
    # 原来固定阈值<1.5%→ob_score=0→grade=19→INVALID（误杀高分信号）
    # 修复: 以 entry_gap_pct 为基准，对齐阈值 = max(entry_gap_pct × 1.2, 1.5%)
    _align_tol = max(entry_gap_pct * 1.2, 1.5)   # 动态容忍度
    _align_tight = max(entry_gap_pct * 0.4, 0.5)  # 精确对齐阈值

    # ── 2. FVG（公平价值缺口）验证 ───────────────────────────────────────────
    fvg = smc.get('fvg', {}) if smc else {}
    fvg_key = 'nearest_bear' if signal_dir == 'SHORT' else 'nearest_bull'
    fvg_zone = fvg.get(fvg_key)

    fvg_score = 0
    if fvg_zone:
        fvg_gap = fvg_zone.get('gap_pct', 0) or 0
        fvg_mid = (fvg_zone.get('bottom', 0) + fvg_zone.get('top', 0)) / 2
        fvg_dist = abs(fvg_mid - entry_mid) / entry_mid * 100 if entry_mid > 0 else 999

        if fvg_gap >= 0.5 and fvg_dist < _align_tight:
            fvg_score = 40   # FVG完美对齐
            sources.append(f'FVG={fvg_gap:.2f}% 完美对齐(tol={_align_tight:.1f}%)')
        elif fvg_gap >= 0.3 and fvg_dist < _align_tol:
            fvg_score = 25
            sources.append(f'FVG={fvg_gap:.2f}% 近似对齐(tol={_align_tol:.1f}%)')
        elif fvg_gap >= 0.2 and fvg_dist < _align_tol * 1.5:
            fvg_score = 10
            sources.append(f'FVG={fvg_gap:.2f}% 弱对齐')

    score += fvg_score

    # ── 3. Order Block 验证 ──────────────────────────────────────────────────
    obs = smc.get('order_blocks', {}) if smc else {}
    ob_key = 'nearest_bear_ob' if signal_dir == 'SHORT' else 'nearest_bull_ob'
    ob = obs.get(ob_key)

    ob_score = 0
    if ob:
        ob_lo = float(ob.get('low', 0) or 0)
        ob_hi = float(ob.get('high', 0) or 0)
        ob_mid = ob.get('mid') or ((ob_lo + ob_hi) / 2 if ob_lo and ob_hi else 0)
        # [v24.5-fix] OB距离改用现价(price)为参考基准，而非entry_mid
        # 根因：入场区中点比现价高0.3-0.5%，导致OB距离被人为放大，卡在70分边界
        # 修复逻辑：OB是否「贴近当前行情」应以现价为准；入场区是未来预期位置，不是当前锚点
        _ref_price = price if price > 0 else entry_mid  # 以现价为距离参考
        ob_dist = abs(ob_mid - _ref_price) / _ref_price * 100 if _ref_price > 0 and ob_mid > 0 else 999
        ob_dist_entry = abs(ob_mid - entry_mid) / entry_mid * 100 if entry_mid > 0 and ob_mid > 0 else 999
        # [v24.3-fix] smc_engine的OB无strength字段，用dist_pct判断质量
        # dist_pct<0.3% = 精确对齐(强) / dist_pct<1.0% = 对齐(中) / 其他 = 弱
        ob_dist_pct = float(ob.get('dist_pct', ob_dist) or ob_dist)
        ob_quality = 'strong' if ob_dist_pct < 0.3 else ('medium' if ob_dist_pct < 1.0 else 'weak')

        # [v24.3-fix2] OB与入场区重叠判断：OB区间与entry区间有交集 = 精确对齐
        ob_overlap = (ob_lo <= entry_hi and ob_hi >= entry_lo) if ob_lo and ob_hi else False
        # 价格在OB区间内也算精确对齐
        price_in_ob = (ob_lo <= _ref_price <= ob_hi) if ob_lo and ob_hi else False
        if ob_overlap or price_in_ob or (ob_dist < _align_tight and ob_quality in ('strong', 'medium')):
            ob_score = 35
            sources.append(f'强OB精确对齐(price_dist={ob_dist:.2f}% in_ob={price_in_ob} overlap={ob_overlap})')
        elif ob_dist < _align_tol:
            ob_score = 20
            sources.append(f'OB对齐(price_dist={ob_dist:.1f}%)')
        elif ob_dist < _align_tol * 1.5:
            ob_score = 8
            sources.append(f'弱OB price_dist={ob_dist:.1f}%')

    score += ob_score

    # ── 4. 摆动结构验证（4H高低点）──────────────────────────────────────────
    swing_score = 0
    sw_highs = swing_4h.get('highs', []) if swing_4h else []
    sw_lows  = swing_4h.get('lows', [])  if swing_4h else []

    # Swing对齐阈值同步动态化
    _swing_tol = max(entry_gap_pct * 1.5, 1.0)  # 动态容忍度 [v24.4: 0.5→1.0% BTC ATR4H≈1-1.5%，0.5%下限会误杀真实供给区]
    if signal_dir == 'SHORT' and sw_highs:
        nearby_highs = [h for h in sw_highs if abs(h - entry_mid) / entry_mid < _swing_tol]
        if nearby_highs:
            swing_score = 20
            sources.append(f'4H摆动高点={nearby_highs[0]:.4g} 对齐')
    elif signal_dir == 'LONG' and sw_lows:
        nearby_lows = [l for l in sw_lows if abs(l - entry_mid) / entry_mid < _swing_tol]
        if nearby_lows:
            swing_score = 20
            sources.append(f'4H摆动低点={nearby_lows[0]:.4g} 对齐')

    score += swing_score

    # ── 5. Fib关键位验证 ─────────────────────────────────────────────────────
    fib = key_levels.get('fib', {}) if key_levels else {}
    fib_score = 0

    # Fib对齐阈值同步动态化
    _fib_tight = max(entry_gap_pct * 0.3, 0.5)
    _fib_loose = max(entry_gap_pct * 0.6, 1.0)
    golden_fibs = [('0.618', 15), ('0.786', 12), ('0.500', 8), ('0.382', 5)]
    for fib_key, fib_val in golden_fibs:
        if fib_key in fib:
            fib_level = float(fib[fib_key])
            dist = abs(fib_level - entry_mid) / entry_mid * 100 if entry_mid > 0 else 999
            if dist < _fib_tight:
                fib_score = max(fib_score, fib_val)
                sources.append(f'Fib{fib_key}={fib_level:.4g} 对齐')
                break
            elif dist < _fib_loose:
                fib_score = max(fib_score, fib_val // 2)

    score += fib_score

    # ── 6. 入场区宽度质量（避免零宽度入场）──────────────────────────────────
    zone_width_pct = (entry_hi - entry_lo) / entry_lo * 100 if entry_lo > 0 else 0
    if zone_width_pct < 0.05:
        score -= 15   # 入场区太窄，缺乏流动性缓冲
        sources.append(f'⚠️ 入场区过窄({zone_width_pct:.2f}%)')
    elif zone_width_pct >= 0.2:
        score += 5    # 合理宽度加分
        sources.append(f'入场区合理({zone_width_pct:.2f}%)')

    # ── 7. 15M触发15M触发置信度加分（v24.3新增）──────────────────────────
    # 15M触发置信度是最强的结构确认信号，应直接贡献gradeuff08此前被忽视）
    trigger_confidence = kwargs.get('trigger_confidence', 0)
    if trigger_confidence >= 80:
        score += 25
        sources.append(f'15M触发置信={trigger_confidence}满分(+25)')
    elif trigger_confidence >= 60:
        score += 15
        sources.append(f'15M触发置信={trigger_confidence}(+15)')
    elif trigger_confidence >= 40:
        score += 8
        sources.append(f'15M触发置信={trigger_confidence}(+8)')

    # ── 评级 ─────────────────────────────────────────────────────────────────
    score = max(0, min(100, score))

    if score >= GRADE_S:
        label = 'S-完美结构'
    elif score >= GRADE_A:
        label = 'A-强结构'
    elif score >= GRADE_B:
        label = 'B-弱结构'
    elif score >= GRADE_C:
        label = 'C-极弱结构'
    else:
        label = 'X-无结构'

    # [2026-06-03 动态门槛] reject阈值从GRADE_C(30)降至15
    # 依据: 武曲Paper 121笔实盘 grade>=25 WR=82.5%
    #       grade=19的score=174信号（ETH BEAR_TREND(熊市趋势)）是真实高质量信号
    #       被grade<30误杀，损失大量有效机会
    # 新规则: grade<15才真正"无结构"，15-29是"极弱结构但有锚点"
    _reject_threshold = 15
    reject = (score < _reject_threshold or entry_gap_pct < 0.2)
    reason = ''
    if reject:
        reason = f'结构质量不足(grade={score}<{_reject_threshold})，无有效锚点'

    return {
        'grade':         score,
        'label':         label,
        'sources':       sources,
        'reject':        reject,
        'reason':        reason,
        'entry_gap_pct': round(entry_gap_pct, 2),
        'fvg_score':     fvg_score,
        'ob_score':      ob_score,
        'swing_score':   swing_score,
        'fib_score':     fib_score,
    }


def get_time_weight(utc_hour: int) -> float:
    """
    实证时间权重（UTC小时）
    实盘数据：08-13 WR=56-100%，14-16 WR=0%
    """
    # 高质量窗口
    if utc_hour in (7, 8, 9, 10, 11, 12, 13):
        return 1.15   # 欧洲+美国早盘
    # 低质量窗口（美国午后）
    if utc_hour in (14, 15, 16):
        return 0.70   # WR=0%实证，大幅折扣
    # 亚洲时段（19-22 UTC）
    if utc_hour in (19, 20, 21, 22):
        return 1.10
    # 深夜低流动性
    if utc_hour in (0, 1, 2, 3, 4, 5):
        return 0.85
    return 1.0


def _kelly_position_local(wr: float, rr: float, nav: float, max_pct: float = 0.10) -> float:  # [2026-08-28封印] 仅限本文件自测，核心链路用position_sizer.kelly_position
    """
    半Kelly仓位计算
    wr: 胜率(0-1)  rr: 盈亏比  nav: 净值  max_pct: 单笔上限
    """
    if rr <= 0 or wr <= 0: return 0
    kelly = wr - (1 - wr) / rr
    if kelly <= 0: return 0
    half_kelly = kelly / 2
    position = min(half_kelly, max_pct) * nav
    return round(position, 2)


if __name__ == '__main__':
    # 自测
    print("=== structure_quality_engine 自测 ===")

    # 模拟BTC场景（有FVG结构）
    r1 = evaluate_structure_quality(
        'BTCUSDT', 'SHORT', 73900, 75900, 76300,
        smc={'fvg': {'nearest_bear': {'bottom': 75950, 'top': 76250, 'gap_pct': 0.4}}, 'order_blocks': {}},
        swing_4h={'highs': [76100, 76500], 'lows': [73000]},
        key_levels={'fib': {'0.618': 76000}},
        momentum={'atr_1h': 185}
    )
    print(f"BTC SHORT(FVG结构): grade={r1['grade']} label={r1['label']} sources={r1['sources']}")

    # 模拟LTC场景（无结构）
    r2 = evaluate_structure_quality(
        'LTCUSDT', 'SHORT', 50.93, 50.93, 50.93,
        smc={'fvg': {}, 'order_blocks': {}},
        swing_4h={'highs': [52.0, 53.5], 'lows': [49.0]},
        key_levels={'fib': {}},
        momentum={'atr_1h': 0.19}
    )
    print(f"LTC SHORT(无结构): grade={r2['grade']} label={r2['label']} reject={r2['reject']} reason={r2['reason']}")

    # Kelly计算
    print(f"\nKelly仓位示例:")
    print(f"  BTC WR=92% RR=0.26 NAV=127: ${_kelly_position_local(0.92, 0.26, 127)}")
    print(f"  LTC WR=50% RR=3.0  NAV=127: ${_kelly_position_local(0.50, 3.0, 127)}")
    print(f"  DOGE WR=59% RR=2.0 NAV=127: ${_kelly_position_local(0.59, 2.0, 127)}")

    # 时间权重
    print(f"\n时间权重(UTC):")
    for h in [7, 8, 14, 15, 20]:
        print(f"  {h:02d}:00 → {get_time_weight(h)}x")


# OB失效降级机制（apply_ob_decay_penalty）

def apply_ob_decay_penalty(grade: float, symbol: str, ob_data: dict,
                            current_price: float, timeframe: str = '1h') -> tuple:
    """
    失效OB降级惩罚：
    当OB已被价格穿越（失效），grade -= 30
    
    Args:
        grade: 当前结构评级
        symbol: 交易对
        ob_data: OB数据字典 {ob_high, ob_low, ob_ts}
        current_price: 当前价格
        timeframe: OB所在时间框架
    
    Returns:
        (adjusted_grade, penalty_reason)
    """
    if not ob_data:
        return grade, "无OB数据"
    
    ob_high = ob_data.get('ob_high', 0)
    ob_low  = ob_data.get('ob_low', 0)
    direction = ob_data.get('direction', 'SHORT')
    
    if not (ob_high and ob_low and current_price):
        return grade, "数据不完整"
    
    penalty = 0
    reason = ""
    
    # 空单OB：价格需在OB上方（OB low > current_price = OB失效）
    if direction in ('SHORT', '做空'):
        if current_price < ob_low * 0.995:  # 价格穿越OB下沿 = OB失效
            penalty = 30
            reason = f"SHORT OB失效(价格{current_price:.4f}<OB下沿{ob_low:.4f})"
        elif current_price < ob_high * 0.99:  # 价格深入OB内部
            penalty = 15
            reason = f"SHORT OB部分失效(价格在OB内{current_price:.4f})"
    
    # 多单OB：价格需在OB下方（OB high < current_price = OB失效）
    elif direction in ('LONG', '做多'):
        if current_price > ob_high * 1.005:  # 价格穿越OB上沿 = OB失效
            penalty = 30
            reason = f"LONG OB失效(价格{current_price:.4f}>OB上沿{ob_high:.4f})"
        elif current_price > ob_low * 1.01:
            penalty = 15
            reason = f"LONG OB部分失效"
    
    # 时间框架衰减：1H OB比4H OB衰减更快
    tf_penalty = {'1h': 1.0, '4h': 0.7, '1d': 0.5}.get(timeframe, 1.0)
    penalty = int(penalty * tf_penalty)
    
    adjusted = max(0, grade - penalty)
    return adjusted, reason if penalty > 0 else f"OB有效(penalty=0)"


def get_effective_grade(symbol: str, raw_grade: float, signal_data: dict,
                         current_price: float) -> tuple:
    """
    获取有效grade（应用所有衰减后）
    供brahma_analyze调用的统一入口
    
    Returns: (effective_grade, grade_detail)
    """
    grade = raw_grade
    details = []
    
    # 应用OB衰减
    ob_data = signal_data.get('ob_data') or {}
    if ob_data:
        grade, ob_reason = apply_ob_decay_penalty(grade, symbol, ob_data, current_price)
        details.append(f"OB: {ob_reason}")
    
    # Bridge-Gate/StructureGate最低门槛（grade<70直接拒绝）[v24.2]
    if grade < 70:
        details.append(f"grade={grade:.0f}<70 Bridge-Gate/StructureGate拒绝(TO率=73~100%)")
    
    return grade, " | ".join(details) if details else f"grade={grade:.0f}"

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/market_structure_scanner.py ══
import os
#!/usr/bin/env python3
# ponytail: market_structure_scanner 309行，有意为之，重构前先 grep 所有调用方
"""
market_structure_scanner.py — 四维市场结构扫描器
设计院 · 苏摩111 · 2026-07-01

扫描四大结构层：
  1. OB  (Order Block)    — SMC订单块，最强压力/支撑区
  2. LIQ (清算群)         — 多空强制平仓密集区
  3. GEX (期权伽马)       — 做市商对冲压力/磁铁位
  4. FVG (Fair Value Gap) — 公允价值缺口，回补磁铁

调用：
  python3 brahma_brain/market_structure_scanner.py
  python3 brahma_brain/market_structure_scanner.py --symbol ETHUSDT
  from brahma_brain.market_structure_scanner import scan_structure
"""

import sys, os, json, time
from pathlib import Path

_DIR  = Path(__file__).parent
_ROOT = _DIR.parent
sys.path.insert(0, str(_ROOT))

from brahma_brain.smc_engine     import analyze_smc
from brahma_brain.data_cache     import get_klines
from brahma_brain.brahma_bus     import bus
from brahma_brain.liq_scanner    import get_liq_snapshot

GEX_STATE_FILE = _ROOT / 'data' / 'gex_state.json'


# ════════════════════════════════════════════════════════════════
# 核心扫描函数
# ════════════════════════════════════════════════════════════════

def scan_structure(symbol: str = 'BTCUSDT') -> dict:
    """
    扫描 OB / LIQ / GEX / FVG 四维结构，返回综合结果字典
    """
    coin = symbol.replace('USDT', '').upper()
    result = {'symbol': symbol, 'coin': coin, 'ts': time.time()}

    # ── 1. 当前价 ─────────────────────────────────────────────
    try:
        px = bus.price(symbol)
    except Exception:
        import requests
        px = float(requests.get(
            f'https://fapi.binance.com/fapi/v1/ticker/price',
            params={'symbol': symbol}, timeout=5).json()['price'])
    result['price'] = px

    # ── 2. OB — 订单块 ────────────────────────────────────────
    ob_data = {'bear_ob': {}, 'bull_ob': {}, 'bear_ob_4h': {}, 'bull_ob_4h': {}}
    try:
        k1h = get_klines(symbol, '1h', 200)
        k4h = get_klines(symbol, '4h', 100)
        smc_1h = analyze_smc(symbol, k1h)
        smc_4h = analyze_smc(symbol, k4h)

        ob_1h = smc_1h.get('order_blocks', {})
        ob_4h = smc_4h.get('order_blocks', {})
        fvg_1h = smc_1h.get('fvg', {})
        fvg_4h = smc_4h.get('fvg', {})

        ob_data['bear_ob']    = ob_1h.get('nearest_bear_ob', {})
        ob_data['bull_ob']    = ob_1h.get('nearest_bull_ob', {})
        ob_data['bear_ob_4h'] = ob_4h.get('nearest_bear_ob', {})
        ob_data['bull_ob_4h'] = ob_4h.get('nearest_bull_ob', {})
        ob_data['bear_obs_all'] = ob_1h.get('bear_obs', [])[:3]
        ob_data['bull_obs_all'] = ob_1h.get('bull_obs', [])[:3]

        # FVG
        fvg_data = {
            'bear_fvg':    fvg_1h.get('nearest_bear', {}),
            'bull_fvg':    fvg_1h.get('nearest_bull', {}),
            'bear_fvg_4h': fvg_4h.get('nearest_bear', {}),
            'bull_fvg_4h': fvg_4h.get('nearest_bull', {}),
            'bear_fvgs':   fvg_1h.get('bear_gaps', [])[:3],
            'bull_fvgs':   fvg_1h.get('bull_gaps', [])[:3],
        }
        result['fvg'] = fvg_data
    except Exception as e:
        ob_data['error'] = str(e)
        result['fvg'] = {}
    result['ob'] = ob_data

    # ── 3. LIQ — 清算群 ───────────────────────────────────────
    liq_data = {}
    try:
        snap = get_liq_snapshot(symbol)
        liq_data = {
            'long_pct':        snap.get('long_pct', 0),
            'short_pct':       snap.get('short_pct', 0),
            'liq_short_5pct':  snap.get('liq_short_5pct', 0),   # 空头+5%清算密集位
            'liq_short_10pct': snap.get('liq_short_10pct', 0),
            'liq_long_5pct':   snap.get('liq_long_5pct', 0),    # 多头-5%清算密集位
            'liq_long_10pct':  snap.get('liq_long_10pct', 0),
            'fund_rate':       snap.get('fund_rate', 0),
            'fund_bias':       snap.get('fund_bias', ''),
            'liq_bias':        snap.get('liq_bias', ''),
            'liq_risk':        snap.get('liq_risk', ''),
            'oi_b':            snap.get('oi_b', 0),
            'oi_chg4h':        snap.get('oi_chg4h', 0),
        }
        # Tardis清算墙（最重要的精确数据）
        tardis = snap.get('tardis_walls', {})
        if tardis.get('available'):
            long_walls  = tardis.get('long_walls', [])[:3]
            short_walls = tardis.get('short_walls', [])[:3]
            liq_data['tardis_long_walls']  = long_walls
            liq_data['tardis_short_walls'] = short_walls
            liq_data['tardis_bias']        = tardis.get('bias', '')
    except Exception as e:
        liq_data['error'] = str(e)
    result['liq'] = liq_data

    # ── 4. GEX — 期权伽马暴露 ────────────────────────────────
    gex_data = {}
    try:
        if GEX_STATE_FILE.exists():
            gex_state = json.loads(GEX_STATE_FILE.read_text())
            g = gex_state.get(coin, {})
            gex_data = {
                'max_strike':   g.get('max_gex_strike', 0),
                'min_strike':   g.get('min_gex_strike', 0),
                'zero_flip':    g.get('zero_flip', 0),
                'direction':    g.get('gex_direction', ''),
                'dist_max_pct': g.get('dist_to_max_pct', 0),
                'dist_min_pct': g.get('dist_to_min_pct', 0),
                'net_at_spot':  g.get('net_gex_at_spot', 0),
                'fib_786':      g.get('fib_786', 0),
                'fib_618':      g.get('fib_618', 0),
                'fib_500':      g.get('fib_500', 0),
                'fib_382':      g.get('fib_382', 0),
            }
    except Exception as e:
        gex_data['error'] = str(e)
    result['gex'] = gex_data

    return result


# ════════════════════════════════════════════════════════════════
# 格式化输出（供AI播报）
# ════════════════════════════════════════════════════════════════

def format_report(r: dict) -> str:
    """格式化为标准监控卡片"""
    sym   = r.get('coin', '?')
    px    = r.get('price', 0)
    ob    = r.get('ob', {})
    liq   = r.get('liq', {})
    gex   = r.get('gex', {})
    fvg   = r.get('fvg', {})

    def pf(v, is_usd=True):
        if not v: return 'N/A'
        if is_usd:
            if v > 1000: return f'${v:,.0f}'
            if v > 1:    return f'${v:,.2f}'
            return f'${v:.5f}'
        return str(v)

    lines = [f'【{sym}/USDT · ${px:,.1f}】']

    # OB
    bear_ob = ob.get('bear_ob', {})
    bull_ob = ob.get('bull_ob', {})
    bear_ob_4h = ob.get('bear_ob_4h', {})
    bull_ob_4h = ob.get('bull_ob_4h', {})

    if bear_ob or bull_ob:
        lines.append('📦 OB订单块')
        if bear_ob and bear_ob.get('high'):
            dist = (bear_ob['high'] - px) / px * 100
            lines.append(f'  空头OB 1H: {pf(bear_ob.get("low"))}~{pf(bear_ob.get("high"))} ({dist:+.2f}%)')
        if bull_ob and bull_ob.get('high'):
            dist = (px - bull_ob['low']) / px * 100
            lines.append(f'  多头OB 1H: {pf(bull_ob.get("low"))}~{pf(bull_ob.get("high"))} ({dist:+.2f}%)')
        if bear_ob_4h and bear_ob_4h.get('high') and bear_ob_4h != bear_ob:
            lines.append(f'  空头OB 4H: {pf(bear_ob_4h.get("low"))}~{pf(bear_ob_4h.get("high"))}')
        if bull_ob_4h and bull_ob_4h.get('high') and bull_ob_4h != bull_ob:
            lines.append(f'  多头OB 4H: {pf(bull_ob_4h.get("low"))}~{pf(bull_ob_4h.get("high"))}')

    # 清算
    if liq and not liq.get('error'):
        lines.append('💥 清算群')
        tardis_long  = liq.get('tardis_long_walls',  [])
        tardis_short = liq.get('tardis_short_walls', [])
        if tardis_long:
            top = tardis_long[0]
            px_w, val = top[0], top[1]
            lines.append(f'  多头清算墙: ${px_w:,.0f}  (${val/1e6:.1f}M)')
        elif liq.get('liq_long_5pct'):
            lines.append(f'  多头清算密集: ${liq["liq_long_5pct"]:,.0f} (~5%下方)')
        if tardis_short:
            top = tardis_short[0]
            px_w, val = top[0], top[1]
            lines.append(f'  空头清算墙: ${px_w:,.0f}  (${val/1e6:.1f}M)')
        elif liq.get('liq_short_5pct'):
            lines.append(f'  空头清算密集: ${liq["liq_short_5pct"]:,.0f} (~5%上方)')
        bias = liq.get('tardis_bias') or liq.get('liq_bias','')
        if bias:
            lines.append(f'  方向偏向: {bias}')
        long_pct = liq.get('long_pct', 0)
        if long_pct:
            lines.append(f'  多空比: 多{long_pct:.0f}% / 空{liq.get("short_pct",0):.0f}%')

    # GEX
    if gex and not gex.get('error') and gex.get('max_strike'):
        direction = '🔴负伽马(放大)' if gex.get('direction') == 'NEGATIVE' else '🟢正伽马(压制)'
        lines.append(f'⚡ GEX伽马  {direction}')
        lines.append(f'  MAX: ${gex["max_strike"]:,.0f} (+{gex["dist_max_pct"]:.1f}%)')
        lines.append(f'  ZeroFlip: ${gex["zero_flip"]:,.0f}')
        lines.append(f'  MIN: ${gex["min_strike"]:,.0f} (-{gex["dist_min_pct"]:.1f}%)')

    # FVG
    bear_fvg = fvg.get('bear_fvg', {})
    bull_fvg = fvg.get('bull_fvg', {})
    if bear_fvg or bull_fvg:
        lines.append('🕳️  FVG公允缺口')
        if bear_fvg and bear_fvg.get('top'):
            gap = bear_fvg.get('gap_pct', 0)
            dist = (bear_fvg['bottom'] - px) / px * 100
            lines.append(f'  空头FVG: {pf(bear_fvg.get("bottom"))}~{pf(bear_fvg.get("top"))} ({gap:.2f}%, {dist:+.1f}%)')
        if bull_fvg and bull_fvg.get('top'):
            gap = bull_fvg.get('gap_pct', 0)
            dist = (px - bull_fvg['top']) / px * 100
            lines.append(f'  多头FVG: {pf(bull_fvg.get("bottom"))}~{pf(bull_fvg.get("top"))} ({gap:.2f}%, {dist:+.1f}%)')

    return '\n'.join(lines)


# ════════════════════════════════════════════════════════════════
# 自推送：直接推送到 Jarvis，绕开AI渲染层
# ════════════════════════════════════════════════════════════════

import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
try:
    from system_config import JARVIS_TARGET, JARVIS_CHANNEL  # type: ignore
except Exception:
    JARVIS_TARGET  = os.environ.get('JARVIS_TARGET', '73295708:thread:01a033af-3697-734a-9f9c-c3e34a00c378')
    JARVIS_CHANNEL = 'jarvis'


def _push(message: str):
    """openclaw message send 直接推送，保留换行格式"""
    import subprocess
    subprocess.run(
        ['openclaw', 'message', 'send',
         '--channel', JARVIS_CHANNEL,
         '--target',  JARVIS_TARGET,
         '--message', message],
        capture_output=True, timeout=15
    )


# ════════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════════

def main():
    import argparse
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(description='四维市场结构扫描器')
    parser.add_argument('--symbol',  default='BTCUSDT', help='交易对（默认BTCUSDT）')
    parser.add_argument('--json',    action='store_true', help='输出原始JSON')
    parser.add_argument('--both',    action='store_true', help='同时扫描BTC+ETH')
    parser.add_argument('--push',    action='store_true', help='自推送到Jarvis（绕开AI渲染）')
    args = parser.parse_args()

    symbols = ['BTCUSDT', 'ETHUSDT'] if args.both else [args.symbol]

    # 扫描所有标的
    reports = []
    for sym in symbols:
        result = scan_structure(sym)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        else:
            reports.append(format_report(result))

    if args.json:
        return

    # 拼接完整播报内容
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    sep = '━' * 19
    body = f'\n\n'.join(reports)
    full_msg = f'📊 市场结构 {now_utc}\n{sep}\n{body}\n{sep}'

    if args.push:
        # 自推送模式：直接发到Jarvis，保留换行
        _push(full_msg)
        print(f'[market-structure] 推送完成 → {JARVIS_TARGET}')
        print(full_msg)  # 同时输出到log
    else:
        # 直接输出（供手动调用）
        print(full_msg)


if __name__ == '__main__':
    main()

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/divergence_engine.py ══
# ponytail: divergence_engine 746行，独立计算引擎，功能内聚，拆分条件: 单引擎>3000行且有完整测试
"""
from brahma_brain.math_utils import calc_rsi, ema  # 统一标量版 v1.0 (calc_rsi_series = 序列版，仅本文件使用)

# ── STATUS: ACTIVE ──────────────────────────────────────────
# 背离检测引擎，SMC辅助
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
divergence_engine.py · RSI/MACD背离检测引擎
brahma_brain · Phase 2

功能：
  - RSI 常规背离（价格/RSI方向相反）
  - RSI 隐藏背离（趋势延续信号）
  - RSI 失败摆动（FTS，高胜率反转）
  - MACD 柱状图背离（常规+隐藏）
  - MACD 零轴位置判断
  - 背离综合评分（0~20分）
"""

# ═══════════════════════════════════════════════════════════════
# 一、RSI计算工具
# ═══════════════════════════════════════════════════════════════

def calc_rsi_series(closes: list, n: int = 14) -> list:
    """[2026-08-28 精简] 委托math_utils.rsi_series — SSOT"""
    from math_utils import calc_rsi as _mu_rsi
    import pandas as _pd
    s = _pd.Series(closes)
    results = [_mu_rsi(closes[:i+1], n) for i in range(max(n, len(closes)))]
    return results[-len(closes):]
def calc_macd_series(closes: list, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """返回完整MACD序列"""
    def ema_s(data, n):
        if len(data) < n:
            return [data[i] for i in range(len(data))]
        k = 2 / (n + 1)
        e = sum(data[:n]) / n
        result = [None] * (n - 1) + [e]
        for x in data[n:]:
            e = x * k + e * (1 - k)
            result.append(e)
        return result

    ema_fast = ema_s(closes, fast)
    ema_slow = ema_s(closes, slow)

    macd_line = []
    for f, s in zip(ema_fast, ema_slow):
        if f is None or s is None:
            macd_line.append(None)
        else:
            macd_line.append(f - s)

    valid_macd = [x for x in macd_line if x is not None]
    if len(valid_macd) < signal:
        sig_line = [0.0] * len(macd_line)
    else:
        sig_raw = ema_s(valid_macd, signal)
        none_count = macd_line.count(None)
        sig_line = [None] * none_count + sig_raw

    histogram = []
    for m, sg in zip(macd_line, sig_line):
        if m is None or sg is None:
            histogram.append(None)
        else:
            histogram.append(m - sg)

    return {
        'macd':      macd_line,
        'signal':    sig_line,
        'histogram': histogram,
    }

# ═══════════════════════════════════════════════════════════════
# 二、摆动点识别（用于背离检测）
# ═══════════════════════════════════════════════════════════════

def find_pivots(values: list, lookback: int = 3) -> dict:
    """识别序列中的摆动高低点"""
    highs, lows = [], []
    valid = [(i, v) for i, v in enumerate(values) if v is not None]
    for k in range(lookback, len(valid) - lookback):
        i, v = valid[k]
        window_vals = [valid[k-j][1] for j in range(1, lookback+1)] + \
                      [valid[k+j][1] for j in range(1, lookback+1)]
        if v >= max(window_vals):
            highs.append((i, v))
        if v <= min(window_vals):
            lows.append((i, v))
    return {'highs': highs[-6:], 'lows': lows[-6:]}

# ═══════════════════════════════════════════════════════════════
# 三、RSI背离检测
# ═══════════════════════════════════════════════════════════════

def detect_rsi_divergence(closes: list, n_rsi: int = 14, lookback: int = 5) -> dict:
    """
    检测RSI背离
    返回：常规看空/看多背离，隐藏背离，FTS
    """
    rsi_vals = calc_rsi_series(closes, n_rsi)

    price_pivots = find_pivots(closes,   lookback)
    rsi_pivots   = find_pivots(rsi_vals, lookback)

    ph = price_pivots['highs']
    pl = price_pivots['lows']
    rh = rsi_pivots['highs']
    rl = rsi_pivots['lows']

    results = {
        'regular_bearish': False,   # 常规看空（价格HH，RSI LH）→ 顶部反转
        'regular_bullish': False,   # 常规看多（价格LL，RSI HL）→ 底部反转
        'hidden_bearish':  False,   # 隐藏看空（价格LH，RSI HH）→ 下跌延续
        'hidden_bullish':  False,   # 隐藏看多（价格HL，RSI LL）→ 上涨延续
        'fts_bearish':     False,   # 失败摆动看空
        'fts_bullish':     False,   # 失败摆动看多
        'details':         [],
        'score_long':  0,
        'score_short': 0,
    }

    # 常规看空背离：价格HH，RSI LH
    if len(ph) >= 2 and len(rh) >= 2:
        p_high1, p_high2 = ph[-2][1], ph[-1][1]
        r_high1, r_high2 = rh[-2][1], rh[-1][1]
        if p_high2 > p_high1 and r_high2 < r_high1:
            results['regular_bearish'] = True
            results['details'].append(
                f'常规看空背离: 价格{p_high1:.2f}→{p_high2:.2f}↑  RSI{r_high1:.1f}→{r_high2:.1f}↓'
            )
            results['score_short'] += 8

    # 常规看多背离：价格LL，RSI HL
    if len(pl) >= 2 and len(rl) >= 2:
        p_low1, p_low2 = pl[-2][1], pl[-1][1]
        r_low1, r_low2 = rl[-2][1], rl[-1][1]
        if p_low2 < p_low1 and r_low2 > r_low1:
            results['regular_bullish'] = True
            results['details'].append(
                f'常规看多背离: 价格{p_low1:.2f}→{p_low2:.2f}↓  RSI{r_low1:.1f}→{r_low2:.1f}↑'
            )
            results['score_long'] += 8

    # 隐藏看空背离：价格LH，RSI HH（下跌趋势延续）
    if len(ph) >= 2 and len(rh) >= 2:
        p_high1, p_high2 = ph[-2][1], ph[-1][1]
        r_high1, r_high2 = rh[-2][1], rh[-1][1]
        if p_high2 < p_high1 and r_high2 > r_high1:
            results['hidden_bearish'] = True
            results['details'].append(
                f'隐藏看空背离(趋势延续): 价格↓  RSI↑'
            )
            results['score_short'] += 5

    # 隐藏看多背离：价格HL，RSI LL（上涨趋势延续）
    if len(pl) >= 2 and len(rl) >= 2:
        p_low1, p_low2 = pl[-2][1], pl[-1][1]
        r_low1, r_low2 = rl[-2][1], rl[-1][1]
        if p_low2 > p_low1 and r_low2 < r_low1:
            results['hidden_bullish'] = True
            results['details'].append(
                f'隐藏看多背离(趋势延续): 价格↑  RSI↓'
            )
            results['score_long'] += 5

    # RSI失败摆动（FTS）- 看空
    # RSI>70 → 回落 → 再涨但未超前高 → 跌破前低 = 极强顶部信号
    if len(rsi_vals) >= 10:
        rv = [v for v in rsi_vals[-20:] if v is not None]
        if len(rv) >= 6:
            # 寻找RSI>70后的FTS
            for i in range(2, len(rv)-2):
                if rv[i] > 70 and rv[i+1] < rv[i]:
                    for j in range(i+2, len(rv)-1):
                        if rv[j] > rv[i+1] and rv[j] < rv[i]:  # 未超前高
                            if rv[j+1] < rv[i+1]:               # 跌破前低
                                results['fts_bearish'] = True
                                results['details'].append('RSI失败摆动看空(FTS) 极强顶部信号')
                                results['score_short'] += 6
                                break

    # RSI失败摆动（FTS）- 看多
    if len(rsi_vals) >= 10:
        rv = [v for v in rsi_vals[-20:] if v is not None]
        if len(rv) >= 6:
            for i in range(2, len(rv)-2):
                if rv[i] < 30 and rv[i+1] > rv[i]:
                    for j in range(i+2, len(rv)-1):
                        if rv[j] < rv[i+1] and rv[j] > rv[i]:  # 未破前低
                            if rv[j+1] > rv[i+1]:               # 突破前高
                                results['fts_bullish'] = True
                                results['details'].append('RSI失败摆动看多(FTS) 极强底部信号')
                                results['score_long'] += 6
                                break

    # 日线级别RSI背离额外加分
    rsi_now = rsi_vals[-1] if rsi_vals[-1] is not None else 50
    if results['regular_bearish'] and rsi_now > 65:
        results['score_short'] += 4
        results['details'].append('日线级别RSI背离确认 +4')
    if results['regular_bullish'] and rsi_now < 35:
        results['score_long'] += 4
        results['details'].append('日线级别RSI背离确认 +4')

    return results

# ═══════════════════════════════════════════════════════════════
# 四、MACD背离检测
# ═══════════════════════════════════════════════════════════════

def detect_macd_divergence(closes: list) -> dict:
    """检测MACD柱状图背离"""
    macd_data = calc_macd_series(closes)
    hist      = macd_data['histogram']
    macd_line = macd_data['macd']
    sig_line  = macd_data['signal']

    results = {
        'regular_bearish': False,
        'regular_bullish': False,
        'hidden_bearish':  False,
        'hidden_bullish':  False,
        'zero_cross_up':   False,   # MACD金叉在零轴以下（强多信号）
        'zero_cross_down': False,   # MACD死叉在零轴以上（强空信号）
        'details':         [],
        'score_long':  0,
        'score_short': 0,
    }

    price_pivots = find_pivots(closes, 3)
    hist_pivots  = find_pivots(hist,   3)

    ph = price_pivots['highs']
    pl = price_pivots['lows']
    hh = hist_pivots['highs']
    hl = hist_pivots['lows']

    # 常规看空：价格HH，MACD柱LH
    if len(ph) >= 2 and len(hh) >= 2:
        if ph[-1][1] > ph[-2][1] and hh[-1][1] < hh[-2][1]:
            results['regular_bearish'] = True
            results['score_short'] += 5
            results['details'].append('MACD柱看空背离 +5')

    # 常规看多：价格LL，MACD柱HL
    if len(pl) >= 2 and len(hl) >= 2:
        if pl[-1][1] < pl[-2][1] and hl[-1][1] > hl[-2][1]:
            results['regular_bullish'] = True
            results['score_long'] += 5
            results['details'].append('MACD柱看多背离 +5')

    # 零轴位置金叉/死叉（更强信号）
    valid_macd = [(i, v) for i, v in enumerate(macd_line) if v is not None]
    valid_sig  = [(i, v) for i, v in enumerate(sig_line)  if v is not None]
    if len(valid_macd) >= 2 and len(valid_sig) >= 2:
        m_prev, m_curr = valid_macd[-2][1], valid_macd[-1][1]
        s_prev, s_curr = valid_sig[-2][1],  valid_sig[-1][1]
        # 金叉
        if m_prev <= s_prev and m_curr > s_curr:
            if m_curr < 0:
                results['zero_cross_up'] = True
                results['score_long'] += 4
                results['details'].append('MACD金叉(零轴以下) 强多信号 +4')
            else:
                results['score_long'] += 2
                results['details'].append('MACD金叉 +2')
        # 死叉
        if m_prev >= s_prev and m_curr < s_curr:
            if m_curr > 0:
                results['zero_cross_down'] = True
                results['score_short'] += 4
                results['details'].append('MACD死叉(零轴以上) 强空信号 +4')
            else:
                results['score_short'] += 2
                results['details'].append('MACD死叉 +2')

    return results

# ═══════════════════════════════════════════════════════════════
# 五、K线形态识别
# ═══════════════════════════════════════════════════════════════

def detect_candlestick_patterns(opens: list, highs: list,
                                 lows: list, closes: list) -> dict:
    """识别最近K线形态"""
    if len(closes) < 3:
        return {'patterns': [], 'score_long': 0, 'score_short': 0}

    o, h, l, c = opens, highs, lows, closes
    patterns = []
    score_long = 0
    score_short = 0

    # 最近3根K线
    def body(i):   return abs(c[i] - o[i])
    def upper(i):  return h[i] - max(c[i], o[i])
    def lower(i):  return min(c[i], o[i]) - l[i]
    def is_bull(i): return c[i] > o[i]
    def is_bear(i): return c[i] < o[i]

    avg_body = sum(body(i) for i in range(-5, 0)) / 5 if len(closes) >= 5 else body(-1)

    i = -1  # 最新K线

    # 锤子线（看多反转）
    if (lower(i) >= body(i) * 2 and upper(i) < body(i) * 0.3
            and body(i) > 0 and len(closes) >= 10):
        trend_check = closes[-1] < closes[-10]   # 在下跌中
        if trend_check:
            patterns.append('锤子线(看多反转)')
            score_long += 4

    # 射击之星（看空反转）
    if (upper(i) >= body(i) * 2 and lower(i) < body(i) * 0.3
            and body(i) > 0 and len(closes) >= 10):
        trend_check = closes[-1] > closes[-10]   # 在上涨中
        if trend_check:
            patterns.append('射击之星(看空反转)')
            score_short += 4

    # 多头吞没
    if len(closes) >= 2:
        if (is_bear(-2) and is_bull(-1)
                and c[-1] > o[-2] and o[-1] < c[-2]
                and body(-1) > body(-2)):
            patterns.append('多头吞没')
            score_long += 5

    # 空头吞没
    if len(closes) >= 2:
        if (is_bull(-2) and is_bear(-1)
                and c[-1] < o[-2] and o[-1] > c[-2]
                and body(-1) > body(-2)):
            patterns.append('空头吞没')
            score_short += 5

    # 晨星（三K看多）
    if len(closes) >= 3:
        if (is_bear(-3) and body(-2) < avg_body * 0.4
                and is_bull(-1) and c[-1] > (o[-3] + c[-3]) / 2):
            patterns.append('晨星(三K看多反转)')
            score_long += 6

    # 暮星（三K看空）
    if len(closes) >= 3:
        if (is_bull(-3) and body(-2) < avg_body * 0.4
                and is_bear(-1) and c[-1] < (o[-3] + c[-3]) / 2):
            patterns.append('暮星(三K看空反转)')
            score_short += 6

    # 大阳线
    if body(i) > avg_body * 1.5 and is_bull(i):
        patterns.append('大阳线(多头强势)')
        score_long += 3

    # 大阴线
    if body(i) > avg_body * 1.5 and is_bear(i):
        patterns.append('大阴线(空头强势)')
        score_short += 3

    return {
        'patterns':    patterns,
        'score_long':  min(score_long, 10),
        'score_short': min(score_short, 10),
    }

# ═══════════════════════════════════════════════════════════════
# 六、综合背离评分（0~20分）
# ═══════════════════════════════════════════════════════════════

def _calc_volume_contraction(closes: list, volumes: list, lookback: int = 6, vol_mult: float = 0.6) -> dict:
    """
    达摩院100轮实训验证：vol_mult=0.6 是量价背离最优参数
    检测：价格创新高/低时成交量是否萎缩（量价背离核心条件）
    returns: {'vol_contraction': bool, 'vol_ratio': float, 'rsi_lo': bool, 'rsi_hi': bool}
    """
    if not volumes or len(volumes) < lookback + 5:
        return {'vol_contraction': False, 'vol_ratio': 1.0, 'price_extreme': False}
    n = len(closes)
    recent_vol = volumes[-1]
    # 20日成交量均值
    vol_ma = sum(volumes[max(0,n-20):n]) / min(20, n)
    vol_ratio = recent_vol / vol_ma if vol_ma > 0 else 1.0
    # 成交量萎缩：当前量 < 均量 * vol_mult
    vol_contraction = vol_ratio < vol_mult
    # 价格是否在 lookback 内创新高/低
    recent_h = closes[-1]
    recent_l = closes[-1]
    price_hi = recent_h >= max(closes[max(0,n-lookback-1):n-1])
    price_lo = recent_l <= min(closes[max(0,n-lookback-1):n-1])
    return {
        'vol_contraction': vol_contraction,
        'vol_ratio': round(vol_ratio, 3),
        'price_extreme_hi': price_hi,
        'price_extreme_lo': price_lo,
        'vol_ma': round(vol_ma, 2),
    }


def divergence_score(opens: list, highs: list, lows: list,
                     closes: list, signal_dir: str,
                     interval_label: str = '1H',
                     volumes: list = None,
                     regime: str = '',
                     ts_ms: int = 0) -> dict:
    """
    综合背离评分 v3 —达摩院100轮实训升级版
    新增：
      1. 成交量验证（vol_mult=0.6 铁证参数）
      2. 时间窗口过滤（周二/12月惩罚）
      3. 体制自动切换（单边牛市RSI>60三周 → 背离降权）
    优先级：隐藏背离 >常观背离 > FTS失败摇动
    signal_dir: 'LONG' or 'SHORT'
    """
    rsi_div  = detect_rsi_divergence(closes)
    macd_div = detect_macd_divergence(closes)
    candle   = detect_candlestick_patterns(opens, highs, lows, closes)

    # ── 成交量验证（达摩院100轮铁证：vol_mult=0.6）────────────
    vol_info = _calc_volume_contraction(closes, volumes or [], lookback=6, vol_mult=0.6)
    # 成交量萎缩时做量价背离加分，成交量放大时降分（假突破/追涨危险）
    vol_bonus = 0
    vol_penalty = 0
    if volumes:
        if vol_info['vol_contraction']:
            # 量缩+价格创极值 = 经典量价背离，+3分
            if (signal_dir == 'LONG' and vol_info['price_extreme_lo']) or \
               (signal_dir == 'SHORT' and vol_info['price_extreme_hi']):
                vol_bonus = 3
        else:
            # 量放时信号弱，-2分（100轮实训：放量时量价背离失效）
            vol_penalty = 2

    # ── 时间窗口过滤（达摩院实训铁证）──────────────────────────
    time_penalty = 0
    time_note = ''
    if ts_ms > 0:
        try:
            from datetime import datetime, timezone
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            weekday = dt.weekday()  # 0=周一, 1=周二
            month = dt.month
            # 周二：BTC仅27%胜率，ETH仅40%（100轮实训铁证）
            if weekday == 1:  # 周二
                time_penalty += 3
                time_note = '⚠️周二时间惩罚-3（实训胜率仅27-40%）'
            # 12月：ETH量价背离24%（100轮实训铁证）
            if month == 12:
                time_penalty += 4
                time_note += ' ⚠️12月季节性惩罚-4（ETH实训仅24%）'
            # 周末加分：周六ETH80%/BTC68%（100轮实训铁证）
            if weekday in (5, 6):  # 周六/周日
                vol_bonus += 2
                time_note += ' ✅周末加成+2（实训胜率55-80%）'
        except Exception:
            pass

    # ── 体制切换检测（单边牛市降权）───────────────────────────
    regime_penalty = 0
    regime_note = ''
    if regime:
        regime_upper = regime.upper()
        # 单边BULL_TREND时量价背离做多失效（2021减半：14%）
        if 'BULL_TREND' in regime_upper and signal_dir == 'LONG':
            # 在强趋势牛市里，量缩不是衰竭，而是洗盘后继续
            regime_penalty = 3
            regime_note = '⚠️单边牛市量价背离做多降权-3（实训14%）'
        # BEAR_IMPULSE/BEAR_TREND做空时额外奖励（2022Luna：60% PF12.57）
        elif any(x in regime_upper for x in ['BEAR_IMPULSE', 'BEAR_TREND']) and signal_dir == 'SHORT':
            vol_bonus += 2
            regime_note = '✅熊市背离做空加成+2（实训60% PF12.57）'

    # ── 强度分级评分逻辑 ────────────────────────────────
    # 主要信号和分数：
    # S级(隐藏背离)    = 12分  延续趋势最可靠
    # A级(常观背离)    = 10分  趋势反转信号
    # B级(FTS失败摇动) = 8分   高胜率底部
    # C级(MACD单策题)    = 5分
    # D级(K线形态)       = 3分

    grade = 'NONE'
    grade_score = 0
    grade_notes = []

    if signal_dir == 'LONG':
        # S级：隐藏看多背离（价格 HL RSI HH — 下降趋势延续）
        if rsi_div.get('hidden_bullish'):
            grade = 'S'; grade_score = 12
            grade_notes.append('🔵 隐藏看多背离(S级) 趋势延续最可靠 +12')
        # A级：常观看多背离（价格 LL RSI HL — 反转信号）
        elif rsi_div.get('regular_bullish'):
            grade = 'A'; grade_score = 10
            grade_notes.append('🟢 常观看多背离(A级) 底部反转信号 +10')
        # B级： RSI FTS
        elif rsi_div.get('fts_bullish'):
            grade = 'B'; grade_score = 8
            grade_notes.append('🟡 RSI失败摇动看多(B级) +8')
        # C级： MACD单独背离
        elif macd_div.get('regular_bullish') or macd_div.get('hidden_bullish'):
            grade = 'C'; grade_score = 5
            grade_notes.append('🟠 MACD背离(C级) +5')

        # 叠加加分：MACD + RSI 同时共振
        if grade in ('A','B') and (macd_div.get('regular_bullish') or macd_div.get('zero_cross_up')):
            grade_score = min(grade_score + 3, 18)
            grade_notes.append('+MACD共振 +3')
        # MACD 0轴位置加分
        macd_zero_bonus = 2 if macd_div.get('zero_cross_up') else 0
        grade_score = min(grade_score + macd_zero_bonus, 20)
        if macd_zero_bonus:
            grade_notes.append('MACD穿越0轴 +2')

        # K线形态加分
        candle_bonus = min(candle['score_long'], 4)
        grade_score  = min(grade_score + candle_bonus, 20)
        if candle_bonus and candle['patterns']:
            grade_notes.append(f'K线{candle["patterns"][0]} +{candle_bonus}')

        raw = grade_score
        # 兼容旧字段
        rsi_dir_s = rsi_div.get('score_long', 0)
        macd_dir_s= macd_div.get('score_long', 0)
        details_dir = (
            [f'[{interval_label}] ' + d for d in rsi_div['details'] if '看多' in d or '多' in d] +
            [f'[{interval_label}] ' + d for d in macd_div['details'] if '多' in d or '金叉' in d]
        )
    else:  # SHORT
        # S级：隐藏看空背离（价格 HH RSI LH — 上涨趋势延续）
        if rsi_div.get('hidden_bearish'):
            grade = 'S'; grade_score = 12
            grade_notes.append('🔵 隐藏看空背离(S级) 下跌趋势延续 +12')
        # A级：常观看空背离（价格 HH RSI LH — 顶部反转）
        elif rsi_div.get('regular_bearish'):
            grade = 'A'; grade_score = 10
            grade_notes.append('🟢 常观看空背离(A级) 顶部反转信号 +10')
        # B级： RSI FTS
        elif rsi_div.get('fts_bearish'):
            grade = 'B'; grade_score = 8
            grade_notes.append('🟡 RSI失败摇动看空(B级) +8')
        # C级： MACD单独背离
        elif macd_div.get('regular_bearish') or macd_div.get('hidden_bearish'):
            grade = 'C'; grade_score = 5
            grade_notes.append('🟠 MACD背离(C级) +5')

        # 叠加共振
        if grade in ('A','B') and (macd_div.get('regular_bearish') or macd_div.get('zero_cross_down')):
            grade_score = min(grade_score + 3, 18)
            grade_notes.append('+MACD共振 +3')
        macd_zero_pen = -2 if macd_div.get('zero_cross_up') else 0  # 0轴上方做空 = 降分
        # (0轴下方做空不调整)
        grade_score = max(0, grade_score + macd_zero_pen)

        candle_bonus = min(candle['score_short'], 4)
        grade_score  = min(grade_score + candle_bonus, 20)
        if candle_bonus and candle['patterns']:
            grade_notes.append(f'K线{candle["patterns"][0]} +{candle_bonus}')

        raw = grade_score
        details_dir = (
            [f'[{interval_label}] ' + d for d in rsi_div['details'] if '看空' in d or '空' in d] +
            [f'[{interval_label}] ' + d for d in macd_div['details'] if '空' in d or '死叉' in d]
        )

    # [修复] NONE级别：加入辅助分（MACD趋势方向+对手背离惩罚）
    if grade == 'NONE':
        # MACD方向轻微辅助
        if signal_dir == 'SHORT':
            if macd_div.get('zero_cross_down'):
                raw = 4; grade_notes.append('MACD死叉辅助 +4')
            elif macd_div.get('regular_bearish'):
                raw = 3; grade_notes.append('MACD弱看空 +3')
            # 对手强背离惩罚（看多背离出现，做空降分）
            elif rsi_div.get('regular_bullish') or rsi_div.get('fts_bullish'):
                raw = 0; grade_notes.append('⚠️ 对手看多背离，做空谨慎')
            else:
                raw = 2  # 中性小分
        else:  # LONG
            if macd_div.get('zero_cross_up'):
                raw = 4; grade_notes.append('MACD金叉辅助 +4')
            elif macd_div.get('regular_bullish'):
                raw = 3; grade_notes.append('MACD弱看多 +3')
            elif rsi_div.get('regular_bearish') or rsi_div.get('fts_bearish'):
                raw = 0; grade_notes.append('⚠️ 对手看空背离，做多谨慎')
            else:
                raw = 2
    score = min(raw, 20)
    # ── 达摩院实训 v3 修正项应用────────────────────────────────────
    # 成交量加分/惩分
    score = max(0, min(score + vol_bonus - vol_penalty, 20))
    # 时间窗口惩分（周二/-12月）
    score = max(0, score - time_penalty)
    # 体制惩分/加分
    score = max(0, min(score - regime_penalty, 20))
    # 更新详细信息
    if vol_bonus > 0 and volumes:
        grade_notes.append(f'✅量缩验证加分+{vol_bonus}(vol={vol_info["vol_ratio"]:.2f}x均值)')
    if vol_penalty > 0 and volumes:
        grade_notes.append(f'⚠️放量空间惩分-{vol_penalty}(量价背离密度降低)')
    if time_note:
        grade_notes.append(time_note)
    if regime_note:
        grade_notes.append(regime_note)

    grade_label = {'S': '🔵S级(隐藏)', 'A': '🟢A级(常观)', 'B': '🟡B级(FTS)', 'C': '🟠C级(MACD)', 'NONE': '⚪无背离'}.get(grade, grade)

    return {
        'score':       score,
        'max':         20,
        'grade':       grade,
        'grade_label': grade_label,
        'grade_notes': grade_notes,
        'details':     grade_notes + details_dir,
        'rsi_div':     rsi_div,
        'macd_div':    macd_div,
        'candle':      candle,
        'vol_info':    vol_info if volumes else {},
        'vol_bonus':   vol_bonus,
        'vol_penalty': vol_penalty,
        'time_penalty':time_penalty,
        'regime_penalty': regime_penalty,
    }


# ═══════════════════════════════════════════════════════════════
# 多周期背离共振 v4（设计院 2026-06-05）
# 三级共振：15M + 1H + 4H 同时背离 → 极强底/顶部信号
# ═══════════════════════════════════════════════════════════════

def multitf_divergence_score(symbol: str, signal_dir: str) -> dict:
    """
    多周期背离共振评分（最高20分）
    同时满足的周期越多，分数越高，底/顶部可靠性越强

    共振等级：
      三级共振（15M+1H+4H）→ TRIPLE  +20分
      双级共振（任意两个）  → DOUBLE  +12分
      单周期              → SINGLE  +6分
      无背离              → NONE    +0分
    """
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from data_cache import get_klines, klines_to_ohlcv
    except Exception as e:
        return {'score': 0, 'resonance': 'NONE', 'notes': [f'数据加载失败: {e}']}

    tf_results = {}
    tf_scores  = {}

    for tf in ['15m', '1h', '4h']:
        try:
            k = klines_to_ohlcv(get_klines(symbol, tf, 150))
            if not k or len(k.get('c', [])) < 30:
                continue
            vols = k.get('v', [])
            res = divergence_score(
                k['o'], k['h'], k['l'], k['c'],
                signal_dir, tf.upper(),
                volumes=vols if vols else None
            )
            tf_results[tf] = res
            tf_scores[tf]  = res['score']
        except Exception:
            continue

    if not tf_scores:
        return {'score': 0, 'resonance': 'NONE', 'notes': ['无法获取数据']}

    # 有效背离：score >= 8 视为该周期有信号
    active = {tf: s for tf, s in tf_scores.items() if s >= 8}
    notes  = []

    for tf, s in tf_scores.items():
        grade = tf_results[tf].get('grade', '?')
        notes.append(f'{tf.upper()} 背离={grade} score={s}')

    if len(active) >= 3:
        resonance = 'TRIPLE'
        base_score = 20
        notes.insert(0, f'🔥 三级共振！{list(active.keys())} 同步背离 → 极强{"底" if signal_dir=="LONG" else "顶"}部信号')
    elif len(active) >= 2:
        resonance = 'DOUBLE'
        base_score = 12
        notes.insert(0, f'⚡ 双级共振 {list(active.keys())} → {"底" if signal_dir=="LONG" else "顶"}部确认增强')
    elif len(active) >= 1:
        resonance = 'SINGLE'
        base_score = 6
        notes.insert(0, f'✅ 单周期背离 {list(active.keys())}')
    else:
        resonance = 'NONE'
        base_score = 0
        notes.insert(0, '无多周期背离共振')

    # 加分：高grade背离
    bonus = 0
    for tf, res in tf_results.items():
        if res.get('grade') in ('S', 'A') and tf_scores.get(tf, 0) >= 8:
            bonus += 2
            notes.append(f'{tf.upper()} {res["grade"]}级背离 +2')

    return {
        'score':     min(base_score + bonus, 20),
        'resonance': resonance,
        'tf_scores': tf_scores,
        'active_tfs': list(active.keys()),
        'notes':     notes,
        'details':   {tf: tf_results[tf].get('grade_label','?') for tf in tf_results},
    }


# ─── 快速测试 ────────────────────────────────────────────────
if __name__ == '__main__':
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from data_cache import get_klines, klines_to_ohlcv

    sym = sys.argv[1] if len(sys.argv) > 1 else 'ETHUSDT'
    direction = sys.argv[2] if len(sys.argv) > 2 else 'SHORT'

    k1h = klines_to_ohlcv(get_klines(sym, '1h', 200))
    k4h = klines_to_ohlcv(get_klines(sym, '4h', 200))

    pass  # [静默]

    for label, k in [('1H', k1h), ('4H', k4h)]:
        res = divergence_score(k['o'], k['h'], k['l'], k['c'], direction, label)
        print(f'\n  {label} 背离评分: {res["score"]}/20')
        for d in res['details']:
            print(f'    {d}')

    pass  # [静默]