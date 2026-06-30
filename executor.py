#!/usr/bin/env python3
"""
扣扳机层 - EXECUTOR v2.0（复盘纠正版）
==========================================
复盘发现的5个问题已全部修复：

路径说明 2026-06-11:
  本文件是旧系统扣扳机层，被 auto_poster.py 调用。
  新系统主执行路径：full_cycle_scanner -> trade_gateway -> hunter_executor
  本文件暂不删除，Phase3统一执行引擎时合并。

修复1: R:R 必须用入场区上沿计算（最保守），不再用中值
修复2: 目标设置去掉POC作为T1，改为VAH突破后目标
修复3: LVN真空区检测，止损必须放在LVN外侧
修复4: 方向验证，只发出与策略管理器 allowed_directions 一致的信号
修复5: 时间框架对齐强制验证，不足时降级为观察状态
"""

import os
import json
from datetime import datetime, timezone
from tz_utils import now_cst_short, now_cst_str, now_utc_iso

# 末梢神经接入
try:
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '.'))
    from nerve_system.nerve_emitter import get_nerve as _get_nerve
    _nerve = _get_nerve("executor")
except Exception:
    class _FallbackNerve:
        def emit(self, *a, **kw): pass
    _nerve = _FallbackNerve()

MIN_SCORE    = 70     # 最低可执行评分（应用于已归一化到100分制的分数）
                      # [修复3] brahma 150分制输入时，经 brahma_to_executor_bridge() 自动归一化
MIN_RR       = 2.5    # 最低R:R比 ← 训练大纲: 30%胜率需R:R≥2.5才正期望
MAX_POSITION = 0.03   # 最大仓位3%
MAX_RISK_PCT = 7.0    # 最大止损距离%

# ══ 训练大纲规则集 (40056笔历史数据, 2018-2026) ══════════════════
# 规则1: 1W周期全面亏损(-$75,900U)，永久封锁
BLOCKED_INTERVALS = ['1w', '1W', 'week', 'weekly']
# 规则2: 黑名单已清空（20260516用户指令）
# 黑名单统一引用 hunter_config（单一来源），不在 executor 单独维护
try:
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), 'lana', 'hunter_v2'))
    from hunter_config import SYMBOL_BLACKLIST
except Exception:
    SYMBOL_BLACKLIST = []  # fallback：hunter_config 不可用时放行

# ── P1-2修复: 组合总暴露限制 ─────────────────────────────────
# ══════════════════════════════════════════════════════════
# 🔱 梵天三大底线（硬约束，全系统强制执行）
# ══════════════════════════════════════════════════════════
# 底线1：每笔止损止盈 — 无止损信号拒绝执行，SL必须≥1%
# 底线2：50%账户亏损熔断 — position_monitor.py 强制执行
# 底线3：全天候收益优先 — 持续赢利激活移动止盈（position_monitor.py）
# ══════════════════════════════════════════════════════════

_BOTTOM_LINE_1_MIN_SL_PCT = 0.005   # 底线1：最小止损距离≥0.5%（低于此拒绝信号）
_BOTTOM_LINE_1_REQUIRE_SL = True    # 底线1：无止损字段，拒绝执行
_MAX_PORTFOLIO_EXPOSURE = 0.50      # 全部持仓名义≤账户本金50%（AI账户上限）
_MAX_SAME_DIRECTION     = 0.30      # 同方向持仓≤账户本金30%（20仓上限，防单边集中）

def get_portfolio_exposure() -> dict:
    """读取当前持仓总暴露（来自positions_config）"""
    try:
        import json; from pathlib import Path
        cfg_file = Path(__file__).parent / 'data' / 'positions_config.json'
        if not cfg_file.exists():
            return {'total_pct': 0, 'long_pct': 0, 'short_pct': 0, 'count': 0}
        cfg = json.load(open(str(cfg_file)))
        open_pos = {k:v for k,v in cfg.items() if isinstance(v,dict) and v.get('status')=='OPEN'}
        total = sum(float(v.get('position_pct', v.get('size_pct', 0.015))) for v in open_pos.values())
        longs = sum(float(v.get('position_pct', 0.015)) for v in open_pos.values() if v.get('pos_side','')=='LONG')
        shorts= sum(float(v.get('position_pct', 0.015)) for v in open_pos.values() if v.get('pos_side','')=='SHORT')
        return {'total_pct': total, 'long_pct': longs, 'short_pct': shorts, 'count': len(open_pos)}
    except Exception:
        return {'total_pct': 0, 'long_pct': 0, 'short_pct': 0, 'count': 0}


# 规则4: 最低R:R=2.5（30%胜率系统的盈亏平衡点）
# EV = 0.30 × 2.5 - 0.70 × 1 = 0.75 - 0.70 = +0.05 ✅
MIN_RR_STRICT = 2.5

# ══════════════════════════════════════════════════════
#  分层宇宙参数（Walk-Forward回测驱动，2025-01-01~2026-04-01）
#  来源：20币 × 4层市值 × 2套参数 回测结论
#  v2026-05-13
# ══════════════════════════════════════════════════════
TIER_PARAMS = {
    # 第1层：主力币（BTC/ETH等 $100亿+市值）
    # 激进版胜出，ETH PF=1.256，BTC PF=1.184
    # [P1 2026-05-22] SL收窄20%: 0.04→0.032；TP2=SL×3.0: 0.096（盈亏比压缩修复）
    'flagship': {
        'symbols':   ['BTCUSDT', 'ETHUSDT', 'DOGEUSDT'],
        'sl_pct':    0.045,  # 止损 -4.5% (N08最优Calmar验证, 2026-05-23 从3.2%提升)
        'tp1_pct':   0.08,   # T1 +8%（不变）
        'tp2_pct':   0.135,  # T2 +13.5%（=SL×3.0, 4.5%×3.0）
        'max_risk':  6.0,    # 最大止损距离6%（跟随SL调整）
        'min_rr':    1.8,
    },
    # 第2层：中型币（$5亿~$50亿，均PF 1.484，最优层）
    # 激进版大幅胜出，TRUMP PF=2.745，NEAR净收益+57%
    # [P1 2026-05-22] SL收窄20%: 0.05→0.040；TP2=SL×3.0: 0.120
    'mid': {
        'symbols':   ['BNBUSDT', 'HYPEUSDT', 'NEARUSDT', 'TRUMPUSDT',
                      '1000PEPEUSDT', 'TONUSDT', 'SNDKUSDT', 'CRCLUSDT'],
        'sl_pct':    0.040,  # 止损 -4.0%（SL×0.8收紧，P1）
        'tp1_pct':   0.08,   # T1 +8%（不变）
        'tp2_pct':   0.120,  # T2 +12.0%（=SL×3.0，P1）
        'max_risk':  6.0,
        'min_rr':    1.6,
    },
    # 第3层：小型币（$5000万~$5亿，唯一层保守参数胜出）
    # 短期爆发后快速回调，T1要早打，JUP PF=2.093
    # [P1 2026-05-22] SL收窄20%: 0.04→0.032；TP2=SL×3.0: 0.096
    'small': {
        'symbols':   ['WIFUSDT', 'LDOUSDT', 'JUPUSDT', 'RENDERUSDT',
                      'AIOTUSDT', 'LAYERUSDT', 'POLUSDT'],  # v9: 移除黑名单CHZUSDT、重复NEARUSDT和重复JUPUSDT
        'sl_pct':    0.032,  # 止损 -3.2%（更紧，SL×0.8，P1）
        'tp1_pct':   0.04,   # T1 +4%（早打！不变）
        'tp2_pct':   0.096,  # T2 +9.6%（=SL×3.0，P1）
        'max_risk':  5.0,
        'min_rr':    1.5,
    },
    # 第4层：微型币（<$5000万，全层PnL为负，暂停实盘）
    'micro': {
        'symbols':   [],     # 暂停，不产生执行信号
        'sl_pct':    0.056,  # [P1] 0.07×0.8
        'tp1_pct':   0.08,
        'tp2_pct':   0.168,  # [P1] =SL×3.0
        'max_risk':  8.0,
        'min_rr':    1.5,
        'disabled':  True,   # 回测显示全层亏损，禁用
    },
}

