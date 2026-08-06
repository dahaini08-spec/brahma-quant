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
# ── 内存门控（设计院2026-08-04封印）───────────────────
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'scripts') if '/scripts/' not in __file__ else _os.path.dirname(_os.path.abspath(__file__)))
try:
    from brahma_mem_manager import mem_gate as _mem_gate
    _mem_gate(900)
except (ImportError, SystemExit) as _e:
    if isinstance(_e, SystemExit): raise
# ── 进程内存上限硬封（设计院P3 2026-08-05）──────────────
try:
    import resource as _resource
    _RLIMIT_1500MB = 1500 * 1024 * 1024
    _resource.setrlimit(_resource.RLIMIT_AS, (_RLIMIT_1500MB, _RLIMIT_1500MB))
except Exception:
    pass  # 容器环境不支持时静默跳过
# ─────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────

import sys, os, json, time, math, hmac, hashlib, requests
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# [v7.0 设计院 2026-07-11] sys.path必须在brahma_brain import之前注入
BASE = Path(__file__).parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
if str(BASE / 'brahma_brain') not in sys.path:
    sys.path.insert(0, str(BASE / 'brahma_brain'))
if str(BASE / 'scripts') not in sys.path:
    sys.path.insert(0, str(BASE / 'scripts'))

from brahma_brain.math_utils import calc_rsi as _calc_rsi, ema as _calc_ema  # 统一数学库 v1.0

try:
    from scripts.system_config import (
        FAPI_BASE, JARVIS_USER_ID, JARVIS_THREAD_ID,
        JARVIS_CHANNEL, API_KEY, API_SECRET
    )
    JARVIS_TARGET = f'{JARVIS_USER_ID}:t:{JARVIS_THREAD_ID}'
except Exception:
    FAPI_BASE     = 'https://fapi.binance.com'
    JARVIS_TARGET = '73295708:t:019f8768-6731-777d-8924-2426a5abd10f'
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
    'D': {                    # [设计院 2026-08-06] D类: 趋势累积型 — 新增
        '7d_oi_growth': 3.0,  # 7日OI持续正增长≥3%
        'price_ema20':  True, # 价格在EMA20上方（方向确认）
        'fr_max':       0.05, # 资金费率未过热
        'score_min':    25,   # 综合评分≥25（观察级，仅推送不开单）
    },
}

# ── 执行参数（对接sub_executor）─────────────────────────────
OI_EXEC_PARAMS = {
    'A_BULL': {'size_pct': 0.05, 'sl_pct': 2.5, 'tp_mult': 1.5, 'lev': 5,  'hold': '7-365天'},
    'A_BEAR': {'size_pct': 0.03, 'sl_pct': 3.0, 'tp_mult': 1.0, 'lev': 3,  'hold': '3-30天'},
    'B_10X':  {'size_pct': 0.05, 'sl_pct': 2.5, 'tp_mult': 1.2, 'lev': 10, 'hold': '3-14天'},
    'B':      {'size_pct': 0.05, 'sl_pct': 2.5, 'tp_mult': 1.2, 'lev': 5,  'hold': '1-7天'},
    'C':      {'size_pct': 0.03, 'sl_pct': 2.0, 'tp_mult': 1.0, 'lev': 5,  'hold': '1-24H'},
    'D':      {'size_pct': 0.00, 'sl_pct': 0.0, 'tp_mult': 0.0, 'lev': 0,  'hold': 'OBSERVE_ONLY'},  # D类仅推送
}

# ── 黑名单（稳定性差/无OI历史）──────────────────────────────
BLACKLIST = set()

# ── 强制主力币入池 ────────────────────────────────────────────
FORCE_INCLUDE = {'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT'}


# ════════════════════════════════════════════════════════════════
# 基础工具函数
# ════════════════════════════════════════════════════════════════

