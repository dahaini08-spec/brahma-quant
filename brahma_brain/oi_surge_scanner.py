#!/usr/bin/env python3
"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# OI突增扫描器
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
oi_surge_scanner.py · OI持仓量驱动拉升猎手 v1.0
[设计院 × 达摩院 · 2026-06-30 · 苏摩授权]

核心逻辑：
  OI上升≠必涨，必须用大户方向+资金费率双重验证，区分：
    ① 空头建仓（OI↑ + 大户L%↓ + 资金费率极负）→ 排除
    ② 聪明钱潜伏（OI↑ + 大户L%↑ + 资金费率中性）→ 入选
    ③ 双向对冲（OI↑ + 大户L%~50% + 资金费率~0）→ 低优先级

三种模式：
  模式A: 大周期蓄能（15天OI持续增长 + 价格未拉升）
  模式B: 中期蓄力（3天OI明显增加 + 价格未大幅拉升）
  模式C: 短期异动（6H OI快速积累 + 价格平稳）

五层过滤体系：
  L1: OI结构过滤（增幅+连续性+规模）
  L2: 大户方向过滤（L%≥60% 且趋势↑ 且 大户L%>散户L%）
  L3: 资金费率过滤（不能极度偏负）
  L4: 价格技术过滤（RSI+EMA位置+ATR）
  L5: 梵天体制映射（决定仓位级别）

输出：
  data/oi_candidates.json   → brahma_core.py 读取加分
  推送通道：YOUR_USER_ID:t:YOUR_THREAD_ID