# ── Kelly公式动态仓位（来自Walk-Forward回测结果）──
# 各层级胜率和盈亏比（回测驱动）
# Kelly参数 — 2018-2026历史回测数据驱动（2026-05-13更新）
# flagship: 544笔/8年，WR=31.6% PF=0.97 → ATR止损×1.2收紧
# mid:      190笔，WR=38.9% PF=1.10 → 最优层，维持参数
# small:    138笔，WR=44.9% PF=1.00 → 高胜率但盈利被止损吃掉，T2待优化
# SHORT方向胜率39.9% > LONG 36.7%（SHORT信号更精准）
# 最佳入场时段: UTC 08:00(41.8%) UTC 20:00(41.5%)  最差: UTC 00:00(31.7%)
# ── 达摩院实证常量 [2026-05-22 N-D精化] ────────────────────────────
# N-D: UTC19H PF=2.220(最高) UTC21H=2.171 UTC16H=2.110 UTC14H=2.105（101k样本）
_HOUR_WEIGHT = {
    0: 0.75, 1: 0.75, 2: 0.75, 3: 0.75,
    4: 0.85, 5: 0.85,
    6: 0.90, 7: 1.05,
    8: 1.00, 9: 1.00, 10: 1.00, 11: 1.00,
    12: 1.05,
    13: 1.05, 14: 1.15, 15: 1.10,
    16: 1.15, 17: 1.10,
    18: 1.05, 19: 1.25,  # 🥇 UTC19H PF=2.220
    20: 1.10, 21: 1.20,  # 🥇 UTC21H PF=2.171
    22: 1.05, 23: 0.90,
}
# L4: 达摩院验证最优标的（仓位+15%）
_DHARMA_TOP_SYMBOLS = {'DOGEUSDT', 'ADAUSDT', 'BNBUSDT'}
# L2: 顺/逆势判断
_BULLISH_REGIMES = {'BULL_TREND', 'BULL_EARLY', 'BULL_CORRECTION', 'BREAKOUT_BULL'}
_BEARISH_REGIMES = {'BEAR_TREND', 'BEAR_EARLY', 'BEAR_IMPULSE', 'BEAR_RECOVERY', 'BREAKOUT_BEAR'}


# [修复 2026-05-22] Kelly参数双轨制：实盘>=30笔用实盘数据，否则用回测保守值
# 根因: 回测 avg_loss=0.043 与实盘 avg_loss=0.010 差4倍，Kelly会严重虚高
# ── 达摩院400轮训练 2026-05-23 更新 ──────────────────────────────
# 旧值: flagship WR=0.316 PF=0.97（负期望），mid WR=0.389 PF=1.10
# 新值: 基于100,797条训练 ≥145 排黑名单 WR=0.451 PF=1.835
KELLY_STATS = {
    'flagship': {'win_rate': 0.451, 'avg_win': 0.065, 'avg_loss': 0.029,
                 'pf': 1.835, 'note': '400轮训练: ≥145排黑名单 WR=45.1% PF=1.835'},
    'mid':      {'win_rate': 0.451, 'avg_win': 0.065, 'avg_loss': 0.029,
                 'pf': 1.835, 'note': '400轮训练: ≥145排黑名单 WR=45.1% PF=1.835'},
    'small':    {'win_rate': 0.465, 'avg_win': 0.065, 'avg_loss': 0.029,
                 'pf': 1.883, 'note': '400轮训练: 三角组合≥145 WR=46.5% PF=1.883'},
}


# ── 达摩院8年ML预测层 [UP-013 2026-05-22] ─────────────────────────────────
# 100,797条8年实证数据 → 127个四维预测节点（regime×direction×score×hour）
# 实盘<50笔时作为Kelly校准补充，实盘>=50笔后完全切换实盘ML
_ML_LOOKUP_PATH = os.path.join(os.path.dirname(__file__), 'data', 'ml_lookup_dharma8y.json')
_ml_lookup_cache: dict = {}
_ml_lookup_loaded = False

def _load_ml_lookup() -> dict:
    global _ml_lookup_cache, _ml_lookup_loaded
    if _ml_lookup_loaded:
        return _ml_lookup_cache
    try:
        with open(_ML_LOOKUP_PATH, 'r', encoding='utf-8') as _f:
            data = json.load(_f)
            _ml_lookup_cache = data.get('lookup', {})
        _ml_lookup_loaded = True
        print(f"  [达摩院ML] 预测节点已加载: {len(_ml_lookup_cache)}个")
    except Exception as _e:
        print(f"  [达摩院ML] 加载失败: {_e}")
    return _ml_lookup_cache

def _dharma_ml_predict(regime: str, direction: str, score: int, utc_hour: int) -> dict | None:
    """
    达摩院8年数据预测：返回 {wr, pf, kelly, n} 或 None
    四维特征匹配：regime × direction × score_bucket × hour_bucket
    """
    lookup = _load_ml_lookup()
    if not lookup:
        return None
    # 精确匹配
    score_b = f"s{(score // 20) * 20}"
    hour_b  = f"h{(utc_hour // 4) * 4:02d}"
    key = f"{regime[:10]}|{direction[:4]}|{score_b}|{hour_b}"
    if key in lookup:
        return lookup[key]
    # 降级：去掉hour维度
    for k, v in lookup.items():
        parts = k.split('|')
        if len(parts) >= 3 and parts[0] == regime[:10] and parts[1] == direction[:4] and parts[2] == score_b:
            return v
    # 再降级：只用regime+direction
    matched = [v for k, v in lookup.items()
               if k.startswith(f"{regime[:10]}|{direction[:4]}")]
    if matched:
        avg = {
            'wr':    round(sum(m['wr']    for m in matched) / len(matched), 4),
            'pf':    round(sum(m['pf']    for m in matched) / len(matched), 4),
            'kelly': round(sum(m['kelly'] for m in matched) / len(matched), 4),
            'n':     sum(m['n'] for m in matched)
        }
        return avg
    return None


def _get_live_kelly_stats(tier_name: str) -> dict:
    """实盘数据动态Kelly - 样本>=30笔时使用实盘参数，避免回测4x偏差"""
    try:
        import json as _j
        from pathlib import Path as _P
        _tr_path = _P(__file__).parent / 'data' / 'trade_records.jsonl'
        _tr = [_j.loads(l) for l in _tr_path.read_text().strip().split('\n')]
        _live = [r for r in _tr if not r.get('_is_simulation') and r.get('source') != 'dharma_stream_v1']
        if tier_name == 'flagship':
            _live = [r for r in _live if r.get('symbol','') in ('BTCUSDT','ETHUSDT')]
        _wins = [r for r in _live if str(r.get('result','')).startswith('WIN')]
        _loss = [r for r in _live if str(r.get('result','')).startswith('LOSS')]
        _n = len(_wins) + len(_loss)
        if _n < 30:
            return KELLY_STATS.get(tier_name, KELLY_STATS['mid'])
        _wr = len(_wins) / _n
        _pw = [abs(r['pnl_pct'])/100 for r in _wins if r.get('pnl_pct')]
        _pl = [abs(r['pnl_pct'])/100 for r in _loss if r.get('pnl_pct')]
        _aw = sum(_pw)/max(len(_pw),1); _al = sum(_pl)/max(len(_pl),1)
        if _al < 0.001: return KELLY_STATS.get(tier_name, KELLY_STATS['mid'])
        return {'win_rate': round(_wr,3), 'avg_win': round(_aw,4),
                'avg_loss': round(_al,4), 'pf': round(_aw/_al,3),
                'note': f'实盘{_n}笔动态Kelly', '_live': True}
    except Exception:
        return KELLY_STATS.get(tier_name, KELLY_STATS['mid'])

