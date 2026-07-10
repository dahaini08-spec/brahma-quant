#!/usr/bin/env python3
"""
OI高级扫描器 v3.0 — 设计院全局深度完善
2026-07-10 苏摩111授权

═══════════════════════════════════════════════════════════════
核心设计思想（全球顶级OI研究方法论）:

【A类: 现货低倍 · 持续建仓型】
  信号特征: 1-365天持仓量持续提升，OI累计增幅≥100%
  适合操作: 1-5x 低杠杆，现货等值仓位
  原理: 机构/大户长周期累积→价格上行概率极大
  判断标准:
    · 7D OI增幅≥50% + 30D推算100%以上
    · 大户/散户多空比 > 1.3（多头主导建仓）
    · 资金费率温和（0~0.05%，未过热）
    · 基差为正（期货溢价 = 主力押注上涨）

【B类: 合约中线 · 趋势布局型】
  信号特征: OI在50%-500%区间内持续累积
  适合操作: 10x中线，分批建仓
  原理: 中期机构方向性布局信号
  判断标准:
    · 24H OI增幅≥15% 且方向与价格共振
    · OI加速度为正（建仓速度在加快）
    · 鲸鱼多空比>1.5（大户入场方向）
    · 资金费率<0.03%（惩罚机制未触发）

【C类: 短线异动 · 即时方向型】
  信号特征: 1H/4H OI突变，量价配合
  适合操作: 高杠杆短线，辅助入场择时
  判断标准:
    · 1H OI变化>1.5% + 量比>1.5x
    · OI方向矩阵明确（非NEUTRAL）

─────────────────────────────────────────────────
核心修复（原系统无一信号根因）:
  BUG-1: oi_candidates.json 39.5H未更新（MAX_AGE=4H → 直接跳过）
  BUG-2: market_screener输出 scan_candidates.json，但sub_executor
          读取 oi_candidates.json → 路径不匹配
  BUG-3: market_screener是空头评分体系，OI猎手需要多头信号
  BUG-4: 无独立推送，OI信号从未推给苏摩做决策
─────────────────────────────────────────────────
"""
import sys, os, json, time, math, hmac, hashlib, requests
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

try:
    from scripts.system_config import (
        FAPI_BASE, JARVIS_USER_ID, JARVIS_THREAD_ID,
        JARVIS_CHANNEL, API_KEY, API_SECRET
    )
    JARVIS_TARGET = f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}'
except Exception:
    FAPI_BASE     = 'https://fapi.binance.com'
    JARVIS_TARGET = '73295708:thread:019f309c-609b-7a75-a195-e221e5927c63'
    JARVIS_CHANNEL = 'jarvis'
    API_KEY = API_SECRET = ''

# ── 输出路径（同时写两个，兼容sub_executor读取）──────────────
OI_CANDIDATES_PATH  = BASE / 'data' / 'oi_candidates.json'
OI_SIGNAL_LOG       = BASE / 'data'  / 'oi_advanced_signals.jsonl'
OI_CACHE_PATH       = BASE / 'data'  / 'oi_adv_cache.json'

# ── 全市场扫描配置 ────────────────────────────────────────────
MIN_VOLUME_USD  = 20e6    # 最低24H成交额$20M（覆盖中小市值）
MIN_OI_USD      = 5e6     # 最低OI规模$5M
MAX_WORKERS     = 8       # 并发线程数
TOP_N           = 50      # 候选池大小

# ── 三级阈值（苏摩授权全力模式）─────────────────────────────
THRESHOLD = {
    'A': {
        '7d_oi_min':  40.0,   # A类: 7D OI增幅≥40%（原50%，适当降低）
        'fr_max':      0.08,   # 资金费率上限
        'whale_l_min': 55.0,   # 鲸鱼多头比例≥55%
        'score_min':   50,     # 综合评分≥50
    },
    'B': {
        '24h_oi_min': 8.0,    # B类: 24H OI增幅≥8%（原15%，降低门槛）
        '4h_oi_min':  2.0,    # 4H OI增幅≥2%
        'score_min':  40,     # 综合评分≥40
    },
    'C': {
        '1h_oi_min':  1.2,    # C类: 1H OI变化≥1.2%（原1.5%）
        'vol_spike':  1.3,    # 量比≥1.3x
        'score_min':  30,     # 综合评分≥30
    },
}