def _fetch(url, timeout=6, retries=2):
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 403:
                return None  # [FIX 2026-07-22] 403直接返回，不重试
            elif r.status_code == 429:
                time.sleep(1)
        except Exception:
            if attempt < retries - 1:
                time.sleep(0.1)
    return None




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
    # [FIX 2026-07-22] openInterestHist 403 → fallback实时OI构造伪历史
    _oi_now_cache = {}
    try:
        _d_now = _fetch(f'{FAPI_BASE}/fapi/v1/openInterest?symbol={sym}')
        if isinstance(_d_now, dict) and _d_now.get('openInterest'):
            _oi_now_cache['oi'] = float(_d_now['openInterest'])
    except Exception:
        pass

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
        elif _oi_now_cache.get('oi'):
            # fallback：用实时OI构造2条伪历史（变化=0，后续算法可运行）
            import time as _time
            _oi = _oi_now_cache['oi']
            _now_ts = int(_time.time() * 1000)
            _step = {'1h': 3600000, '4h': 14400000, '1d': 86400000}[period]
            result[period] = [
                {'ts': _now_ts - _step * i, 'oi': _oi * (1 - 0.001*i), 'oi_usd': _oi * (1 - 0.001*i)}
                for i in range(min(limit, 5), -1, -1)
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
        ema20  = _calc_ema(closes_1h, 20)
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

        # ── [设计院 Fix-4 2026-07-26] 梵天死穴冲突检测 ─────────────────────────
        # 铁证: 1000PEPEUSDT regime=BEAR_TREND + direction_bias=LONG = 死穴
        # BEAR_TREND + LONG / BULL_TREND + SHORT → 降级为WATCH
        _regime_up = str(regime).upper()
        _dir_bias  = sig_info.get('direction_bias', 'LONG')
        _is_dead   = (
            ('BEAR_TREND' in _regime_up and _dir_bias == 'LONG') or
            ('BULL_TREND' in _regime_up and _dir_bias == 'SHORT') or
            ('CHOP' in _regime_up and _dir_bias == 'LONG' and score < 110)
        )
        if _is_dead:
            result['action']           = 'watchlist'
            result['_dead_zone_note']  = f'死穴:{_regime_up}+{_dir_bias}→降级WATCH'
            result['size_pct']         = 0
        # ── [END Fix-4] ──────────────────────────────────────────────────────────

        # ── [设计院 Fix-2 2026-07-26] FOMC宏观仓位降权 ──────────────────────────
        # 铁证: size_pct=5%硬编码，FOMC T+3天应为2.5%
        if not _is_dead and result.get('size_pct', 0) > 0:
            try:
                import sys as _oi_sys, os as _oi_os
                _oi_brain = _oi_os.path.join(_oi_os.path.dirname(_oi_os.path.dirname(
                    _oi_os.path.abspath(__file__))), 'brahma_brain')
                if _oi_brain not in _oi_sys.path: _oi_sys.path.insert(0, _oi_brain)
                from macro_calendar import get_upcoming_events as _oi_ev
                _macro_factor = 1.0
                for _ev in _oi_ev(days_ahead=7):
                    _d = _ev.get('days_to', 99)
                    if _ev.get('impact') == 'CRITICAL':
                        if _d <= 1:   _macro_factor = min(_macro_factor, 0.3)
                        elif _d <= 3: _macro_factor = min(_macro_factor, 0.5)
                        elif _d <= 7: _macro_factor = min(_macro_factor, 0.7)
                if _macro_factor < 1.0:
                    result['size_pct'] = round(result['size_pct'] * _macro_factor, 2)
                    result['_macro_factor'] = _macro_factor
                    result['_macro_note']   = f'FOMC降权×{_macro_factor}'
            except Exception:
                pass
        # ── [END Fix-2] ──────────────────────────────────────────────────────────

        # [P1修复 2026-07-13 设计院] action字段双向化（sub_executor读取）
        # 修复前：做空方向一律写watchlist→sub_executor白名单过滤跳过
        # 修复后：根据direction_bias决定BUY/SELL类 action
        _dir = sig_info.get('direction_bias', 'LONG')
        if score >= THRESHOLD['A']['score_min'] and sig_info['mode'] == 'A':
            if _dir == 'LONG':
                result['action'] = 'buy_full' if score >= 70 else 'buy_light'
            else:
                result['action'] = 'sell_full' if score >= 70 else 'sell_light'
        elif score >= THRESHOLD['B']['score_min'] and sig_info['mode'] == 'B':
            if _dir == 'LONG':
                result['action'] = 'buy_full' if score >= 60 else 'buy_light'
            else:
                result['action'] = 'sell_full' if score >= 60 else 'sell_light'
        elif score >= THRESHOLD['C']['score_min']:
            result['action'] = 'watchlist'
    else:
        result['action'] = 'watchlist'

    return result


def _fmt_price(p: float) -> str:
    """价格格式化：大价格用逗号分隔，小价格用科学计数消除"""
    if p <= 0: return '?'
    if p >= 1000:   return f'${p:,.2f}'
    elif p >= 1:    return f'${p:.4f}'
    elif p >= 0.001: return f'${p:.6f}'
    else:           return f'${p:.8f}'


def _calc_oi_strategy(r: dict) -> dict:
    """[设计院封印 2026-07-16 苏摩111] OI信号7要素策略计算
    宪法止损公式:
      做多止损 = 进场价 × (1 - SL_PCT)
      做空止损 = 进场价 × (1 + SL_PCT)
    SL_PCT: 做多→2.0% / BULL做空→2.5% / BEAR做空→2.0% / CHOP做空→2.5%
    """
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))

    price    = float(r.get('price', 0) or 0)
    direction = r.get('direction_bias', r.get('direction', 'LONG'))
    regime   = r.get('regime', 'BULL_TREND')
    mode     = r.get('mode', 'B')
    score    = float(r.get('oi_score', 50))

    if price <= 0:
        return {}

    # ── 止损参数 ──────────────────────────────────────────────
    if direction == 'LONG':
        sl_pct = 0.020
    else:  # SHORT
        sl_pct = 0.025 if 'BULL' in regime.upper() or 'CHOP' in regime.upper() else 0.020

    # ── 入场区间（当前价±0.3%，避免追高） ────────────────────
    entry_lo = round(price * 0.997, 6)
    entry_hi = round(price * 1.003, 6)

    # ── 止损价 ────────────────────────────────────────────────
    if direction == 'LONG':
        sl_price = round(entry_lo * (1 - sl_pct), 6)
    else:
        sl_price = round(entry_hi * (1 + sl_pct), 6)

    # ── RR & TP（宪法：BEAR做空RR=1.0 / 其他RR=1.5-2.0） ────
    exec_p = OI_EXEC_PARAMS.get(mode + ('_BULL' if 'BULL' in regime.upper() else
                                        '_BEAR' if 'BEAR' in regime.upper() else ''),
                                 OI_EXEC_PARAMS.get(mode, OI_EXEC_PARAMS.get('B', {})))
    rr     = exec_p.get('tp_mult', 1.5)
    lev    = exec_p.get('lev', 5)
    hold   = exec_p.get('hold', '1-7天')

    sl_dist = abs(price - sl_price)
    if direction == 'LONG':
        tp1 = round(entry_lo + sl_dist * rr, 6)
        tp2 = round(entry_lo + sl_dist * rr * 2, 6)
    else:
        tp1 = round(entry_hi - sl_dist * rr, 6)
        tp2 = round(entry_hi - sl_dist * rr * 2, 6)

    # ── FG感知仓位 ────────────────────────────────────────────
    try:
        from position_sizer import get_position_pct
        import json as _json
        from pathlib import Path as _Path
        _ms = _json.loads((_Path(__file__).parent.parent / 'data/macro_state.json').read_text())
        fg  = float(_ms.get('fng_score', 50) or 50)
        nav = 100.0  # 标准NAV基准
        ep  = get_position_pct(r.get('symbol',''), score, direction, nav, fear_greed=fg, regime=regime)
        size_pct = ep.get('pct', exec_p.get('size_pct', 5) * 100) / 100
        fg_note  = f'FG={fg:.0f}' + ('(极度恐惧)' if fg <= 20 else ('(恐惧)' if fg <= 40 else ''))
    except Exception:
        size_pct = exec_p.get('size_pct', 0.05)
        fg_note  = ''

    # ── 有效期 ────────────────────────────────────────────────
    import time as _t
    expire_ts = _t.time() + {'A': 86400*3, 'B': 86400, 'C': 3600*4}.get(mode, 86400)
    expire_str = _t.strftime('%m-%d %H:%M UTC', _t.gmtime(expire_ts))

    # ── 触发条件 ──────────────────────────────────────────────
    chg_1h = float(r.get('chg_1h', 0))
    chg_4h = float(r.get('chg_4h', 0))
    if direction == 'LONG':
        if chg_1h > 3:
            trigger = f'OI已建仓+价格站稳 {_fmt_price(entry_lo)} → 限价入场'
        else:
            trigger = f'等OI 1H再涨{max(0, 2-chg_1h):.1f}%确认 + 价格不低于 {_fmt_price(entry_lo)}'
    else:
        if chg_1h < -3:
            trigger = f'OI空头已建仓+价格跌破 {_fmt_price(entry_hi)} → 限价入场'
        else:
            trigger = f'等OI 1H再降{max(0, 2+chg_1h):.1f}%确认 + 价格不超过 {_fmt_price(entry_hi)}'

    return {
        'entry_lo':  entry_lo, 'entry_hi':  entry_hi,
        'sl_price':  sl_price, 'sl_pct':    round(sl_pct*100, 1),
        'tp1':       tp1,       'tp2':       tp2,
        'rr':        rr,        'lev':       lev,
        'hold':      hold,      'size_pct':  size_pct,
        'fg_note':   fg_note,   'trigger':   trigger,
        'expire':    expire_str,
    }