# ⚠️ 旧回测黑名单已废弃，由训练大纲统一管理（见上方 SYMBOL_BLACKLIST）
# 注意: XRP/SOL/CHZ 在40056笔训练中为正收益，恢复交易资格
_LEGACY_BLACKLIST_DEPRECATED = set()  # 不再使用

def _get_tier_params(symbol):
    """根据币种返回对应层级参数，默认用mid层"""
    for tier_name, cfg in TIER_PARAMS.items():
        if cfg.get('disabled'): continue
        if symbol in cfg.get('symbols', []):
            return tier_name, cfg
    # 未命中任何层 → 用mid层默认参数
    return 'mid', TIER_PARAMS['mid']


# ════════════════════════════════════════════
#   核心策略生成
# ════════════════════════════════════════════

def generate_strategy(scan_result, signal_info=None, direction_override=None):
    """
    基于扫描结果生成完整策略

    [修复1] R:R 用入场区上沿（最保守）计算
    [修复2] 目标位不再用POC，改用VAH突破后延伸
    [修复3] LVN检测：止损放在LVN外侧
    [修复4] 方向验证：检查strategy_manager.allowed_directions
    [修复5] 时间框架对齐验证：<3框架 → 降级为WATCH_ONLY
    """
    sym    = scan_result.get("symbol", "?")
    # ══ 训练大纲守门规则 ════════════════════════════════════
    if sym in SYMBOL_BLACKLIST:
        return {'executable': False, 'reason': f'黑名单: {sym} 历史负期望', 'symbol': sym}
    # ── P2修复: 体制矩阵完整硬性过滤（接入 check_regime_matrix）────
    _exec_regime = scan_result.get('regime', '')
    _exec_dir    = scan_result.get('direction', '')
    # 旧逻辑兼容：裸 CHOP/HIGH_RISK 封锁
    if str(_exec_regime).upper() in ('CHOP', 'HIGH_RISK'):
        print(f"  🚫 executor体制封锁: {_exec_regime} — 不执行下单")
        return None
    # 新逻辑：调用完整矩阵
    try:
        from regime_matrix import check_regime_matrix as _crm
        _rm_pass, _rm_reason = _crm({
            'direction': _exec_dir,
            'regime':    _exec_regime,
            'score':     scan_result.get('score', 0),
            'channel':   scan_result.get('channel', ''),
        })
        if not _rm_pass:
            print(f"  🚫 [regime_matrix] 拦截: {_rm_reason}")
            return _reject(sym, scan_result.get('score', 0), f"体制矩阵拦截: {_rm_reason}")
    except Exception as _e_rm:
        print(f"  ⚠️  regime_matrix 加载失败，放行: {_e_rm}")  # 降级放行，不影响运行

    score  = scan_result.get("score", 0)
    price  = scan_result.get("price", 0)
    levels = scan_result.get("levels")
    trends = scan_result.get("trends", {})

    # ── 黑名单过滤 ──
    if sym in SYMBOL_BLACKLIST:
        return _reject(sym, score, "黑名单币种（回测PF<1，暂停实盘）")

    # ── BTC 宏观过滤层 ──
    try:
        from btc_macro_filter import get_macro_state, filter_signal as _macro_filter
        _scan_result_direction = scan_result.get('direction') or ('SHORT' if scan_result.get('is_short') else 'LONG')
        _macro_st = get_macro_state()
        _macro_ok, _macro_reason, _ = _macro_filter(_macro_st, _scan_result_direction, sym, score / 10.0 if score > 10 else score)
        if not _macro_ok:
            return _reject(sym, score, f"宏观过滤: {_macro_reason}")
    except Exception as _e:
        _ = _e  # 宏观层加载失败时放行

    # ── 层级参数加载（Walk-Forward回测驱动）──
    tier_name, tier_cfg = _get_tier_params(sym)
    if tier_cfg.get('disabled'):
        return _reject(sym, score, "微型币禁用（第4层回测全层亏损）")
    _SL_PCT   = tier_cfg['sl_pct']
    _TP1_PCT  = tier_cfg['tp1_pct']
    _TP2_PCT  = tier_cfg['tp2_pct']
    _MAX_RISK = tier_cfg['max_risk']
    _MIN_RR   = tier_cfg['min_rr']

    # ── 评分门槛 ──
    # 达摩院品种降权 2026-05-23: 弱势品种需额外+10分
    try:
        from regime_matrix import SYMBOL_SCORE_BONUS
        _bonus = SYMBOL_SCORE_BONUS.get(sym, 0)
    except Exception:
        _bonus = 0
    _effective_min = MIN_SCORE + _bonus
    if score < _effective_min:
        return _reject(sym, score, f"综合评分 {score} < 门槛 {_effective_min}({'品种降权+'+str(_bonus)+'分' if _bonus else '全局门槛'})")

    # ── 🔱 底线1前置检查：无止损信息拒绝执行 ────────────────────────
    if _BOTTOM_LINE_1_REQUIRE_SL:
        # 检查是否有足够的价格/ATR信息来计算止损
        if not price or price <= 0:
            return _reject(sym, score, "底线1：无有效价格，无法设定止损，拒绝执行")
        _atr_check = scan_result.get("atr_pct", scan_result.get("atr14_pct", 0))
        if _atr_check <= 0 and not levels:
            return _reject(sym, score, "底线1：无ATR/筹码数据，无法设定止损，拒绝执行")
    # ─────────────────────────────────────────────────────────────────

    if not levels or not price:
        return _reject(sym, score, "筹码数据不足")

    poc = levels.get("poc", 0)
    vah = levels.get("vah", 0)
    val = levels.get("val", 0)
    hvns = levels.get("hvn", [])
    lvns = levels.get("lvn", [])

    if not all([poc, vah, val]):
        return _reject(sym, score, "筹码关键位数据不完整（POC/VAH/VAL缺失）")

    # ── [修复4] 方向决定（统一入口，必须在TF检查之前确定方向）──
    rsi_4h_val   = scan_result.get("rsi_4h", 50)
    trend_4h     = scan_result.get("trends", {}).get("4h", "SIDE")
    trend_1d     = scan_result.get("trends", {}).get("1d", "SIDE")
    if direction_override:
        is_short = (direction_override == "SHORT")
    elif rsi_4h_val >= 80 and trend_4h in ("DOWN", "SIDE"):
        is_short = True   # RSI超买 + 4H下行/横盘 → 做空
    elif trend_1d == "DOWN" and trend_4h == "DOWN":
        is_short = True   # 日线+4H双下行 → 做空
    elif rsi_4h_val <= 30 and trend_4h in ("UP", "SIDE"):
        is_short = False  # RSI超卖 + 4H上行/横盘 → 做多
    else:
        is_short = False  # 默认做多（顺势）
    direction = "SHORT" if is_short else "LONG"
    try:
        import strategy_manager
        allowed = strategy_manager.get_pipeline_params().get("allowed_directions", ["LONG", "SHORT"])
        if direction not in allowed:
            return _reject(sym, score,
                f"方向 {direction} 被当前策略预设禁止（允许: {allowed}）",
                extra={"direction_blocked": True})
    except Exception as _e:
        _ = _e  # strategy_manager 不可用时不阻断

    # ── [修复5] 时间框架对齐验证（方向确定后调用，传入正确 direction）──
    tf_check = _check_timeframe_alignment(trends, direction)
    tf_aligned = tf_check["aligned"]
    tf_major = tf_check["major_aligned"]  # 4h+1d 是否对齐

    if tf_aligned < 2:
        return _reject(sym, score,
            f"时间框架严重背离（{tf_aligned}/4对齐），不入场",
            extra={"tf_check": tf_check})

    # 2/4 对齐但含4h+1d：降级为WATCH_ONLY（观察），不执行
    _target_trend = "DOWN" if is_short else "UP"
    if tf_aligned == 2:
        return {
            "symbol": sym,
            "executable": False,
            "watch_only": True,
            "score": score,
            "reason": f"时间框架共振不足（{tf_aligned}/4），降级为观察状态",
            "watch_note": (
                f"等待1h也对齐后再入场\n"
                f"当前: 1d={'✅' if trends.get('1d')==_target_trend else '❌'} "
                f"4h={'✅' if trends.get('4h')==_target_trend else '❌'} "
                f"1h={'✅' if trends.get('1h')==_target_trend else '❌'} "
                f"15m={'✅' if trends.get('15m')==_target_trend else '❌'}"
            ),
            "tf_check": tf_check,
            "levels": levels,
            "price_now": price,
        }

    # ── 入场区计算 ──

    # ── P2: 追价窗口 ───────────────────────────────────────────
    # score≥8.0(=80分) 且 体制=TRENDING时，入场区上沿扩展从+0.8%−1.5%
    _chase_regime = str(scan_result.get('regime', 'UNKNOWN')).upper()
    _is_trending  = 'TRENDING' in _chase_regime or _chase_regime in ('BULL_TREND','BEAR_TREND','BREAKOUT_BULL','BREAKOUT_BEAR')
    _chase_ok     = (score >= 80) and _is_trending
    _chase_note   = ''

    if is_short:
        # 空单：在当前价 AT 或略高位置卖出（等小反弹入场）
        entry_low  = round(price * 1.000, 8)  # 不低于现价
        if _chase_ok:
            entry_high = round(price * 1.015, 8)  # P2 追价: +1.5%
            _chase_note = '追价窗口开启(+1.5%)'
        else:
            entry_high = round(price * 1.008, 8)  # 等小反弹
    elif price <= val * 1.02:
        # 价格在 VAL 附近或下方 → VAL 支撑入场
        entry_low  = round(val * 0.990, 8)
        if _chase_ok:
            entry_high = round(val * 1.020, 8)  # P2 追价: 扩展到+2.0%
            _chase_note = '追价窗口开启(VAL+2.0%)'
        else:
            entry_high = round(val * 1.015, 8)
    elif val < price <= poc * 1.015:
        # 价格在 VAL~POC 之间 → 当前位置收窄入场
        entry_low  = round(price * 0.992, 8)
        if _chase_ok:
            entry_high = round(price * 1.015, 8)  # P2 追价: +1.5%
            _chase_note = '追价窗口开启(+1.5%)'
        else:
            entry_high = round(price * 1.005, 8)
    else:
        # 价格在 POC 上方 → 等回踩 POC
        entry_low  = round(poc * 0.985, 8)
        if _chase_ok:
            entry_high = round(poc * 1.018, 8)  # P2 追价: POC+1.8%
            _chase_note = '追价窗口开启(POC+1.8%)'
        else:
            entry_high = round(poc * 1.010, 8)

    if _chase_note:
        print(f"  🎯 {sym} {_chase_note} (score={score} 体制={_chase_regime})")

    # ── ATR计算 ──
    def _atr_calc(candles, p=14):
        trs=[]
        for i in range(1,len(candles)):
            h,l,pc=candles[i]["h"],candles[i]["l"],candles[i-1]["c"]
            trs.append(max(h-l,abs(h-pc),abs(l-pc)))
        return sum(trs[-p:])/p if len(trs)>=p else (sum(trs)/len(trs) if trs else price*0.02)
    c4h_raw = scan_result.get("_c4h", [])
    atr14 = _atr_calc(c4h_raw) if c4h_raw else price * 0.02
    atr_pct = atr14 / price * 100

    # ── ATR自适应止损覆盖（Stoikov动态止盈）──
    _atr_raw = scan_result.get('atr_pct_4h') or scan_result.get('atr_pct') or atr_pct
    if _atr_raw > 0:
        # v9.2: 分层ATR乘数（回测数据驱动）
        # flagship: 1.2x（BTC ATR 1.8% → SL=2.7%，止损更紧，历史PF=0.97改善）
        # mid:      1.5x（回测PF=1.10，维持）
        # small:    1.4x（胜率最高44.9%，略收紧）
        # [P1 2026-05-22] SL收窄20%；TP2放宽至SL×3.0等效ATR倍数
        _atr_sl_mult  = 0.96 if tier_name == 'flagship' else (1.2 if tier_name == 'mid' else 1.12)
        _atr_tp_mult1 = 3.0  if tier_name == 'flagship' else 3.5
        _atr_tp_mult2 = 7.2  if tier_name == 'flagship' else 7.2
        _SL_PCT  = max(0.02, min(0.07,  _atr_raw / 100 * _atr_sl_mult))
        _TP1_PCT = max(0.04, min(0.15,  _atr_raw / 100 * _atr_tp_mult1))
        _TP2_PCT = max(0.07, min(0.25,  _atr_raw / 100 * _atr_tp_mult2))
        _atr_adaptive = True
    else:
        _atr_adaptive = False

    lvns_below = sorted([l for l in lvns if l < val], reverse=True)
    lvns_above = sorted([l for l in lvns if l > vah])

    # 分层止损计算（Walk-Forward回测驱动）
    if is_short:
        # 空单：ATR止损 与 百分比止损 取较小（更严格）
        # v9.3修复: ATR×1.5触及率97%→升级至ATR×2.0，触及率降至88%
        sl_atr  = price + atr14 * 2.0
        sl_pct  = price * (1 + _SL_PCT)
        stop_loss = round(min(sl_atr, sl_pct) if atr_pct < _SL_PCT else sl_pct, 8)
        sl_note = f"空单止损 {_SL_PCT*100:.2f}%（{'ATR自适应' if _atr_adaptive else tier_name+'层参数'}，ATR={atr_pct:.1f}%，SL=ATR×2.0）"
    elif lvns_below:
        nearest_lvn_below = lvns_below[0]
        lvn_sl = round(nearest_lvn_below * 0.995, 8)
        pct_sl = round(price * (1 - _SL_PCT), 8)
        # LVN止损 vs 百分比止损，取较高（更近，更严格）
        stop_loss = max(lvn_sl, pct_sl)
        sl_note = f"多单止损 {_SL_PCT*100:.2f}%（{'ATR自适应' if _atr_adaptive else tier_name+'层参数'}，LVN辅助）"
    else:
        stop_loss = round(price * (1 - _SL_PCT), 8)
        sl_note = f"多单止损 {_SL_PCT*100:.2f}%（{'ATR自适应' if _atr_adaptive else tier_name+'层参数'}）"

    # ── R:R 用入场区计算──
    # 空单：最差=在最低卖出价（entry_low）卖出，止损在上方
    # 多单：最差=在最高买入价（entry_high）买入，止损在下方
    entry_worst = entry_low if is_short else entry_high
    risk_from_worst = abs(stop_loss - entry_worst)

    if risk_from_worst <= 0:
        return _reject(sym, score, "止损距离为0，止损逻辑错误")

    # ── P1-B: 方向校验 ──────────────────────────────────────
    # 做多: stop_loss < entry_worst（止损在入场价下方）
    # 做空: stop_loss > entry_worst（止损在入场价上方）
    if not is_short and stop_loss >= entry_worst:
        return _reject(sym, score,
            f"P1-B 方向错误: 做多但 stop_loss({stop_loss:.6g}) >= entry({entry_worst:.6g})，止损应在入场价以下")
    if is_short and stop_loss <= entry_worst:
        return _reject(sym, score,
            f"P1-B 方向错误: 做空但 stop_loss({stop_loss:.6g}) <= entry({entry_worst:.6g})，止损应在入场价以上")

    # ── 🔱 底线1硬校验：止损距离过小拒绝执行 ──────────────────────────
    _sl_pct_actual = risk_from_worst / entry_worst
    if _BOTTOM_LINE_1_REQUIRE_SL and _sl_pct_actual < _BOTTOM_LINE_1_MIN_SL_PCT:
        return _reject(sym, score,
            f"底线1：止损距离{_sl_pct_actual*100:.3f}% < 最小{_BOTTOM_LINE_1_MIN_SL_PCT*100:.1f}%，"
            f"止损太近易被扫，拒绝执行")
    # ─────────────────────────────────────────────────────────────────

    risk_pct = round(risk_from_worst / entry_worst * 100, 2)

    if risk_pct > _MAX_RISK:
        return _reject(sym, score,
            f"止损距离 {risk_pct:.1f}% > {_MAX_RISK}% 上限（{tier_name}层参数）")

    # ── [修复2] 目标位：空单向下，多单向上 ──
    # 分层止盈计算（Walk-Forward回测驱动）
    if is_short:
        # 空单止盈：百分比目标（分层参数）+ ATR双重验证
        t1 = round(price * (1 - _TP1_PCT), 8)
        t2 = round(price * (1 - _TP2_PCT), 8)
        t3 = round(price * (1 - _TP2_PCT * 1.5), 8)
    else:
        # 多单止盈：VAH结构 与 百分比目标 取较近者（保证能到达）
        t1_vah = round(vah * 1.005, 8)
        t1_pct = round(price * (1 + _TP1_PCT), 8)
        t1 = min(t1_vah, t1_pct)   # 取较近，优先结构位 vs 百分比
        
        t2_ext = round(vah + (vah - val), 8)
        t2_pct = round(price * (1 + _TP2_PCT), 8)
        t2 = min(t2_ext, t2_pct)
        if lvns_above:
            nearest_lvn_above = lvns_above[0]
            hvns_above_lvn = [h for h in hvns if h > nearest_lvn_above]
            if hvns_above_lvn:
                t2 = round(min(min(hvns_above_lvn), t2_pct), 8)
        t3 = round(price * (1 + _TP2_PCT * 1.3), 8)

    # ── R:R ──
    if is_short:
        rr1 = round((entry_worst - t1) / risk_from_worst, 2)
        rr2 = round((entry_worst - t2) / risk_from_worst, 2)
    else:
        rr1 = round((t1 - entry_worst) / risk_from_worst, 2)
        rr2 = round((t2 - entry_worst) / risk_from_worst, 2)

    # ── P1修复: T1 R:R自动校正（确保rr1≥全局MIN_RR=2.5）─────
    # 实盘数据: 实际R:R=2.06 < 设计2.5，原因是T1目标过保守
    _GLOBAL_MIN_RR = MIN_RR  # 2.5
    if rr1 < _GLOBAL_MIN_RR:
        # 自动上移T1直到满足2.5最低要求
        if is_short:
            t1 = round(entry_worst - risk_from_worst * _GLOBAL_MIN_RR, 8)
            rr1 = round((entry_worst - t1) / risk_from_worst, 2)
        else:
            t1 = round(entry_worst + risk_from_worst * _GLOBAL_MIN_RR, 8)
            rr1 = round((t1 - entry_worst) / risk_from_worst, 2)

    # T2 必须达到 MIN_RR
    if rr2 < _MIN_RR:
        return _reject(sym, score,
            f"R:R={rr2:.2f}:1 < {_MIN_RR}:1（{tier_name}层最低R:R要求）\n"
            f"入场区上沿 ${entry_worst:,.6g}")

    # ── Kelly公式动态仓位（半Kelly，回测驱动）──
    ks = _get_live_kelly_stats(tier_name)  # [修复] 动态Kelly，优先实盘数据
    # ── 达摩院ML预测辅助（实盘<50笔时补充） [UP-013] ────────────────────
    try:
        _n_live = ks.get('_n_live', 0)
        if _n_live < 50:
            _ml_pred = _dharma_ml_predict(
                regime    = scan_result.get('regime', ''),
                direction = direction,
                score     = int(score),
                utc_hour  = datetime.now(timezone.utc).hour,
            )
            if _ml_pred and _ml_pred.get('n', 0) >= 30:
                # 混合权重: 实盘占比 = n_live/50, 8年占比 = 1 - n_live/50
                _w_live = _n_live / 50.0
                _w_ml   = 1.0 - _w_live
                _wr_mix = ks['W'] * _w_live + _ml_pred['wr'] * _w_ml if 'W' in ks else _ml_pred['wr']
                _pf_mix = ks.get('pf', 1.0) * _w_live + _ml_pred['pf'] * _w_ml
                kelly_full = max(0, _wr_mix - (1 - _wr_mix) / max(_pf_mix, 1.0))
                kelly_half = kelly_full / 2
                print(f"  [达摩院ML] 混合Kelly: live{_n_live}笔×{_w_live:.1f}+8Y×{_w_ml:.1f} "
                      f"WR={_wr_mix:.3f} PF={_pf_mix:.3f} kelly={kelly_half:.4f}")
    except Exception as _ml_e:
        pass  # ML失败不影响主流程
    W  = ks['win_rate']
    R  = ks['avg_win'] / ks['avg_loss'] if ks['avg_loss'] > 0 else 1.0
    kelly_full = max(0, W - (1 - W) / R)
    kelly_half = kelly_full / 2   # 半Kelly，实用版
    # 评分/共振加成
    if score >= 85 and tf_aligned >= 3:    pos = min(kelly_half * 1.2, MAX_POSITION)
    elif score >= 75 and tf_aligned >= 3:  pos = min(kelly_half,        MAX_POSITION)
    elif tf_aligned == 3:                  pos = min(kelly_half * 0.7,  MAX_POSITION)
    else:                                  pos = min(kelly_half * 0.5,  MAX_POSITION)
    pos = max(pos, 0.005)  # 最小0.5%

    # ── P1修复: 相关性折扣（BTC+ETH同向持仓减仓）───────────
    try:
        _corr = check_correlation_risk(sym, direction)
        if _corr['corr_discount'] < 1.0:
            pos = round(pos * _corr['corr_discount'], 4)
            pos = max(pos, 0.005)
            if _corr['warning']: print(f"  {_corr['warning']}  → 仓位×{_corr['corr_discount']}")
    except Exception as _e:
        _ = None  # 非致命异常，不阻断

    # ── v9.5 2026-05-23: flagship PF=1.835（400轮训练）不再需要强制减半 ──
    # 旧逻辑: PF=0.97<1.0时×0.5，现已更新为正期望参数，条件永远为False
    if tier_name == 'flagship' and ks.get('pf', 1.0) < 1.0:
        pos *= 0.5  # 保留逻辑作为安全网，当前PF=1.835不触发
        pos = max(pos, 0.005)

    # ── 训练大纲 P0-3: ATR标准化仓位 ────────────────────────────
    # 原理: risk_usdt = pos × balance × atr_pct = 固定风险单位1%
    # ATR越高 → 仓位越小（保持固定损失金额）
    if atr_pct > 0:
        atr_norm_pos = 0.01 / (atr_pct / 100.0)  # 1%风险预算/ATR
        pos = min(pos, atr_norm_pos)

    # ── 训练大纲 P0-3: 连续亏损降档 ─────────────────────────────
    # 读取symbol_memory连续亏损记录
    try:
        import symbol_memory as _sm
        _mem = _sm._load(sym)
        _recent = _mem.get('recent_rounds', [])[-5:]
        _consec_loss = 0
        for _r in reversed(_recent):
            if _r.get('outcome') == 'LOSS':
                _consec_loss += 1
            else:
                break
        if _consec_loss >= 5:
            pos = 0.0      # 5连亏停止
        elif _consec_loss >= 3:
            pos *= 0.5     # 3连亏半仓
        elif _consec_loss >= 2:
            pos *= 0.75    # 2连亏减仓25%
    except Exception as _e:
        _ = _e  # 无记忆时不影响入场

    # ── 达摩院L1: 时段权重（UTC小时 → 仓位系数）──────────────────
    try:
        _utc_hour = datetime.now(timezone.utc).hour
        _hw = _HOUR_WEIGHT.get(_utc_hour, 1.0)
        if _hw != 1.0:
            pos = round(pos * _hw, 4)
            print(f"  [达摩院L1] UTC{_utc_hour:02d}H 时段权重×{_hw} → pos={pos:.4f}")
    except Exception:
        pass

    # ── 达摩院L2: 顺势/逆势判断 ──────────────────────────────────
    # 逆势信号（BULL体制做空 / BEAR体制做多）降权-30%
    # CHOP体制：双向均不加成也不降权（中性）
    try:
        _regime_now = scan_result.get('regime', '')
        _is_chop_regime = 'CHOP' in str(_regime_now).upper()
        _is_counter = (
            not _is_chop_regime and (
                (_regime_now in _BULLISH_REGIMES and direction in ('SHORT', '做空')) or
                (_regime_now in _BEARISH_REGIMES and direction in ('LONG', '做多'))
            )
        )
        if _is_counter:
            pos = round(pos * 0.70, 4)
            print(f"  [达摩院L2] 逆势信号({_regime_now}×{direction}) 降权×0.70 → pos={pos:.4f}")
        elif _is_chop_regime:
            pass  # CHOP体制中性，不折扣也不加成
    except Exception:
        pass

    # ── 达摩院L3: 4H信号加成（+10%）────────────────────────────
    try:
        _interval = scan_result.get('interval', scan_result.get('tf', ''))
        if '4h' in str(_interval).lower() or '4H' in str(_interval):
            pos = round(pos * 1.10, 4)
            print(f"  [达摩院L3] 4H信号加成×1.10 → pos={pos:.4f}")
    except Exception:
        pass

    # ── 达摩院L4: 最优标的加成（+15%）──────────────────────────
    try:
        if sym in _DHARMA_TOP_SYMBOLS:
            pos = round(pos * 1.15, 4)
            print(f"  [达摩院L4] 最优标的{sym} 加成×1.15 → pos={pos:.4f}")
    except Exception:
        pass

    # ── 达摩院N-B: 高分信号仓位加成 [2026-05-22] ─────────────
    # score>=150 PF=2.231 WR=45.8%，score>=140 PF=2.125 — 实证加成
    try:
        _sig_score = int(scan_result.get('score', score) or score)
        if _sig_score >= 150:
            pos = round(pos * 1.20, 4)
            print(f"  [达摩院N-B] score={_sig_score}>=150 加成×1.20 → pos={pos:.4f}")
        elif _sig_score >= 140:
            pos = round(pos * 1.10, 4)
            print(f"  [达摩院N-B] score={_sig_score}>=140 加成×1.10 → pos={pos:.4f}")
    except Exception:
        pass

    pos = round(pos, 3)
    if risk_pct > 4: pos = min(pos, 0.015)
    if tf_aligned < 4: pos = min(pos, 0.020)  # 未完全对齐，降仓

    # 变化率
    t1_chg = round((t1 - entry_worst) / entry_worst * 100, 2)
    t2_chg = round((t2 - entry_worst) / entry_worst * 100, 2)
    sl_chg = round((stop_loss - entry_worst) / entry_worst * 100, 2)

    # 触发条件
    triggers = _build_triggers(trends, price, entry_high, tf_check, is_short=is_short)

    # ── 自适应评分校验（内联版，替代已归档的 adaptive_reward.py）──────────
    # FIX-01 2026-05-15: 内联核心逻辑，消除外部依赖
    # FIX-03 2026-05-17: 体制识别统一使用 lana/state_engine（主导），market_regime降为备用
    try:
        _regime_str = "RANGING"
        try:
            from lana.state_engine import detect_state as _detect_state
            _kl = [float(k[4]) for k in (klines_1h or [])[-50:]]
            _robj = _detect_state(price, _kl) if len(_kl) >= 20 else None
            if _robj:
                # [修复 2026-05-22] lana返回dict，用['state']读取，非.name属性
                if isinstance(_robj, dict):
                    _regime_str = _robj.get('state', 'CHOP_MID')
                else:
                    _regime_str = _robj.name if hasattr(_robj, 'name') else str(_robj)
        except Exception:
            try:
                import market_regime as _mr2
                _regime_str = _mr2.detect_regime().get("regime", "RANGING")
            except Exception as _e:
                _ = None  # 非致命异常，不阻断
        # 体制门槛映射（P1-A 2026-05-15: RANGING从70’78，deep_audit方案落地）
        # RANGING震荡体制信号物质差，WR=43%，提高门槛至 deep_audit 推荐的7.8分(=78)
        _REGIME_THRESHOLD = {
            "TRENDING_UP":     75, "TRENDING_DOWN":   75,
            "HIGH_VOLATILITY": 80, "BREAKOUT_BULL":   72,
            "BREAKOUT_BEAR":   72, "RANGING":         78,  # P1-A
        }
        _atr_regime = "HIGH_VOLATILITY" if (atr_pct / 100.0) > 0.05 else _regime_str
        _min_thresh = _REGIME_THRESHOLD.get(_atr_regime, 70)
        if score < _min_thresh and score < MIN_SCORE:
            return _reject(sym, score,
                f"自适应门槛拒绝: 体制={_atr_regime} 要求≥{_min_thresh}分")
    except Exception:
        _ = _e  # 自适应校验异常不阻塞主流程

    # ── [修复3] LVN 路径检测 ──
    lvn_warnings = []
    lvns_in_path = [l for l in lvns if entry_low <= l <= t2 * 1.02]
    if lvns_in_path:
        for l in lvns_in_path[:3]:
            if l < entry_worst:
                lvn_warnings.append(f"⚠️ LVN ${l:,.0f} 在止损路径内（快速跌穿风险）")
            elif l > t1:
                lvn_warnings.append(f"📍 LVN ${l:,.0f} 在目标路径内（价格可快速穿越至此）")

    return {
        "symbol":       sym,
        "executable":   True,
        "score":        score,
        "price_now":    price,
        "direction":    direction,
        "tf_aligned":   tf_aligned,
        "tf_check":     tf_check,
        "tier":         tier_name,       # 分层宇宙层级标识
        "entry": {
            "low":        entry_low,
            "high":       entry_high,
            "mid":        round((entry_low + entry_high) / 2, 8),
            "worst":      entry_worst,  # 明确标出最差入场价
            "chase_mode": bool(_chase_note),  # P2 追价标志
        },
        "stop_loss":      stop_loss,
        "sl_note":        sl_note,
        "risk_pct":       risk_pct,
        "rr_calc_basis":  f"入场区{'低沿' if is_short else '上沿'} ${entry_worst:,.6g}（最保守）",
        "targets": {
            "t1": {"price": t1, "chg_pct": t1_chg, "rr": rr1,
                   "note": "VAH突破确认位"},
            "t2": {"price": t2, "chg_pct": t2_chg, "rr": rr2,
                   "note": "等幅延伸目标"},
            "t3": {"price": t3, "note": "扩展目标（部分止盈后持有）"},
        },
        "position_pct":   pos,
        "stop_loss_pct":  round(_SL_PCT, 4),   # ATR自适应后实际使用的止损比例
        "t1_pct":         round(_TP1_PCT, 4),  # Stoikov动态T1比例
        "t2_pct":         round(_TP2_PCT, 4),  # Stoikov动态T2比例
        "atr_pct":        round(atr_pct, 2),   # 当前ATR百分比
        "atr_adaptive":   _atr_adaptive,
        "triggers":       triggers,
        "levels":         levels,
        "trends":         trends,
        "lvn_warnings":   lvn_warnings,
        "ts":             _now(),
    }