"""

import os, sys, json, time, requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, 'brahma_brain'))

FAPI         = 'https://fapi.binance.com'
OUT_FILE     = os.path.join(BASE_DIR, 'data', 'oi_candidates.json')
PUSH_TARGET  = os.environ.get('JARVIS_TARGET', '73295708:thread:019f309c-609b-7a75-a195-e221e5927c63')  # SSOT v2
PUSH_CHANNEL = 'jarvis'

# ── 扫描范围 ─────────────────────────────────────────────────────
# [全市场模式 2026-07-03] 苏摩授权：动态拉取成交量Top150，覆盖全市场
def _get_dynamic_symbols(top_n: int = 150) -> list:
    """动态获取成交量前N的USDT永续合约"""
    try:
        tickers = requests.get('https://fapi.binance.com/fapi/v1/ticker/24hr', timeout=10).json()
        usdt = [(t['symbol'], float(t.get('quoteVolume', 0)))
                for t in tickers
                if t['symbol'].endswith('USDT')
                and 'UP' not in t['symbol'] and 'DOWN' not in t['symbol']
                and float(t.get('quoteVolume', 0)) > 1_000_000]  # 最低100万U/天
        usdt.sort(key=lambda x: -x[1])
        return [s for s, _ in usdt[:top_n]]
    except Exception:
        pass
    # fallback: 原固定列表
    return [
        'BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT',
        'ADAUSDT','AVAXUSDT','LINKUSDT','DOTUSDT','HYPEUSDT','ENAUSDT',
        '1000PEPEUSDT','SUIUSDT','NEARUSDT','BNBUSDT','MSTRUSDT',
    ]

SCAN_SYMBOLS = _get_dynamic_symbols(150)  # 动态，运行时更新

# ── 过滤阈值（铁证驱动，可调参） ────────────────────────────────
CFG = {
    # L1 OI结构
    'oi_chg_15d_min':   15.0,   # 模式A: 15天OI增幅下限
    'oi_chg_3d_min':     8.0,   # 模式B: 3天OI增幅下限
    'oi_chg_6h_min':     3.0,   # 模式C: 6H OI增幅下限
    'oi_cont_min':       0.45,  # 连续性下限（上升根/总根）
    'oi_usd_min':        5e6,   # 最低OI规模（防微盘）
    'cont_6h_min':       4,     # 模式C: 6H内连续上升根数

    # L2 大户方向
    'whale_l_min':      60.0,   # 大户L%最低门槛
    'whale_vs_retail':   True,  # 是否要求大户L% > 散户L%

    # L3 资金费率
    'funding_floor':   -0.005,  # 资金费率下限（更负=空头太重）
    'funding_trend_up': True,   # 是否要求近期趋势向上

    # L4 技术
    'rsi_1d_max':       65.0,   # 日线RSI上限（非超买才有空间）
    'ema_range':         0.08,  # 价格在EMA20_4H ±8% 内

    # 价格背离阈值
    'px_max_chg_15d':    5.0,   # 模式A: 价格15天涨幅上限
    'px_max_chg_3d':     8.0,   # 模式B: 价格3天涨幅上限
    'px_max_chg_6h':     3.0,   # 模式C: 价格6H涨幅上限
}


# ────────────────────────────────────────────────────────────────
# 数据获取层
# ────────────────────────────────────────────────────────────────

def _get(url, params, timeout=12):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        return r.json()
    except Exception:
        return None


def fetch_all(sym):
    """并行拉取单标的所需全部数据"""
    results = {}
    def job(key, url, params):
        results[key] = _get(url, params)

    tasks = [
        ('oi_4h',   f'{FAPI}/futures/data/openInterestHist', {'symbol':sym,'period':'4h','limit':90}),
        ('oi_1h',   f'{FAPI}/futures/data/openInterestHist', {'symbol':sym,'period':'1h','limit':72}),
        ('kl_4h',   f'{FAPI}/fapi/v1/klines',               {'symbol':sym,'interval':'4h','limit':60}),
        ('kl_1h',   f'{FAPI}/fapi/v1/klines',               {'symbol':sym,'interval':'1h','limit':72}),
        ('kl_1d',   f'{FAPI}/fapi/v1/klines',               {'symbol':sym,'interval':'1d','limit':30}),
        ('funding', f'{FAPI}/fapi/v1/fundingRate',           {'symbol':sym,'limit':8}),
        ('whale',   f'{FAPI}/futures/data/topLongShortPositionRatio',
                                                             {'symbol':sym,'period':'1h','limit':6}),
        ('retail',  f'{FAPI}/futures/data/globalLongShortAccountRatio',
                                                             {'symbol':sym,'period':'1h','limit':6}),
    ]
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(job, k, u, p): k for k, u, p in tasks}
        for f in as_completed(futs):
            pass
    return results


# ────────────────────────────────────────────────────────────────
# 工具函数
# ────────────────────────────────────────────────────────────────

def _rsi(closes, n=14):
    if len(closes) < n+1:
        return 50.0
    g, l = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        g.append(max(d, 0)); l.append(max(-d, 0))
    ag = sum(g[-n:])/n; al = sum(l[-n:])/n
    return round(100 - 100/(1 + ag/al), 1) if al > 0 else 100.0

def _ema(c, n):
    k = 2/(n+1); e = c[0]
    for v in c[1:]:
        e = v*k + e*(1-k)
    return e

def _chg(vals, start=0, end=-1):
    if not vals or len(vals) < 2:
        return 0.0
    s = vals[start]; t = vals[end] if end != -1 else vals[-1]
    return (t - s) / s * 100 if s else 0.0

def _cont(vals):
    """连续性：上升根数/总根数"""
    if len(vals) < 2:
        return 0.0
    ups = sum(1 for i in range(1, len(vals)) if vals[i] > vals[i-1])
    return ups / (len(vals) - 1)

def _accel(vals):
    """加速度：后半段斜率 - 前半段斜率"""
    if len(vals) < 4:
        return 0.0
    h = len(vals) // 2
    s1 = _chg(vals, 0, h)
    s2 = _chg(vals, h, -1)
    return s2 - s1


# ────────────────────────────────────────────────────────────────
# 核心分析：单标的五层过滤
# ────────────────────────────────────────────────────────────────

def analyze(sym):
    data = fetch_all(sym)

    # 解析数据
    oi_4h = data.get('oi_4h') or []
    oi_1h = data.get('oi_1h') or []
    kl_4h = data.get('kl_4h') or []
    kl_1h = data.get('kl_1h') or []
    kl_1d = data.get('kl_1d') or []
    funding_raw = data.get('funding') or []
    whale_raw   = data.get('whale')   or []
    retail_raw  = data.get('retail')  or []

    if (not oi_4h or len(oi_4h) < 20 or
        not oi_1h or len(oi_1h) < 24 or
        not kl_4h or len(kl_4h) < 20):
        return None

    # OI时序
    oi_v4h = [float(x['sumOpenInterest']) for x in oi_4h]
    oi_v1h = [float(x['sumOpenInterest']) for x in oi_1h]
    oi_usd = float(oi_4h[-1].get('sumOpenInterestValue', 0))

    # 价格时序
    c4h = [float(k[4]) for k in kl_4h]
    c1h = [float(k[4]) for k in kl_1h]
    c1d = [float(k[4]) for k in kl_1d] if kl_1d else c4h[-30:]
    price = c4h[-1]

    # 技术指标
    rsi_4h = _rsi(c4h)
    rsi_1d = _rsi(c1d)
    ema20   = _ema(c4h, 20)
    vs_ema  = (price - ema20) / ema20  # 相对EMA距离

    # 资金费率
    if isinstance(funding_raw, list) and funding_raw:
        fund_vals = [float(f['fundingRate'])*100 for f in funding_raw]
        fund_avg  = sum(fund_vals) / len(fund_vals)
        fund_last = fund_vals[-1]
        fund_trend = fund_vals[-1] - fund_vals[0]  # 正=向上
    else:
        fund_avg = fund_last = fund_trend = 0.0

    # 大户持仓
    if isinstance(whale_raw, list) and whale_raw:
        whale_l_vals = [float(x['longAccount'])*100 for x in whale_raw]
        whale_l = whale_l_vals[-1]
        whale_trend = whale_l_vals[-1] - whale_l_vals[0]
    else:
        whale_l = 50.0; whale_trend = 0.0

    # 散户持仓
    if isinstance(retail_raw, list) and retail_raw:
        retail_l = float(retail_raw[-1]['longAccount'])*100
    else:
        retail_l = 50.0

    # ── 模式检测 ──────────────────────────────────────────────
    mode = None
    oi_score = 0.0  # L1评分

    # 模式A: 15天大周期（90根4H = 15天）
    oi_chg_15d = _chg(oi_v4h)
    px_chg_15d = _chg(c4h)
    oi_cont_4h = _cont(oi_v4h)
    oi_accel   = _accel(oi_v4h)

    if (oi_chg_15d >= CFG['oi_chg_15d_min'] and
        px_chg_15d  < CFG['px_max_chg_15d'] and
        oi_cont_4h >= CFG['oi_cont_min'] and
        oi_usd      >= CFG['oi_usd_min']):
        mode = 'A'
        oi_score = oi_chg_15d - px_chg_15d

    # 模式B: 3天中期（72根1H = 3天）
    if mode is None:
        oi_chg_3d = _chg(oi_v1h)
        px_chg_3d = _chg(c1h)
        oi_accel_3d = _accel(oi_v1h)
        if (oi_chg_3d >= CFG['oi_chg_3d_min'] and
            abs(px_chg_3d) < CFG['px_max_chg_3d'] and
            oi_usd >= CFG['oi_usd_min']):
            mode = 'B'
            oi_score = oi_chg_3d - abs(px_chg_3d)
            oi_accel = oi_accel_3d

    # 模式C: 6H短期（最近6根1H）
    if mode is None:
        oi_6h = oi_v1h[-6:]
        px_6h = c1h[-6:]
        oi_chg_6h = _chg(oi_6h)
        px_chg_6h = _chg(px_6h)
        cont_6h = sum(1 for i in range(1, len(oi_6h)) if oi_6h[i] > oi_6h[i-1])
        if (oi_chg_6h >= CFG['oi_chg_6h_min'] and
            abs(px_chg_6h) < CFG['px_max_chg_6h'] and
            cont_6h >= CFG['cont_6h_min']):
            mode = 'C'
            oi_score = oi_chg_6h

    if mode is None:
        return None  # 不满足任何模式

    # ── [2026-07-06] OI Score 防溢出截断（KORUUSDT score=27590 BUG修复）──
    oi_score = max(-200.0, min(500.0, oi_score))

    # ── L2: 大户方向过滤 ──────────────────────────────────────
    l2_pass = (whale_l >= CFG['whale_l_min'] and whale_trend >= 0)
    if CFG['whale_vs_retail']:
        l2_pass = l2_pass and (whale_l > retail_l)

    # ── L3: 资金费率过滤 ──────────────────────────────────────
    l3_pass = (fund_avg >= CFG['funding_floor'])
    if CFG['funding_trend_up']:
        # 宽松：近期趋势向上或中性（不要求严格正向）
        l3_pass = l3_pass and (fund_trend >= -0.003)

    # ── L4: 技术过滤 ──────────────────────────────────────────
    l4_pass = (
        rsi_1d < CFG['rsi_1d_max'] and
        abs(vs_ema) <= CFG['ema_range']
    )

    # 通过层数
    layers_pass = [True, l2_pass, l3_pass, l4_pass]  # L1已在模式检测中通过
    n_pass = sum(layers_pass)

    # ── L5: 体制映射 ──────────────────────────────────────────
    try:
        regime_file = os.path.join(BASE_DIR, 'data', 'regime_state.json')
        with open(regime_file) as f:
            regime_data = json.load(f)
        regime = regime_data.get(sym, {}).get('confirmed', 'UNKNOWN')
    except Exception:
        regime = 'UNKNOWN'

    action_map = {
        'BEAR_TREND':    ('buy_light', 1),   # [方案A 2026-07-04] BEAR_TREND下OI异常=逼空信号，1%轻仓放行（原:watchlist封禁）
        'BEAR_EARLY':    ('buy_light', 1),   # [方案A 2026-07-04] 同上，1%轻仓
        'CHOP_MID':      ('buy_light', 3),   # SIZE=3%
        'BEAR_RECOVERY': ('buy_light', 2),   # SIZE=2%
        'BULL_TREND':    ('buy_full',  4),   # SIZE=4%
        'BULL_EARLY':    ('buy_full',  4),
    }
    action, size_pct = action_map.get(regime, ('watchlist', 0))

    # ── OI加分计算（brahma_core接入用）────────────────────────
    # 最高+18分：L1-L4全过 + 加速 + 大户强势
    oi_bonus = 0
    if n_pass >= 4:
        oi_bonus = 15  # 基础全过
        if oi_accel > 2:
            oi_bonus += 3  # OI加速+3
    elif n_pass == 3:
        oi_bonus = 10
    elif n_pass == 2:
        oi_bonus = 5
    # 体制修正
    if regime == 'BEAR_TREND':
        oi_bonus = min(oi_bonus, 5)  # BEAR_TREND下最多+5（仅轻微加分）

    return {
        'symbol':       sym,
        'mode':         mode,
        'oi_score':     round(oi_score, 2),
        'oi_accel':     round(oi_accel, 2),
        'oi_usd_m':     round(oi_usd / 1e6, 2),
        'whale_l':      round(whale_l, 1),
        'whale_trend':  round(whale_trend, 2),
        'retail_l':     round(retail_l, 1),
        'funding_avg':  round(fund_avg, 4),
        'funding_last': round(fund_last, 4),
        'rsi_4h':       rsi_4h,
        'rsi_1d':       rsi_1d,
        'vs_ema20':     round(vs_ema * 100, 2),
        'regime':       regime,
        'l2_pass':      l2_pass,
        'l3_pass':      l3_pass,
        'l4_pass':      l4_pass,
        'layers_pass':  n_pass,
        'action':       action,
        'size_pct':     size_pct,
        'oi_bonus':     oi_bonus,
        'price':        price,
        'scanned_at':   int(time.time()),
    }


# ────────────────────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────────────────────

def scan(symbols=None):
    # [全市场模式] 每次运行时动态更新扫描列表
    syms = symbols or _get_dynamic_symbols(150)
    print(f'[OI-Scanner] 扫描 {len(syms)} 个标的...')
    t0 = time.time()

    results = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(analyze, s): s for s in syms}
        for f in as_completed(futs):
            sym = futs[f]
            try:
                r = f.result()
                if r:
                    results[sym] = r
            except Exception as e:
                pass

    # 排序：全层通过 > oi_score降序
    ranked = sorted(results.values(),
                    key=lambda x: (x['layers_pass'], x['oi_score']),
                    reverse=True)

    print(f'[OI-Scanner] 完成 {time.time()-t0:.1f}s | 候选: {len(ranked)}个')
    return ranked


def format_push(candidates):
    """格式化推送内容（极简，只推有价值的信息）"""
    # 只推 L2+L3+L4 都通过的（真正的聪明钱潜伏信号）
    high_quality = [c for c in candidates if c['layers_pass'] >= 4]
    # watchlist (BEAR_TREND下)
    watchlist    = [c for c in candidates if c['layers_pass'] >= 3 and c['action'] == 'watchlist']

    if not high_quality and not watchlist:
        return None

    ts = datetime.now(timezone.utc).strftime('%m-%d %H:%M UTC')
    lines = [f'🏹 OI猎手 · {ts}']

    if high_quality:
        lines.append('\n📌 高质量信号（聪明钱潜伏，体制解封即入场）:')
        for c in high_quality[:4]:
            accel = '🔥加速' if c['oi_accel'] > 1 else ''
            lines.append(
                f"  {c['symbol']:<16} 模式{c['mode']} OI+{c['oi_score']:.1f}% {accel}\n"
                f"  大户L={c['whale_l']:.0f}% 资金费={c['funding_avg']:.4f}% RSI1D={c['rsi_1d']}\n"
                f"  体制={c['regime']} 行动={c['action']} 价格=${c['price']:.5g}"
            )

    if watchlist and not high_quality:
        lines.append('\n👁 监控池（低优先级，等待更优体制）:')
        for c in watchlist[:3]:
            lines.append(
                f"  {c['symbol']:<14} OI+{c['oi_score']:.1f}% 大户L={c['whale_l']:.0f}%↑ "
                f"RSI1D={c['rsi_1d']} → 体制切换即买"
            )

    return '\n'.join(lines)


def push_msg(msg):
    import subprocess
    try:
        subprocess.run(
            ['openclaw', 'message', 'send',
             '--channel', PUSH_CHANNEL,
             '--target',  PUSH_TARGET,
             '--message', msg],
            capture_output=True, text=True, timeout=15
        )
    except Exception:
        pass


def get_oi_bonus(symbol):
    """brahma_core调用接口：读缓存返回OI加分"""
    try:
        if not os.path.exists(OUT_FILE):
            return 0, ''
        with open(OUT_FILE) as f:
            cache = json.load(f)
        entry = cache.get('candidates', {}).get(symbol)
        if not entry:
            return 0, ''
        # 缓存超过8H不再加分
        age = time.time() - entry.get('scanned_at', 0)
        if age > 8 * 3600:
            return 0, ''
        bonus = entry.get('oi_bonus', 0)
        mode  = entry.get('mode', '?')
        score = entry.get('oi_score', 0)
        whale = entry.get('whale_l', 0)
        detail = f"OI模式{mode} +{score:.1f}% 大户L={whale:.0f}%"
        return bonus, detail
    except Exception:
        return 0, ''


# ────────────────────────────────────────────────────────────────
# 入口
# ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    candidates = scan()

    # 写缓存
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    cache = {
        'updated_at': int(time.time()),
        'count': len(candidates),
        'candidates': {c['symbol']: c for c in candidates},
    }
    with open(OUT_FILE, 'w') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    print(f'[OI-Scanner] 缓存写入 {OUT_FILE} ({len(candidates)}个候选)')

    # 打印结果
    print(f"\n{'Symbol':<16} {'Mode':>5} {'OI%':>8} {'Accel':>7} "
          f"{'WhaleL%':>8} {'Fund%':>8} {'RSI1D':>6} {'Layers':>7} {'Action':>12} {'Bonus':>6}")
    print('-' * 95)
    for c in candidates[:15]:
        print(f"{c['symbol']:<16} {c['mode']:>5} {c['oi_score']:>7.1f}% "
              f"{c['oi_accel']:>+7.1f} {c['whale_l']:>7.1f}% "
              f"{c['funding_avg']:>7.4f}% {c['rsi_1d']:>6.1f} "
              f"{c['layers_pass']:>4}/4  {c['action']:>12}  {c['oi_bonus']:>+5}分")

    # 推送（有高质量候选才推）
    msg = format_push(candidates)
    if msg:
        push_msg(msg)
        print(f'\n[OI-Scanner] ✅ 推送完成')
    else:
        print(f'\n[OI-Scanner] 无高质量信号，静默')

    # [v2.0 设计院 2026-07-03] 修复断链：buy候选写入rsi_trigger_event触发扫描链
    buy_syms = [c['symbol'] for c in candidates
                if c.get('action') in ('buy_full', 'buy_light')]
    if buy_syms:
        trigger_file = os.path.join(BASE_DIR, 'data', 'rsi_trigger_event.json')
        trigger = {
            'ts':      time.time(),
            'symbol':  buy_syms[0],
            'symbols': buy_syms[:6],
            'source':  'oi_surge_scanner',
            'events':  ['OI_BUY_SIGNAL'],
        }
        with open(trigger_file, 'w') as _tf:
            import json as _json
            _json.dump(trigger, _tf, ensure_ascii=False)
        print(f'[OI-Scanner] 🔗 触发扫描链: {buy_syms[:6]}')
        # [v2.0] 同时写入signal_bus
        try:
            import sys as _s; _s.path.insert(0, str(Path(BASE_DIR)/'scripts'))
            from signal_bus import write as _bw
            for _c in candidates:
                if _c.get('action') in ('buy_full','buy_light'):
                    _bw({'source':'oi','symbol':_c['symbol'],'direction':'LONG',
                         'score':float(_c.get('oi_bonus',10))+100,
                         'valid':True,'regime':'OI_SURGE',
                         'entry_lo':float(_c.get('price',0))*0.995,
                         'entry_hi':float(_c.get('price',0))*1.005,
                         'sl':float(_c.get('price',0))*0.95,
                         'sl_pct':5.0,'tp1':float(_c.get('price',0))*1.10,
                         'rr1':2.0,'expires_at':None})
        except Exception: pass
        # 同时写入scan_candidates让brahma_scan_guard直接识别
        cands_file = os.path.join(BASE_DIR, 'data', 'scan_candidates.json')
        try:
            with open(cands_file, 'r') as _cf:
                existing = _json.load(_cf)
        except Exception:
            existing = {'candidates': [], 'ts': 0}
        existing_syms = {c.get('symbol') for c in existing.get('candidates', [])}
        for c in candidates:
            if c.get('action') in ('buy_full', 'buy_light') and c['symbol'] not in existing_syms:
                existing['candidates'].append({'symbol': c['symbol'], 'score': c.get('oi_bonus', 10), 'source': 'oi'})
        existing['ts'] = int(time.time())
        with open(cands_file, 'w') as _cf:
            _json.dump(existing, _cf, ensure_ascii=False, indent=2)
        print(f'[OI-Scanner] 📋 scan_candidates更新: +{len(buy_syms)}个OI候选')
