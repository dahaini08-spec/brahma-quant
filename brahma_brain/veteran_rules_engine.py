"""
veteran_rules_engine.py — 40年合约实战经验规则引擎
[2026-09-22 苏摩111封印] 将口头实战经验固化为代码硬规则

接入位置：brahma_core.py confluence_score() Step9风控后、trader_brain前
调用方式：
    from brahma_brain.veteran_rules_engine import evaluate_veteran_rules
    _vet = evaluate_veteran_rules(result, extra_data, ms)
    result['confluence']['breakdown']['veteran_rules'] = _vet['summary']
    score += _vet['score_adj']

设计原则：
  - 纯规则引擎，不做预测，只做已发生事实的判断
  - 每条规则有唯一ID，可追溯，可开关
  - 输出score_adj + risk_flags + breakdown
  - <300行（轻量）
"""
import json, sys, time
from pathlib import Path
from typing import Dict, Any, List

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / 'data'

# 规则配置（可热更新）
RULES_CONFIG_FILE = DATA_DIR / 'veteran_rules_config.json'
_config_cache = None
_config_mtime = 0.0


def _load_config() -> dict:
    """加载规则配置，带热更新"""
    global _config_cache, _config_mtime
    try:
        mtime = RULES_CONFIG_FILE.stat().st_mtime
        if mtime != _config_mtime:
            _config_cache = json.loads(RULES_CONFIG_FILE.read_text())
            _config_mtime = mtime
    except Exception:
        if _config_cache is None:
            _config_cache = {'rules_enabled': True, 'debug': False}
    return _config_cache


def _safe_get(d: dict, *keys, default=None):
    """安全嵌套取值"""
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k, default)
        else:
            return default
    return d


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════
# 规则1：GEX铁笼规则 — 正GEX>90% + 价格在Max Strike附近 = 抑制波动
# ═══════════════════════════════════════════════════════════
def _rule_gex_cage(result: dict, extra: dict, ms: dict) -> dict:
    """
    40年老炮：做市商正GEX>90% = 铁笼，价格被钉死，突破需要外力。
    做空在Max GEX附近挂 = 站在做市商同一边。
    """
    gex = _load_json(DATA_DIR / 'gex_state.json')
    sym = ms.get('symbol', 'BTC').replace('USDT', '').upper()
    g = gex.get(sym, {})
    if not g:
        return {'id': 'VR01', 'fired': False, 'score_adj': 0}

    pos_gex = g.get('total_positive_gex', 0)
    neg_gex = abs(g.get('total_negative_gex', 1))
    total = pos_gex + neg_gex
    pos_ratio = pos_gex / total if total > 0 else 0
    max_strike = g.get('max_gex_strike', 0)
    spot = g.get('spot', 0)
    dist_max = g.get('dist_to_max_pct', 999)
    zero_flip_dist = (g.get('zero_flip', 0) - spot) / spot * 100 if spot else 999

    flags = []
    score_adj = 0

    # 铁笼检测
    if pos_ratio > 0.90:
        flags.append('GEX铁笼(>90%正gamma)')
        # 做空在Max GEX附近 = 做市商兜底
        if result.get('signal_dir') == 'SHORT' and abs(dist_max) < 5:
            score_adj += 5
            flags.append(f'做空在磁铁${max_strike:,.0f}附近(+{dist_max:.1f}%)→GEX兜底+5')
        # 做多在铁笼区间 = 被钉死
        if result.get('signal_dir') == 'LONG' and abs(dist_max) < 5:
            score_adj -= 5
            flags.append(f'铁笼区间做多=被钉死-5')

    # 负GEX弹簧检测
    top_strikes = g.get('top_strikes', {})
    neg_walls = {k: v for k, v in top_strikes.items() if v < 0}
    for strike, gex_val in neg_walls.items():
        strike_price = float(strike)
        dist = (strike_price - spot) / spot * 100 if spot else 999
        if abs(dist) < 3:  # 3%内有负GEX墙
            flags.append(f'负GEX墙${strike_price:,.0f}({dist:+.1f}%)=弹簧待释放')
            if result.get('signal_dir') == 'SHORT':
                score_adj += 3  # 做空有弹簧加速
            elif result.get('signal_dir') == 'LONG':
                score_adj -= 3  # 做多有弹簧风险

    # ZeroFlip安全垫
    if abs(zero_flip_dist) > 15:
        flags.append(f'ZeroFlip{zero_flip_dist:+.1f}%→安全垫厚')
    elif abs(zero_flip_dist) < 5:
        flags.append(f'⚠️ZeroFlip{zero_flip_dist:+.1f}%→接近翻转点')

    return {
        'id': 'VR01_GEX铁笼',
        'fired': True,
        'score_adj': score_adj,
        'flags': flags,
    }