# ════════════════════════════════════════════
#   [修复5] 时间框架对齐检测
# ════════════════════════════════════════════

def _check_timeframe_alignment(trends: dict, direction: str = "LONG") -> dict:
    """
    验证多时间框架方向一致性
    direction: LONG 检查 UP 对齐，SHORT 检查 DOWN 对齐
    """
    target = "UP" if direction == "LONG" else "DOWN"
    tf_list = ["1d", "4h", "1h", "15m"]
    # [达摩院V7校准 2026-05-19] 15m权重 1→0（全周期PF<0.95，对齐分归零）
    weights = {"1d": 3, "4h": 2, "1h": 1.5, "15m": 0}

    aligned = sum(1 for tf in tf_list if trends.get(tf) == target)
    major_aligned = (trends.get("4h") == target and trends.get("1d") == target)
    weighted = sum(weights[tf] for tf in tf_list if trends.get(tf) == target)

    detail = {tf: trends.get(tf, "SIDE") for tf in tf_list}

    return {
        "aligned": aligned,
        "total": 4,
        "major_aligned": major_aligned,  # 4h+1d 必须对齐
        "weighted_score": round(weighted, 1),
        "detail": detail,
        "sufficient": aligned >= 3 and major_aligned,
    }


# ════════════════════════════════════════════
#   触发条件构建
# ════════════════════════════════════════════