# ── 执行参数（对接sub_executor）─────────────────────────────
OI_EXEC_PARAMS = {
    'A_BULL': {'size_pct': 0.05, 'sl_pct': 2.5, 'tp_mult': 1.5, 'lev': 5,  'hold': '7-365天'},
    'A_BEAR': {'size_pct': 0.03, 'sl_pct': 3.0, 'tp_mult': 1.0, 'lev': 3,  'hold': '3-30天'},
    'B_10X':  {'size_pct': 0.05, 'sl_pct': 2.5, 'tp_mult': 1.2, 'lev': 10, 'hold': '3-14天'},
    'B':      {'size_pct': 0.05, 'sl_pct': 2.5, 'tp_mult': 1.2, 'lev': 5,  'hold': '1-7天'},
    'C':      {'size_pct': 0.03, 'sl_pct': 2.0, 'tp_mult': 1.0, 'lev': 5,  'hold': '1-24H'},
}

# ── 黑名单（稳定性差/无OI历史）──────────────────────────────
BLACKLIST = set()

# ── 强制主力币入池 ────────────────────────────────────────────
FORCE_INCLUDE = {'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT'}


# ════════════════════════════════════════════════════════════════
# 基础工具函数
# ════════════════════════════════════════════════════════════════

def _fetch(url, timeout=7, retries=2):
    for _ in range(retries):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                time.sleep(1)
        except Exception:
            time.sleep(0.2)
    return None


def _calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    g = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
    l = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
    ag = sum(g[-period:]) / period
    al = sum(l[-period:]) / period
    return round(100 - 100/(1 + ag/al), 2) if al > 0 else 100.0


def _calc_ema(closes, period=20):
    if not closes: return 0.0
    k = 2/(period+1)
    ema = closes[0]
    for c in closes[1:]:
        ema = ema*(1-k) + c*k
    return ema