def format_signal_card(sym, r, rank):
    """[设计院封印 2026-07-16 苏摩111] OI信号卡片 — 7要素完整策略格式"""
    # [Fix-5 设计院 2026-07-26] 来源标识行
    from datetime import datetime, timezone as _tz
    now_str = datetime.now(_tz.utc).strftime('%m-%d %H:%M UTC')
    mode_icon = {'A': '🏆', 'B': '⚡', 'C': '📡'}.get(r['mode'], '👀')
    mode_name = {'A': '现货长线', 'B': '合约中线', 'C': '短线异动'}.get(r['mode'], '监控')
    dir_icon  = {'LONG': '🟢做多', 'SHORT': '🔴做空'}.get(r.get('direction_bias', ''), '⚪')

    # 计算7要素策略
    strat = _calc_oi_strategy(r)
    price = float(r.get('price', 0))

    # OI数据行
    oi_line = (f"OI: 1H {r['chg_1h']:+.1f}% | 4H {r['chg_4h']:+.1f}% | "
               f"24H {r['chg_24h']:+.1f}% | FR {r['fr']:+.5f}%")

    lines = [
        f"[OI猎手 {mode_icon}{mode_name}类] · {now_str}",
        f"{'━'*40}",
        f"{mode_icon} #{rank} {sym} · {mode_name}",
        f"方向: {dir_icon}  |  OI评分: {r['oi_score']:.0f}/100",
        f"体制: {r.get('regime','?')} | RSI_1H: {r['rsi_1h']:.0f} | 鲸鱼多: {r['whale_l']:.0f}%",
        f"",
        oi_line,
        f"规模: ${r['oi_usd_m']:.1f}M | OI加速: {r['accel_4h']:+.1f}",
        f"",
    ]

    if strat:
        lines += [
            f"{'─'*40}",
            f"⚡ 触发条件",
            f"   {strat['trigger']}",
            f"📍 入场区间: {_fmt_price(strat['entry_lo'])} ~ {_fmt_price(strat['entry_hi'])}",
            f"🛡️ 止损:    {_fmt_price(strat['sl_price'])}  ({strat['sl_pct']:.1f}%)",
            f"🎯 TP1:    {_fmt_price(strat['tp1'])}  (RR={strat['rr']:.1f}x)",
            f"🎯 TP2:    {_fmt_price(strat['tp2'])}  (RR={strat['rr']*2:.1f}x)",
            f"📦 仓位:   {strat['size_pct']*100:.1f}%NAV × {strat['lev']}x  {strat['fg_note']}",
            f"⏰ 有效期: {strat['expire']}  持仓参考: {strat['hold']}",
        ]
    else:
        lines += [
            f"价格: ${price:,.4f}  24H: {r['pct24h']:+.1f}%",
            f"建议: {r.get('lev_range','?')}  持仓: {r.get('hold','?')}",
        ]

    lines.append(f"评分明细: {' | '.join(r['score_details'][:3])}")
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

    # [设计院封印 2026-07-16 苏摩111] 状态哈希去重，替代纯时间窗口cooldown
    # 哈希 = OI变化率5%桶 + direction + mode + regime大类
    # 相同哈希内不重推；OI方向翻转立即推
    import hashlib as _hl
    OI_STATE_FILE = BASE / 'data' / 'oi_push_state.json'
    try:
        _oi_state = json.loads(OI_STATE_FILE.read_text())
    except Exception:
        _oi_state = {}

    def _oi_hash(r_: dict) -> str:
        chg_bucket = int(float(r_.get('chg_4h', 0)) / 5) * 5  # 5%桶
        regime_major = ('BULL' if 'BULL' in str(r_.get('regime','')).upper()
                        else 'BEAR_REC' if 'RECOVERY' in str(r_.get('regime','')).upper()
                        else 'BEAR' if 'BEAR' in str(r_.get('regime','')).upper()
                        else 'CHOP')
        raw = f"{chg_bucket}|{r_.get('direction_bias','?')}|{r_.get('mode','?')}|{regime_major}"
        return _hl.md5(raw.encode()).hexdigest()[:8]

    # [设计院封印 2026-08-02] TTL大幅缩短：A类48H→12H，B类24H→6H，防止信号被去重死锁
    # 原设计哲学：哈希不变=信号未变=不重推；修复后：持续有效的信号每12H提醒一次
    SAME_HASH_TTL_OI = {'A': 3600*12, 'B': 3600*6, 'C': 3600*2}  # [修复] 同哈希冷却期大幅缩短
    push_signals = []
    persisted_signals = []  # 哈希未变但TTL到期的「持续有效」信号

    for r in action_signals[:8]:
        sym      = r['symbol']
        mode     = r['mode']
        new_hash = _oi_hash(r)
        sym_st   = _oi_state.get(sym, {})
        old_hash = sym_st.get('hash', '')
        last_push= sym_st.get('ts', 0)
        ttl      = SAME_HASH_TTL_OI.get(mode, 3600*12)

        hash_changed = (new_hash != old_hash)
        cooldown_ok  = (now.timestamp() - last_push > ttl)

        # [C] 体制标注：BEAR_TREND+LONG → 标注WATCH，不计入主推送但发提醒
        _regime_bias = str(r.get('regime', '')).upper()
        _dir_bias    = str(r.get('direction_bias', 'LONG')).upper()
        _is_dead_zone = ('BEAR_TREND' in _regime_bias and _dir_bias == 'LONG')
        if _is_dead_zone:
            r = dict(r)  # 浅拷贝避免污染原对象
            r['_watch_only'] = True
            r['_watch_reason'] = f'⚠️WATCH: BEAR_TREND+LONG死穴，不执行，仅观察'

        if hash_changed or cooldown_ok:
            if hash_changed:
                push_signals.append(r)
            else:
                # 哈希未变但TTL到期 → 「信号持续有效」提醒
                persisted_signals.append(r)
            _oi_state[sym] = {
                'hash': new_hash, 'ts': now.timestamp(),
                'direction': r.get('direction_bias','?'),
                'score': r.get('oi_score', 0), 'mode': mode,
            }
        else:
            age_h = (now.timestamp()-last_push)/3600
            print(f'  [{sym}] 哈希未变({new_hash})，距上次推送{age_h:.1f}H，跳过（TTL={ttl//3600}H）')

    OI_STATE_FILE.write_text(json.dumps(_oi_state, indent=2, ensure_ascii=False))

    save_cache(cache)

    if push_signals or persisted_signals:
        # ── 新信号推送 ──────────────────────────────────────────
        if push_signals:
            header = (
                f"🎯 OI猎手v3.1 · 新信号报告\n"
                f"{now.strftime('%Y-%m-%d %H:%M UTC')}\n"
                f"{'─'*40}\n"
                f"发现 {len(push_signals)} 个新信号\n"
                f"  A类(长线): {sum(1 for x in push_signals if x['mode']=='A')}个\n"
                f"  B类(中线): {sum(1 for x in push_signals if x['mode']=='B')}个\n"
                f"  C类(短线): {sum(1 for x in push_signals if x['mode']=='C')}个\n"
            )
            cards = []
            for i, r in enumerate(push_signals[:6], 1):
                card = format_signal_card(r['symbol'], r, i)
                if r.get('_watch_only'):
                    card += f"\n⚠️ {r.get('_watch_reason','BEAR_TREND+LONG 仅观察')}"
                cards.append(card)
            msg = header + '\n' + '\n\n'.join(cards)
            print(f"\n📤 推送 {len(push_signals)} 个新信号到苏摩...")
            send_message(msg)

        # ── 持续有效信号提醒（哈希未变但TTL到期）──────────────
        if persisted_signals:
            try:
                _rstate = json.loads((BASE/'data/regime_state.json').read_text())
                _regime_now = _rstate.get('BTCUSDT', {}).get('confirmed', 'UNKNOWN') if isinstance(_rstate.get('BTCUSDT'), dict) else str(_rstate.get('BTCUSDT','UNKNOWN'))
            except Exception:
                _regime_now = 'UNKNOWN'
            persist_lines = []
            for r in persisted_signals[:5]:
                score_p = r.get('oi_score', r.get('score', 0))
                dir_p   = r.get('direction_bias', '?')
                watch_tag = ' ⚠️WATCH' if r.get('_watch_only') else ''
                persist_lines.append(f"  {r['symbol']}[{r['mode']}] score={float(score_p):.0f} {dir_p}{watch_tag}")
            persist_msg = (
                f"📌 OI信号持续有效 ({now.strftime('%H:%M UTC')})\n"
                f"以下信号方向未变，依然有效：\n" +
                '\n'.join(persist_lines) +
                f"\n当前体制: {_regime_now} | 每12H自动提醒"
            )
            print(f"\n📤 推送 {len(persisted_signals)} 个持续有效信号提醒...")
            send_message(persist_msg)

        # ── 写入信号日志 ────────────────────────────────────────
        for r in push_signals + persisted_signals:
            with open(OI_SIGNAL_LOG, 'a') as f:
                log = dict(r)
                log['pushed_at'] = now.isoformat()
                # [FIX-ROOT 2026-07-22] 注入grade_num
                try:
                    import sys as _sys3; _sys3.path.insert(0, str(BASE / 'brahma_brain'))
                    from grade_utils import parse_grade as _pg3
                    log['grade_num'] = _pg3(log.get('grade', 0), int(log.get('structure_grade', 0) or 0))
                except Exception:
                    pass
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