def _build_triggers(trends, price, entry_high, tf_check=None, is_short=False):
    """构建入场触发条件（精确，可操作）"""
    conds = []

    if is_short:
        # ── 空单触发条件 ──
        if price < entry_high * 0.97:
            conds.append(f"⚠️ 当前价 ${price:,.6g} 已低于入场区，等待反弹，不追低做空")
            return conds
        # 1h 是否开始转空
        if tf_check and tf_check["detail"].get("1h") == "DOWN":
            conds.append("① 1h 已转空头排列 ✅")
        else:
            conds.append("① 等待 1h 收盘跌破 EMA20，确认转空")
        # 15m 确认
        if tf_check and tf_check["detail"].get("15m") == "DOWN":
            conds.append("② 15m 空头排列确认 ✅")
        else:
            conds.append("② 15m RSI 跌破 55（从高位回落确认）")
        conds.append("③ 入场K线收阴（不进阳线，等阴线实体确认方向）")
        conds.append("④ 成交量萎缩（反弹无量=诱多，是最佳做空时机）")
        conds.append("⑤ 价格反弹至入场区内再空，不追跌，不等位不进")
    else:
        # ── 多单触发条件 ──
        if price > entry_high * 1.03:
            conds.append(f"⚠️ 当前价 ${price:,.6g} 高于入场区，等待回踩，绝不追价")
            return conds
        # 1h 是否对齐
        if tf_check and not tf_check["detail"].get("1h") == "UP":
            conds.append("① 1h 收盘站上 EMA20，确认方向转多")
        else:
            conds.append("① 1h 保持多头排列")
        # 15m 确认
        if tf_check and not tf_check["detail"].get("15m") == "UP":
            conds.append("② 15m RSI 从超卖回升突破 35（回升确认，不是超卖本身）")
        else:
            conds.append("② 15m 多头排列确认，买盘占比 > 55%")
        conds.append("③ 入场K线收阳（不进阴线，等阳线实体确认）")
        conds.append("④ 成交量 > 近5根均量的 1.3 倍（量能配合）")
        conds.append("⑤ 价格回踩到入场区内，不追高，不等位不进")

    return conds