# ═══════════════════════════════════════════════════════════
# 规则2：CVD+OI双确认规则 — 同向=真信号，矛盾=假信号
# ═══════════════════════════════════════════════════════════
def _rule_cvd_oi_confirm(result: dict, extra: dict, ms: dict) -> dict:
    """
    40年老炮：CVD+OI同向做空=双确认做空，最可靠的信号。
    CVD多但OI空=矛盾=空头回补非真实买入=上行有限。
    """
    sym = ms.get('symbol', 'BTC').replace('USDT', '').upper()
    cvd_data = _load_json(DATA_DIR / f'cvd_realtime_{sym.lower()}usdt.json')
    cvd_1h = cvd_data.get('cvd_1h', 0)
    oi_signal = ms.get('oi_signal', '') or _safe_get(extra, 'oi_signal', default='')

    flags = []
    score_adj = 0

    if 'SHORT_BUILD' in str(oi_signal):
        if cvd_1h < 0:
            score_adj += 5
            flags.append(f'CVD+OI双确认做空(CVD={cvd_1h:.0f}+OI=SHORT_BUILD)→真信号+5')
        else:
            score_adj -= 3
            flags.append(f'CVD多但OI空=矛盾(CVD={cvd_1h:.0f}+OI=SHORT_BUILD)→可能是空头回补-3')
    elif 'LONG_UNWIND' in str(oi_signal):
        if cvd_1h < 0:
            score_adj += 3
            flags.append(f'CVD+OI做空一致(LONG_UNWIND+CVD={cvd_1h:.0f})→+3')

    return {
        'id': 'VR02_CVD_OI双确认',
        'fired': bool(flags),
        'score_adj': score_adj,
        'flags': flags,
    }


# ═══════════════════════════════════════════════════════════
# 规则3：聪明钱猎杀规则 — 散户>大户=多头陷阱，大户>散户=主力在买
# ═══════════════════════════════════════════════════════════
def _rule_smart_money_hunt(result: dict, extra: dict, ms: dict) -> dict:
    """
    40年老炮：散户70%做多>大户61%做多=多头陷阱，散户是猎物。
    大户69%多>散户47%多=主力在买，散户在空，看多。
    """
    big_pct = ms.get('big_long_pct', 0) or _safe_get(extra, 'big_long_pct', default=0)
    retail_pct = ms.get('retail_long_pct', 0) or _safe_get(extra, 'retail_long_pct', default=0)

    flags = []
    score_adj = 0

    if big_pct > 0 and retail_pct > 0:
        spread = big_pct - retail_pct
        if retail_pct > big_pct and spread < -5:
            # 散户>大户 = 陷阱
            score_adj -= 5
            flags.append(f'散户{retail_pct:.0f}%>大户{big_pct:.0f}%=多头陷阱→散户是猎物-5')
            if result.get('signal_dir') == 'SHORT':
                score_adj += 3
                flags.append(f'做空+散户陷阱=顺势猎杀+3')
        elif big_pct > retail_pct and spread > 15:
            # 大户>>散户 = 主力在买
            score_adj += 3
            flags.append(f'大户{big_pct:.0f}%>>散户{retail_pct:.0f}%(分歧{spread:.1f}%)=主力在买+3')
            if result.get('signal_dir') == 'LONG':
                score_adj += 2
                flags.append(f'做多+主力在买=顺势+2')

    return {
        'id': 'VR03_聪明钱猎杀',
        'fired': bool(flags),
        'score_adj': score_adj,
        'flags': flags,
    }


# ═══════════════════════════════════════════════════════════
# 规则4：区间策略规则 — 止损墙做空+支撑池接多=逻辑闭环
# ═══════════════════════════════════════════════════════════
def _rule_range_logic(result: dict, extra: dict, ms: dict) -> dict:
    """
    40年老炮：止损墙做空→支撑池止盈→同一位置接多=区间策略逻辑闭环。
    上空下多，GEX铁笼兜上方，支撑池兜下方。
    """
    liq = _load_json(DATA_DIR / f'liq_heatmap_{ms.get("symbol","BTCUSDT").lower()}.json')
    stop_wall = liq.get('nearest_short_liq', 0)
    support_pool = liq.get('nearest_long_liq', 0)
    price = liq.get('price', 0)

    flags = []
    score_adj = 0

    if stop_wall and support_pool and price:
        range_pct = (stop_wall - support_pool) / price * 100
        if range_pct < 6:  # 合理区间
            if result.get('signal_dir') == 'SHORT':
                dist_to_wall = (stop_wall - price) / price * 100
                if dist_to_wall < 3 and dist_to_wall > 0:
                    score_adj += 3
                    flags.append(f'做空在止损墙${stop_wall:,.0f}附近({dist_to_wall:+.1f}%)→区间上沿做空+3')
            elif result.get('signal_dir') == 'LONG':
                dist_to_pool = (support_pool - price) / price * 100
                if dist_to_pool > -3 and dist_to_pool < 0:
                    score_adj += 3
                    flags.append(f'做多在支撑池${support_pool:,.0f}附近({dist_to_pool:+.1f}%)→区间下沿接多+3')
            flags.append(f'区间${support_pool:,.0f}~${stop_wall:,.0f}({range_pct:.1f}%)→上空下多')

    return {
        'id': 'VR04_区间策略',
        'fired': bool(flags),
        'score_adj': score_adj,
        'flags': flags,
    }