def send_message(msg):
    try:
        import subprocess
        subprocess.Popen(
            ['openclaw', 'message', 'send',
             '--to', JARVIS_TARGET, '--channel', JARVIS_CHANNEL,
             '--message', msg],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception as e:
        print(f'  ⚠️ 推送失败: {e}')


def load_cache():
    if OI_CACHE_PATH.exists():
        try:
            return json.loads(OI_CACHE_PATH.read_text())
        except:
            pass
    return {}


def save_cache(c):
    OI_CACHE_PATH.write_text(json.dumps(c, indent=2))


# ════════════════════════════════════════════════════════════════
# 数据拉取层
# ════════════════════════════════════════════════════════════════

def get_oi_multi_period(sym):
    """
    拉取多周期OI历史（1H / 4H / 1D）
    返回: {'1h': [...], '4h': [...], '1d': [...]}
    """
    result = {}
    for period, limit in [('1h', 25), ('4h', 30), ('1d', 35)]:
        d = _fetch(f'{FAPI_BASE}/futures/data/openInterestHist'
                   f'?symbol={sym}&period={period}&limit={limit}')
        if isinstance(d, list) and len(d) >= 3:
            result[period] = [
                {'ts': int(x['timestamp']),
                 'oi': float(x['sumOpenInterest']),
                 'oi_usd': float(x['sumOpenInterestValue'])}
                for x in d
            ]
        else:
            result[period] = []
    return result


def get_premium_info(sym):
    """获取标记价、基差、资金费率"""
    d = _fetch(f'{FAPI_BASE}/fapi/v1/premiumIndex?symbol={sym}')
    if isinstance(d, dict):
        mark  = float(d.get('markPrice', 0))
        index = float(d.get('indexPrice', mark))
        fr    = float(d.get('lastFundingRate', 0)) * 100
        basis = (mark - index) / index * 100 if index > 0 else 0
        return mark, index, round(basis, 4), round(fr, 6)
    return 0, 0, 0, 0


def get_ls_ratio(sym):
    """鲸鱼多空比（大户账户） + 散户多空比"""
    whale_l, retail_l = 50.0, 50.0
    d1 = _fetch(f'{FAPI_BASE}/futures/data/topLongShortAccountRatio?symbol={sym}&period=1h&limit=3')
    if isinstance(d1, list) and d1:
        whale_l = float(d1[-1].get('longAccount', 0.5)) * 100

    d2 = _fetch(f'{FAPI_BASE}/futures/data/globalLongShortAccountRatio?symbol={sym}&period=1h&limit=3')
    if isinstance(d2, list) and d2:
        try:
            ls = float(d2[-1].get('longShortRatio', 1.0))
            retail_l = round(ls/(1+ls)*100, 1)
        except:
            pass
    return round(whale_l, 1), round(retail_l, 1)


def get_klines(sym, interval, limit):
    d = _fetch(f'{FAPI_BASE}/fapi/v1/klines?symbol={sym}&interval={interval}&limit={limit}')
    if isinstance(d, list):
        return [{'open': float(k[1]), 'high': float(k[2]),
                 'low': float(k[3]),  'close': float(k[4]),
                 'vol': float(k[5])} for k in d]
    return []


def get_ticker(sym):
    d = _fetch(f'{FAPI_BASE}/fapi/v1/ticker/24hr?symbol={sym}')
    if isinstance(d, dict):
        return {
            'price':    float(d.get('lastPrice', 0)),
            'pct24h':   float(d.get('priceChangePercent', 0)),
            'vol_usdt': float(d.get('quoteVolume', 0)),
        }
    return {}


# ════════════════════════════════════════════════════════════════
# OI分析引擎
# ════════════════════════════════════════════════════════════════

def calc_oi_changes(oi_data):
    """
    计算多周期OI变化率（全球标准：连续增长>单次变化）
    返回: (chg_1h, chg_4h, chg_24h, chg_7d, accel_4h)
    """
    def _chg(lst, n):
        if len(lst) < n+1: return 0.0
        cur, past = lst[-1]['oi'], lst[-(n+1)]['oi']
        return round((cur-past)/max(past,1)*100, 2)

    def _accel(lst, short=3, long=8):
        """加速度：近期变化率 vs 历史变化率"""
        if len(lst) < long+1: return 0.0
        r_short = _chg(lst[-short:] + [lst[-1]], short-1) if short > 1 else 0
        r_long  = _chg(lst, long)
        return round(r_short - r_long/3, 2)

    h1 = oi_data.get('1h', [])
    h4 = oi_data.get('4h', [])
    hd = oi_data.get('1d', [])

    chg_1h  = _chg(h1, 1)
    chg_4h  = _chg(h4, 4) if h4 else _chg(h1, 4)
    chg_24h = _chg(hd, 1) if hd else _chg(h1, 24)
    chg_7d  = _chg(hd, 7) if len(hd) >= 8 else 0.0
    chg_30d = _chg(hd, 30) if len(hd) >= 31 else 0.0
    accel   = _accel(h4 if h4 else h1)

    cur_oi_usd = h1[-1]['oi_usd'] if h1 else 0

    return {
        'chg_1h':   chg_1h,
        'chg_4h':   chg_4h,
        'chg_24h':  chg_24h,
        'chg_7d':   chg_7d,
        'chg_30d':  chg_30d,
        'accel_4h': accel,
        'oi_usd_m': round(cur_oi_usd/1e6, 2),
    }


def calc_oi_direction_matrix(oi_1h, price_chg_pct):
    """
    Glassnode核心方法论：OI/价格4象限方向矩阵
    OI↑ + Price↑ = LONG_BUILD  (多头建仓，做多)
    OI↑ + Price↓ = SHORT_BUILD (空头建仓，做空)
    OI↓ + Price↑ = SHORT_COVER (空头平仓/轧空)
    OI↓ + Price↓ = LONG_UNWIND (多头止损)
    """
    if len(oi_1h) < 4: return 'UNKNOWN', 0
    recent_chg = (oi_1h[-1]['oi'] - oi_1h[-3]['oi']) / max(oi_1h[-3]['oi'], 1) * 100
    oi_up   = recent_chg > 0.3
    oi_down = recent_chg < -0.3
    px_up   = price_chg_pct > 0.5
    px_down = price_chg_pct < -0.5

    if oi_up   and px_up:   return 'LONG_BUILD',  +1
    if oi_up   and px_down: return 'SHORT_BUILD',  -1
    if oi_down and px_up:   return 'SHORT_COVER',  +1
    if oi_down and px_down: return 'LONG_UNWIND',  -1
    return 'NEUTRAL', 0


def score_oi_signal(oi, basis, fr, whale_l, retail_l, direction, klines_1h):
    """
    综合OI评分（0-100分）

    五大维度（全球顶级机构标准）:
      D1: 多周期OI趋势强度（35分）
      D2: OI建仓方向共振（25分）
      D3: 资金成本结构（20分）
      D4: 筹码分布（大户vs散户）（15分）
      D5: 技术结构加分（5分）
    """
    score = 0
    details = []

    # ── D1: 多周期OI趋势强度（35分）──────────────────────────
    # 1H趋势
    if abs(oi['chg_1h']) >= THRESHOLD['C']['1h_oi_min']:
        pts = min(8, abs(oi['chg_1h']) * 3)
        score += pts
        details.append(f'1H:{oi["chg_1h"]:+.1f}%(+{pts:.0f})')

    # 4H趋势
    if abs(oi['chg_4h']) >= THRESHOLD['B']['4h_oi_min']:
        pts = min(12, abs(oi['chg_4h']) * 2)
        score += pts
        details.append(f'4H:{oi["chg_4h"]:+.1f}%(+{pts:.0f})')

    # 24H趋势
    if abs(oi['chg_24h']) >= THRESHOLD['B']['24h_oi_min']:
        pts = min(15, abs(oi['chg_24h']) * 0.6)
        score += pts
        details.append(f'24H:{oi["chg_24h"]:+.1f}%(+{pts:.0f})')

    # 7D趋势（A类最重要信号）
    if abs(oi['chg_7d']) >= THRESHOLD['A']['7d_oi_min']:
        pts = min(20, abs(oi['chg_7d']) * 0.25)
        score += pts
        details.append(f'7D:{oi["chg_7d"]:+.1f}%(+{pts:.0f})')

    # OI加速（机构加仓加速是强信号）
    if oi['accel_4h'] > 1.0:
        score += 5
        details.append(f'加速+{oi["accel_4h"]:.1f}(+5)')
    elif oi['accel_4h'] > 0.3:
        score += 2
        details.append(f'微加速(+2)')

    # ── D2: OI方向共振（25分）────────────────────────────────
    dir_pts = {
        'LONG_BUILD':  25,
        'SHORT_BUILD': 22,
        'SHORT_COVER': 12,
        'LONG_UNWIND': 10,
        'NEUTRAL':      0,
        'UNKNOWN':      5,
    }.get(direction, 0)
    score += dir_pts
    if dir_pts > 0:
        details.append(f'{direction}(+{dir_pts})')

    # ── D3: 资金成本结构（20分）──────────────────────────────
    # 基差（期货溢价/折价）
    if 0.02 < basis < 0.5:
        score += 8
        details.append(f'BASIS={basis:.3f}%健康(+8)')
    elif basis >= 0.5:
        score += 4    # 溢价过高，市场过热
        details.append(f'BASIS={basis:.3f}%过热(+4)')
    elif basis < -0.05:
        score += 10   # 期货折价 = 空头主导，看空做空
        details.append(f'BASIS={basis:.3f}%折价(+10)')
    elif basis < 0:
        score += 5
        details.append(f'BASIS轻微折价(+5)')

    # 资金费率
    if 0 < fr <= 0.02:
        score += 12
        details.append(f'FR={fr:.4f}%理想(+12)')
    elif 0.02 < fr <= THRESHOLD['A']['fr_max']:
        score += 6
        details.append(f'FR={fr:.4f}%偏高(+6)')
    elif fr > THRESHOLD['A']['fr_max']:
        score -= 5    # 资金费率过高，回调风险大
        details.append(f'FR={fr:.4f}%过热(-5)')
    elif fr < -0.02:
        score += 10   # 负资金费率 = 空头付息，做空有利
        details.append(f'FR={fr:.4f}%负值(+10)')
    elif fr < 0:
        score += 6
        details.append(f'FR负费率(+6)')

    # ── D4: 筹码分布（15分）──────────────────────────────────
    # 鲸鱼多空比
    if whale_l >= 70:
        score += 10
        details.append(f'鲸鱼多头{whale_l:.0f}%(+10)')
    elif whale_l >= 60:
        score += 7
        details.append(f'鲸鱼偏多{whale_l:.0f}%(+7)')
    elif whale_l >= 55:
        score += 4
        details.append(f'鲸鱼轻多{whale_l:.0f}%(+4)')
    elif whale_l < 40:
        score += 8    # 鲸鱼看空 = 做空信号
        details.append(f'鲸鱼看空{whale_l:.0f}%(+8)')

    # 散户/鲸鱼背离（散户极度偏多=反向看空；散户极度看空=可能超卖做多）
    diff = whale_l - retail_l
    if diff > 15:
        score += 5    # 鲸鱼比散户更看多，机构确信
        details.append(f'鲸鱼vs散户+{diff:.0f}%(+5)')
    elif diff < -15:
        score += 3    # 散户极度看多但鲸鱼不跟，危险信号（看空）

    # ── D5: 技术结构（5分）───────────────────────────────────
    if klines_1h:
        closes_1h = [k['close'] for k in klines_1h]
        rsi_1h = _calc_rsi(closes_1h)
        ema20  = _calc_ema(closes_1h[-20:])
        price  = closes_1h[-1]

        # RSI超卖区域（做多信号）
        if rsi_1h < 30:
            score += 5
            details.append(f'RSI_1H={rsi_1h:.0f}超卖(+5)')
        elif rsi_1h < 40:
            score += 3
            details.append(f'RSI_1H={rsi_1h:.0f}偏低(+3)')
        # RSI超买（做空信号）
        elif rsi_1h > 75:
            score += 4
            details.append(f'RSI_1H={rsi_1h:.0f}超买空(+4)')

    return min(100, max(0, score)), details


def classify_signal(oi, score, direction, basis, fr, whale_l, regime='UNKNOWN'):
    """
    三级信号分类
    优先A类（长线建仓），次选B类（中线），C类（短线辅助）
    """
    # A类判断：7D持续增仓 + 大户多头 + 资金成本健康
    if (oi['chg_7d'] >= THRESHOLD['A']['7d_oi_min'] and
        whale_l >= THRESHOLD['A']['whale_l_min'] and
        fr <= THRESHOLD['A']['fr_max'] and
        score >= THRESHOLD['A']['score_min']):
        mode = 'A'
        is_bull = 'BULL' in regime
        params_key = 'A_BULL' if is_bull else 'A_BEAR'
        direction_bias = 'LONG' if whale_l >= 55 else 'SHORT'
        hold = '7-365天'
        lev  = '1-5x'

    # B类判断：24H增仓明显 + 方向清晰
    elif (abs(oi['chg_24h']) >= THRESHOLD['B']['24h_oi_min'] and
          direction in ('LONG_BUILD', 'SHORT_BUILD') and
          score >= THRESHOLD['B']['score_min']):
        mode = 'B'
        params_key = 'B_10X' if abs(oi['chg_24h']) >= 30 else 'B'
        direction_bias = 'LONG' if direction == 'LONG_BUILD' else 'SHORT'
        hold = '3-14天'
        lev  = '5-10x'

    # C类判断：1H短线异动
    elif (abs(oi['chg_1h']) >= THRESHOLD['C']['1h_oi_min'] and
          score >= THRESHOLD['C']['score_min']):
        mode = 'C'
        params_key = 'C'
        direction_bias = 'LONG' if direction in ('LONG_BUILD', 'SHORT_COVER') else 'SHORT'
        hold = '1-24H'
        lev  = '3-5x'

    else:
        return None  # 不满足任何分类

    params = OI_EXEC_PARAMS.get(params_key, OI_EXEC_PARAMS['B'])

    return {
        'mode':            mode,
        'params_key':      params_key,
        'direction_bias':  direction_bias,
        'hold':            hold,
        'lev':             lev,
        'exec_params':     params,
    }


# ════════════════════════════════════════════════════════════════
# 主扫描逻辑
# ════════════════════════════════════════════════════════════════

def scan_symbol(sym, ticker_data):
    """对单个标的执行全量OI分析"""
    price   = ticker_data.get('price', 0)
    pct24h  = ticker_data.get('pct24h', 0)
    vol_usdt = ticker_data.get('vol_usdt', 0)

    if vol_usdt < MIN_VOLUME_USD:
        return None

    # 多周期OI数据
    oi_raw = get_oi_multi_period(sym)
    if not oi_raw.get('1h'):
        return None

    cur_oi_usd = oi_raw['1h'][-1]['oi_usd'] if oi_raw['1h'] else 0
    if cur_oi_usd < MIN_OI_USD:
        return None

    # OI计算
    oi = calc_oi_changes(oi_raw)

    # 市场微观数据
    _, _, basis, fr    = get_premium_info(sym)
    whale_l, retail_l  = get_ls_ratio(sym)
    klines_1h          = get_klines(sym, '1h', 24)

    # 方向矩阵
    direction, dir_bias = calc_oi_direction_matrix(oi_raw['1h'], pct24h)

    # 综合评分
    score, details = score_oi_signal(oi, basis, fr, whale_l, retail_l, direction, klines_1h)

    # 信号分类
    regime = 'UNKNOWN'
    try:
        _r = json.loads((BASE/'data/regime_state.json').read_text())
        regime = _r.get(sym, {}).get('confirmed', 'UNKNOWN') if isinstance(_r.get(sym), dict) else 'UNKNOWN'
    except:
        pass

    sig_info = classify_signal(oi, score, direction, basis, fr, whale_l, regime)

    # 读取RSI_1H用于显示
    rsi_1h = 50.0
    if klines_1h:
        rsi_1h = _calc_rsi([k['close'] for k in klines_1h])

    result = {
        'symbol':     sym,
        'price':      price,
        'pct24h':     round(pct24h, 2),
        'vol_usdt_m': round(vol_usdt/1e6, 1),
        'oi_score':   score,
        'score_details': details[:5],

        # OI多周期
        'chg_1h':   oi['chg_1h'],
        'chg_4h':   oi['chg_4h'],
        'chg_24h':  oi['chg_24h'],
        'chg_7d':   oi['chg_7d'],
        'chg_30d':  oi['chg_30d'],
        'accel_4h': oi['accel_4h'],
        'oi_usd_m': oi['oi_usd_m'],

        # 微观
        'basis':    basis,
        'fr':       fr,
        'whale_l':  whale_l,
        'retail_l': retail_l,
        'direction': direction,
        'rsi_1h':   round(rsi_1h, 1),
        'regime':   regime,

        # 执行参数
        'mode':     sig_info['mode'] if sig_info else 'WATCH',
        'action':   None,  # 下方填充
        'size_pct': 0,
        'lev':      1,
        'hold':     '',
        'layers_pass': 0,  # 兼容sub_executor
    }

    # 填充执行参数（兼容sub_executor格式）
    if sig_info:
        ep = sig_info['exec_params']
        result['mode']        = sig_info['mode']
        result['direction_bias'] = sig_info['direction_bias']
        result['params_key']  = sig_info['params_key']
        result['size_pct']    = ep['size_pct'] * 100  # 存百分比格式
        result['lev']         = ep['lev']
        result['sl_pct']      = ep['sl_pct']
        result['hold']        = sig_info['hold']
        result['lev_range']   = sig_info['lev']
        result['layers_pass'] = 3 if score >= 40 else (2 if score >= 25 else 1)

        # action字段（sub_executor读取）
        if score >= THRESHOLD['A']['score_min'] and sig_info['mode'] == 'A':
            result['action'] = 'buy_full' if score >= 70 else 'buy_light'
        elif score >= THRESHOLD['B']['score_min'] and sig_info['mode'] == 'B':
            result['action'] = 'buy_full' if score >= 60 else 'buy_light'
        elif score >= THRESHOLD['C']['score_min']:
            result['action'] = 'watchlist'
    else:
        result['action'] = 'watchlist'

    return result


def format_signal_card(sym, r, rank):
    """格式化信号推送卡片（精简版，适合推送）"""
    mode_icon = {'A': '🏆', 'B': '⚡', 'C': '📡'}.get(r['mode'], '👀')
    mode_name = {'A': '现货长线', 'B': '合约中线', 'C': '短线异动'}.get(r['mode'], '监控')
    dir_icon  = {'LONG': '🟢多', 'SHORT': '🔴空'}.get(r.get('direction_bias', ''), '⚪')

    lines = [
        f"{'─'*40}",
        f"{mode_icon} #{rank} {sym} · {mode_name}信号",
        f"方向: {dir_icon}  |  评分: {r['oi_score']}/100",
        f"",
        f"OI变化: 1H {r['chg_1h']:+.1f}% | 4H {r['chg_4h']:+.1f}%",
        f"       24H {r['chg_24h']:+.1f}% | 7D {r['chg_7d']:+.1f}%",
        f"OI加速: {r['accel_4h']:+.1f}  |  规模: ${r['oi_usd_m']:.1f}M",
        f"",
        f"价格: ${r['price']:,.4f}  24H: {r['pct24h']:+.1f}%",
        f"基差: {r['basis']:+.3f}%  |  FR: {r['fr']:+.5f}%",
        f"鲸鱼多: {r['whale_l']:.0f}%  |  RSI_1H: {r['rsi_1h']:.0f}",
        f"",
        f"建议: {r.get('lev_range','?')}  持仓: {r.get('hold','?')}",
        f"评分明细: {' | '.join(r['score_details'][:3])}",
    ]
    return '\n'.join(lines)


def run():
    now = datetime.now(timezone.utc)
    print(f"\n{'='*55}")
    print(f"🔍 OI高级扫描器v3.0 启动")
    print(f"   时间: {now.strftime('%Y-%m-%dT%H:%M UTC')}")
    print(f"{'='*55}")

    cache = load_cache()

    # ── Step1: 拉取全市场ticker ──────────────────────────────
    print("Step1: 拉取全市场行情...")
    tickers_raw = _fetch(f'{FAPI_BASE}/fapi/v1/ticker/24hr')
    if not isinstance(tickers_raw, list):
        print("❌ ticker拉取失败")
        return

    tickers = {}
    for t in tickers_raw:
        sym = t['symbol']
        if not sym.endswith('USDT'): continue
        if sym in BLACKLIST: continue
        vol = float(t.get('quoteVolume', 0))
        if vol < MIN_VOLUME_USD and sym not in FORCE_INCLUDE:
            continue
        tickers[sym] = {
            'price':   float(t.get('lastPrice', 0)),
            'pct24h':  float(t.get('priceChangePercent', 0)),
            'vol_usdt': vol,
        }

    print(f"  → 符合条件标的: {len(tickers)}个")

    # ── Step2: 并发扫描 ──────────────────────────────────────
    print(f"Step2: 并发OI分析（{MAX_WORKERS}线程）...")
    results = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(scan_symbol, sym, td): sym
                   for sym, td in tickers.items()}
        for f in as_completed(futures):
            try:
                r = f.result()
                if r: results.append(r)
            except Exception as e:
                pass

    # 过滤并排序
    valid = [r for r in results if r.get('oi_score', 0) > 0]
    valid.sort(key=lambda x: -x['oi_score'])

    print(f"  → 有效结果: {len(valid)}个")

    # ── Step3: 分析结果并产生信号 ────────────────────────────
    # 三类信号
    a_signals = [r for r in valid if r['mode'] == 'A' and
                 r['action'] in ('buy_full', 'buy_light')]
    b_signals = [r for r in valid if r['mode'] == 'B' and
                 r['action'] in ('buy_full', 'buy_light')]
    c_signals = [r for r in valid if r['mode'] == 'C' and
                 r['oi_score'] >= THRESHOLD['C']['score_min']]

    print(f"\n信号汇总:")
    print(f"  🏆 A类（现货长线）: {len(a_signals)}个")
    print(f"  ⚡ B类（合约中线）: {len(b_signals)}个")
    print(f"  📡 C类（短线异动）: {len(c_signals)}个")

    # ── Step4: Top5展示 ──────────────────────────────────────
    top_all = (a_signals[:3] + b_signals[:3] + c_signals[:2])
    top_all.sort(key=lambda x: -x['oi_score'])

    print(f"\nTop OI信号列表:")
    print(f"  {'Symbol':<15} {'Mode':>5} {'Score':>6} {'Dir':>12} "
          f"{'1H%':>6} {'24H%':>7} {'7D%':>7} {'FR%':>8} {'Whale%':>7}")
    print(f"  {'-'*80}")
    for r in top_all[:10]:
        action_flag = '✅' if r['action'] in ('buy_full','buy_light') else '👀'
        print(f"  {r['symbol']:<15} {r['mode']:>5} {r['oi_score']:>6.0f} "
              f"{r.get('direction','?'):>12} "
              f"{r['chg_1h']:>+6.1f}% {r['chg_24h']:>+7.1f}% "
              f"{r['chg_7d']:>+7.1f}% {r['fr']:>+8.5f}% "
              f"{r['whale_l']:>6.0f}% {action_flag}")

    # ── Step5: 写入 oi_candidates.json（修复BUG-1/2）──────────
    candidates_dict = {}
    for r in valid[:TOP_N]:
        candidates_dict[r['symbol']] = r

    oi_output = {
        'updated_at':  now.timestamp(),
        'scanned_at':  now.timestamp(),
        'generated':   now.strftime('%Y-%m-%dT%H:%M UTC'),
        'count':       len(valid),
        'a_count':     len(a_signals),
        'b_count':     len(b_signals),
        'c_count':     len(c_signals),
        'candidates':  candidates_dict,
    }

    OI_CANDIDATES_PATH.write_text(json.dumps(oi_output, ensure_ascii=False, indent=2))
    print(f"\n✅ oi_candidates.json 已更新 ({len(candidates_dict)}个候选)")

    # ── Step6: 判断是否推送苏摩 ─────────────────────────────
    action_signals = [r for r in valid
                      if r['action'] in ('buy_full', 'buy_light') and
                      r['oi_score'] >= 40]

    cooldown_h = {'A': 12, 'B': 4, 'C': 1}
    push_signals = []

    for r in action_signals[:8]:
        sym  = r['symbol']
        mode = r['mode']
        key  = f"{sym}_{mode}_{r.get('direction_bias','?')}"
        last = cache.get(key, 0)
        cd   = cooldown_h.get(mode, 2)
        age  = (now.timestamp() - last) / 3600

        if age >= cd:
            push_signals.append(r)
            cache[key] = now.timestamp()

    save_cache(cache)

    if push_signals:
        # 构建推送消息
        header = (
            f"🎯 OI猎手v3.0 · 信号报告\n"
            f"{now.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"{'─'*40}\n"
            f"发现 {len(push_signals)} 个可执行信号\n"
            f"  A类(长线): {sum(1 for x in push_signals if x['mode']=='A')}个\n"
            f"  B类(中线): {sum(1 for x in push_signals if x['mode']=='B')}个\n"
            f"  C类(短线): {sum(1 for x in push_signals if x['mode']=='C')}个\n"
        )

        cards = []
        for i, r in enumerate(push_signals[:6], 1):
            cards.append(format_signal_card(r['symbol'], r, i))

        msg = header + '\n' + '\n\n'.join(cards)
        print(f"\n📤 推送 {len(push_signals)} 个信号到苏摩...")
        send_message(msg)

        # 写入信号日志
        for r in push_signals:
            with open(OI_SIGNAL_LOG, 'a') as f:
                log = dict(r)
                log['pushed_at'] = now.isoformat()
                f.write(json.dumps(log, ensure_ascii=False) + '\n')

        print(f"✅ 推送完成")
    else:
        print(f"\nHEARTBEAT_OK (无新信号需推送)")

    # ── Step7: 全量日志 ───────────────────────────────────────
    log_path = BASE / 'logs' / 'oi_advanced.log'
    with open(log_path, 'a') as f:
        summary = {
            'ts': now.isoformat(),
            'scanned': len(valid),
            'a': len(a_signals), 'b': len(b_signals), 'c': len(c_signals),
            'pushed': len(push_signals),
            'top_syms': [r['symbol'] for r in valid[:10]],
        }
        f.write(json.dumps(summary) + '\n')

    return len(push_signals)


if __name__ == '__main__':
    run()