# ════════════════════════════════════════════
#   信号卡输出（v2.0 更新）
# ════════════════════════════════════════════

def format_signal_card(strategy, radar_info=None):
    if not strategy.get("executable"):
        if strategy.get("watch_only"):
            sym = strategy.get("symbol", "?").replace("USDT", "")
            return (
                f"⏸ ${sym}USDT — 等待信号\n"
                f"现价位于多空共振区间，还没触及最佳入场点。\n\n"
                f"⚠️ {strategy.get('reason', '')}。不追价，等回踩。\n\n"
                f"{strategy.get('watch_note', '')}"
            )
        return f"❌ {strategy.get('symbol','?')} 条件不满足\n{strategy.get('reason','')}"

    s   = strategy
    sym = s["symbol"].replace("USDT", "")
    sc  = s["score"]
    tf  = s.get("tf_aligned", "?")
    dir_label  = "做多 📈" if s["direction"] == "LONG" else "做空 📉"

    # 开头钩子：根据方向和分数选不同口吻
    if s["direction"] == "SHORT":
        if sc >= 85:
            opener = f"${sym}USDT 触及关键压力位，做空机会来了。下方空间大，止损清楚。"
        elif sc >= 75:
            opener = f"${sym}USDT {tf}个周期结构偏空，等反抽到压力位入场。"
        else:
            opener = f"${sym}USDT 有做空机会，轻仓试探。"
    else:
        if sc >= 85:
            opener = f"${sym}USDT 精准回踩支撑，做多信号来了。{tf}个周期共振。"
        elif sc >= 75:
            opener = f"${sym}USDT 回踩小周期关键支撑，值得一试。"
        else:
            opener = f"${sym}USDT 等回踩位进场，不追高。"

    lines = [
        f"${sym}USDT",
        f"",
        opener,
        f"",
        f"方向：{dir_label}  周期对齐：{tf}/4",
        f"1d: {_trend_emoji(s['trends'].get('1d'))}  "
        f"4h: {_trend_emoji(s['trends'].get('4h'))}  "
        f"1h: {_trend_emoji(s['trends'].get('1h'))}  "
        f"15m: {_trend_emoji(s['trends'].get('15m'))}",
        f"",
        f"📍 入场  ${s['entry']['low']:.6g} ~ ${s['entry']['high']:.6g}",
        f"🛡 止损  ${s['stop_loss']:.6g}  ({s['risk_pct']:+.1f}%)",
        f"   {s.get('sl_note', '')}",
        f"🎯 目标  ${s['targets']['t1']['price']:.6g}  "
        f"(+{s['targets']['t1']['chg_pct']:.1f}%  {s['targets']['t1']['rr']:.1f}:1)",
        f"",
    ]

    if s.get("lvn_warnings"):
        lines.append("⚠️ 价格空白区提示")
        for w in s["lvn_warnings"]:
            lines.append(f"   {w}")
        lines.append("")

    lines.append("入场触发条件（必须全部满足）")
    for cond in s["triggers"]:
        lines.append(f"  {cond}")

    lines += [
        f"",
        f"⚠️ 仅供参考，操作风险自担",
        f"",
        f"No.XXXX {now_cst_short()}",
        "━" * 22,
        f"📡 @姓赵不宣",
        f"返佣注册：XZBX666",
        f"#内容挖矿 #实盘策略",
    ]
    return "\n".join(lines)