# ═══════════════════════════════════════════════════════════
# 规则5：期权到期日规则 — 到期前48h GEX墙变薄=突破窗口
# ═══════════════════════════════════════════════════════════
def _rule_expiry_window(result: dict, extra: dict, ms: dict) -> dict:
    """
    40年老炮：GEX数据在期权到期前48小时效力最强。
    做市商到期日逼近被迫平仓 → GEX墙变薄 → 价格可能穿透。
    """
    # Deribit期权到期：每月最后一个周五 08:00 UTC
    # 简单计算：当前日期距月末周五的天数
    from datetime import datetime, timezone, timedelta
    import calendar

    now = datetime.now(timezone.utc)
    # 找本月最后一个周五
    _, last_day = calendar.monthrange(now.year, now.month)
    last_date = datetime(now.year, now.month, last_day, 8, 0, tzinfo=timezone.utc)
    while last_date.weekday() != 4:  # 周五=4
        last_date -= timedelta(days=1)

    days_to_expiry = (last_date - now).total_seconds() / 86400

    flags = []
    score_adj = 0

    if 0 < days_to_expiry < 2:
        flags.append(f'期权到期{days_to_expiry:.1f}天后→GEX墙变薄→突破窗口')
        if result.get('signal_dir') == 'LONG':
            score_adj += 3
            flags.append('做多+到期日窗口=突破概率上升+3')
    elif 0 < days_to_expiry < 7:
        flags.append(f'期权到期{days_to_expiry:.1f}天后→GEX墙开始衰减')
    else:
        flags.append(f'期权到期{days_to_expiry:.1f}天后→GEX墙全效')

    return {
        'id': 'VR05_期权到期日',
        'fired': True,
        'score_adj': score_adj,
        'flags': flags,
    }


# ═══════════════════════════════════════════════════════════
# 规则6：流动性时段规则 — 亚洲盘低流动性=减仓
# ═══════════════════════════════════════════════════════════
def _rule_session_liquidity(result: dict, extra: dict, ms: dict) -> dict:
    """
    40年老炮：亚洲盘流动性低，增强减分。
    欧洲盘开盘流动性恢复，美洲盘最强。
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    hour = now.hour

    flags = []
    score_adj = 0

    if 0 <= hour < 8:
        score_adj -= 2
        flags.append('亚洲盘(0-8UTC)低流动性→减仓-2')
    elif 8 <= hour < 13:
        score_adj += 2
        flags.append('欧洲盘(8-13UTC)流动性恢复+2')
    elif 13 <= hour < 21:
        score_adj += 3
        flags.append('美洲盘(13-21UTC)流动性最强+3')
    else:
        flags.append('收盘时段(21-24UTC)流动性下降')

    return {
        'id': 'VR06_流动性时段',
        'fired': True,
        'score_adj': score_adj,
        'flags': flags,
    }


# ═══════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════
def evaluate_veteran_rules(result: dict, extra_data: dict = None, ms: dict = None) -> dict:
    """
    评估所有实战经验规则，返回综合调整。

    返回:
        {
            'score_adj': int,        # 总分调整
            'risk_flags': list,      # 风险标记列表
            'rules_fired': list,     # 触发的规则列表
            'summary': str,          # 一行摘要
            'breakdown': dict,       # 逐规则详情
        }
    """
    config = _load_config()
    if not config.get('rules_enabled', True):
        return {'score_adj': 0, 'risk_flags': [], 'rules_fired': [], 'summary': '规则引擎已禁用', 'breakdown': {}}

    extra = extra_data or {}
    ms = ms or {}

    rules = [
        _rule_gex_cage,
        _rule_cvd_oi_confirm,
        _rule_smart_money_hunt,
        _rule_range_logic,
        _rule_expiry_window,
        _rule_session_liquidity,
    ]

    total_adj = 0
    all_flags = []
    fired_rules = []
    breakdown = {}

    for rule_fn in rules:
        try:
            r = rule_fn(result, extra, ms)
            if r.get('fired'):
                total_adj += r['score_adj']
                all_flags.extend(r.get('flags', []))
                fired_rules.append(r['id'])
                breakdown[r['id']] = {
                    'score_adj': r['score_adj'],
                    'flags': r['flags'],
                }
        except Exception as e:
            breakdown[f'ERROR_{rule_fn.__name__}'] = str(e)

    # 总分限制：±15分
    total_adj = max(-15, min(15, total_adj))

    summary = f'40年经验: {" | ".join(all_flags[:4])}'
    if len(all_flags) > 4:
        summary += f' | +{len(all_flags)-4}项'

    return {
        'score_adj': total_adj,
        'risk_flags': [f for f in all_flags if '⚠️' in f or '陷阱' in f or '矛盾' in f],
        'rules_fired': fired_rules,
        'summary': summary,
        'breakdown': breakdown,
    }


if __name__ == '__main__':
    # 自测
    test_result = {'signal_dir': 'SHORT', 'regime': 'CHOP_MID'}
    test_ms = {'symbol': 'BTC', 'big_long_pct': 68, 'retail_long_pct': 47}
    r = evaluate_veteran_rules(test_result, {}, test_ms)
    print(json.dumps(r, indent=2, ensure_ascii=False))