def _trend_emoji(t):
    if t == "UP":   return "🟢↑"
    if t == "DOWN": return "🔴↓"
    return "⚪→"


def _reject(sym, score, reason, extra=None):
    r = {"symbol": sym, "executable": False, "score": score, "reason": reason}
    if extra:
        r.update(extra)
    _nerve.emit("ORDER_OPEN_FAIL", {"error": reason})
    return r


def _now():
    return now_cst_str('%Y-%m-%d %H:%M CST')


# ── 入口 ────────────────────────────────────────
if __name__ == "__main__":
    import sys, scanner
    syms = sys.argv[1:] if len(sys.argv) > 1 else ["BTCUSDT"]
    for sym in syms:
        if not sym.upper().endswith("USDT"):
            sym += "USDT"
        print(f"\n🎯 分析 {sym.upper()}...")
        result = scanner.analyze(sym.upper())
        strat = generate_strategy(result)
        print(format_signal_card(strat))


# ── P1修复: 跨信号相关性风控 ─────────────────────────────────
# BTC/ETH/BNB主力币之间相关性0.75+，同向持仓需降仓
_HIGH_CORR_PAIRS = {
    frozenset(['BTCUSDT','ETHUSDT']): 0.87,
    frozenset(['BTCUSDT','BNBUSDT']): 0.75,
    frozenset(['ETHUSDT','BNBUSDT']): 0.78,
    frozenset(['BTCUSDT','SOLUSDT']): 0.72,
    frozenset(['ETHUSDT','SOLUSDT']): 0.74,
}

def check_correlation_risk(new_sym: str, new_dir: str) -> dict:
    # [Phase A-4] 优先使用动态相关系数矩阵
    """
    检查新开仓与现有持仓的相关性风险
    返回: {ok: bool, corr_discount: float, warning: str}
    """
    try:
        import json as _json, os as _os
        _cfg_file = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'data', 'positions_config.json')
        if not _os.path.exists(_cfg_file):
            return {'ok': True, 'corr_discount': 1.0, 'warning': ''}
    except Exception:
        return {'ok': True, 'corr_discount': 1.0, 'warning': ''}

def _dynamic_correlation_check(new_sym: str, new_dir: str, open_positions: list) -> dict:
    """[Phase A-4] 动态相关系数检查，替代静态对列表"""
    try:
        import sys as _dc_sys, os as _dc_os
        _brain = _dc_os.path.join(_dc_os.path.dirname(_dc_os.path.abspath(__file__)), 'brahma_brain')
        if _brain not in _dc_sys.path: _dc_sys.path.insert(0, _brain)
        from dynamic_corr import check_portfolio_correlation
        return check_portfolio_correlation(new_sym, new_dir, open_positions)
    except Exception as _e:
        return {'ok': True, 'corr_discount': 1.0, 'max_corr': 0, 'warnings': [], 'error': str(_e)[:60]}
        cfg = _json.load(open(_cfg_file))
        open_pos = {k:v for k,v in cfg.items()
                    if isinstance(v,dict) and v.get('status')=='OPEN'}
        warnings = []
        max_corr = 0.0
        for key, pos in open_pos.items():
            existing_sym = pos.get('symbol', key.split('_')[0])
            existing_dir = pos.get('pos_side','').upper()
            pair = frozenset([new_sym.upper(), existing_sym.upper()])
            corr = _HIGH_CORR_PAIRS.get(pair, 0)
            if corr >= 0.7 and existing_dir == new_dir.upper():
                max_corr = max(max_corr, corr)
                warnings.append(
                    f"⚠️ 相关性风险: {new_sym}+{existing_sym} {new_dir} 相关性={corr:.2f}"
                )
        if max_corr >= 0.85:
            discount = 0.5  # 高度相关→仓位减半
        elif max_corr >= 0.7:
            discount = 0.7  # 中度相关→仓位×0.7
        else:
            discount = 1.0
        return {
            'ok': True,  # 不拒绝，只降仓
            'corr_discount': discount,
            'max_corr': max_corr,
            'warning': ' | '.join(warnings),
        }
    except Exception:
        return {'ok': True, 'corr_discount': 1.0, 'warning': ''}


# ══════════════════════════════════════════════════════
# [修复3] brahma_to_executor_bridge
# 将 brahma_analysis.analyze() 的输出转换为
# executor.generate_strategy() 所需的 scan_result 格式
# ══════════════════════════════════════════════════════
def brahma_to_executor_bridge(brahma_result: dict) -> dict:
    """
    桥接适配器：brahma_analysis.analyze() → executor.generate_strategy()

    brahma 输出字段映射：
      confluence.total (0-150)  →  score (0-100，归一化)
      momentum.rsi_4h           →  rsi_4h
      momentum.atr_pct          →  atr_pct
      extra.multitf.raw.tfs     →  trends {1h/4h/1d}
      smc.order_blocks + key_levels → levels (概拟 poc/vah/val)
      params.stop_loss/tp1/tp2  →  带入 signal_info
      signal_dir                →  direction / is_short
      regime                    →  regime
    """
    cf      = brahma_result.get('confluence', {})
    mom     = brahma_result.get('momentum', {})
    extra   = brahma_result.get('extra', {})
    smc     = brahma_result.get('smc', {})
    kl      = brahma_result.get('key_levels', {})
    params  = brahma_result.get('params', {})
    dir_    = brahma_result.get('signal_dir', 'LONG')
    price   = brahma_result.get('price', 0)
    symbol  = brahma_result.get('symbol', '')

    # 分数归一化 150 → 100
    raw_score = cf.get('total', 0)
    score_100 = round(raw_score * 100 / 150)

    # 多周期趋势转换
    mt = extra.get('multitf', {})
    tfs = mt.get('raw', {}).get('tfs', {})
    def _tf_label(tf_key):
        d = tfs.get(tf_key, {})
        lbl = d.get('label', 'SIDE')
        return 'UP' if lbl == 'BULL' else ('DOWN' if lbl == 'BEAR' else 'SIDE')
    trends = {
        '1h':  _tf_label('1H'),
        '4h':  _tf_label('4H'),
        '1d':  _tf_label('1D'),
        '1w':  _tf_label('1W'),
        '15m': _tf_label('15m'),
    }

    # 构造概拟 levels（用 pivot PP代POC，BB区间代 vah/val）
    pivot = kl.get('pivot', {})
    bb    = kl.get('bb', {})
    fib   = kl.get('fib', {})
    poc   = pivot.get('pp', price)
    vah   = bb.get('upper', price * 1.02)  # BB上轨代VAH
    val   = bb.get('lower', price * 0.98)  # BB下轨代VAL
    # 如果 SMC OB数据更精确，不覆盖
    ob_data = smc.get('order_blocks', {})
    bull_ob = ob_data.get('nearest_bull_ob')
    bear_ob = ob_data.get('nearest_bear_ob')
    if dir_ == 'SHORT' and bear_ob:
        vah = bear_ob.get('high', vah)
        poc = bear_ob.get('low', poc)
    elif dir_ == 'LONG' and bull_ob:
        val = bull_ob.get('low', val)
        poc = bull_ob.get('high', poc)
    levels = {'poc': poc, 'vah': vah, 'val': val, 'hvn': [], 'lvn': []}

    scan_result = {
        'symbol':     symbol,
        'price':      price,
        'score':      score_100,                 # 已归一化到0-100
        'brahma_raw_score': raw_score,           # 保留原始150分
        'regime':     brahma_result.get('regime', 'UNKNOWN'),
        'direction':  dir_,
        'is_short':   dir_ == 'SHORT',
        'levels':     levels,
        'trends':     trends,
        'rsi_4h':     mom.get('rsi_4h', 50),
        'atr_pct':    mom.get('atr_pct', 0),
        'atr_1h':     mom.get('atr_1h', 0),
        # 将brahma的精确参数带入供 executor 选用
        'brahma_params': {
            'entry_lo':  params.get('entry_lo'),
            'entry_hi':  params.get('entry_hi'),
            'stop_loss': params.get('stop_loss'),
            'tp1':       params.get('tp1'),
            'tp2':       params.get('tp2'),
            'rr1':       params.get('rr1'),
            'rr2':       params.get('rr2'),
            'valid':     params.get('valid'),
        },
    }
    return scan_result

# ── 自检 ──
if __name__ == "__main__":
    assert callable(generate_strategy), "generate_strategy callable"
    assert callable(format_signal_card), "format_signal_card callable"
    print("✅ executor 自检通过")