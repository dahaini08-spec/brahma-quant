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
# 规则7：资金费率极值 — 40年老炮：费率极值=拥挤度=反向信号
# ═══════════════════════════════════════════════════════════
def _rule_funding_rate(result: dict, extra: dict, ms: dict) -> dict:
    """
    40年老炮：资金费率>0.1%连续8h=多头拥挤做空。
    资金费率<-0.05%连续8h=空头拥挤做多。
    费率是合约交易的核心成本指标。
    """
    sym = ms.get('symbol', 'BTC')
    sym_usdt = f'{sym}USDT'
    fr = _load_json(DATA_DIR / f'funding_rate_{sym_usdt.lower()}')
    if not fr or fr.get('ts', 0) == 0:
        return {'id': 'VR07_资金费率极值', 'fired': False, 'score_adj': 0, 'flags': []}

    current_rate = fr.get('current_rate', 0)
    consec_pos_h = fr.get('consecutive_positive_h', 0)
    consec_neg_h = fr.get('consecutive_negative_h', 0)
    rate_8h_avg = fr.get('rate_8h_avg', 0)
    
    flags = []
    score_adj = 0
    
    # 多头拥挤: 费率>0.1%连续8h+
    if current_rate > 0.001 and consec_pos_h >= 8:
        score_adj += 5
        flags.append(f'资金费率{current_rate*100:.4f}%连续{consec_pos_h}h正→多头拥挤做空+5')
        if result.get('signal_dir') == 'SHORT':
            score_adj += 2
            flags.append('做空+多头拥挤=顺势+2')
    # 空头拥挤: 费率<-0.05%连续8h+
    elif current_rate < -0.0005 and consec_neg_h >= 8:
        score_adj += 5
        flags.append(f'资金费率{current_rate*100:.4f}%连续{consec_neg_h}h负→空头拥挤做多+5')
        if result.get('signal_dir') == 'LONG':
            score_adj += 2
            flags.append('做多+空头拥挤=顺势+2')
    # 中性区间
    else:
        rate_pct = current_rate * 100
        if abs(rate_pct) < 0.01:
            flags.append(f'资金费率{rate_pct:.4f}%→中性区间')
        elif rate_pct > 0.01:
            flags.append(f'资金费率{rate_pct:.4f}%→轻微多头成本')
        else:
            flags.append(f'资金费率{rate_pct:.4f}%→轻微空头成本')
    
    return {
        'id': 'VR07_资金费率极值',
        'fired': True,
        'score_adj': score_adj,
        'flags': flags,
    }


# ═══════════════════════════════════════════════════════════
# 规则8：爆仓反转 — 40年老炮：爆仓后1-2h是最佳反向入场窗口
# ═══════════════════════════════════════════════════════════
def _rule_liquidation_reversal(result: dict, extra: dict, ms: dict) -> dict:
    """
    40年老炮：24h爆仓>$500M+恐贪<30=底部反转做多。
    24h爆仓>$500M+恐贪>70=顶部反转做空。
    爆仓量是猎杀完成的确认信号。
    """
    sym = ms.get('symbol', 'BTC')
    sym_usdt = f'{sym}USDT'
    liq = _load_json(DATA_DIR / f'liquidation_{sym_usdt.lower()}')
    if not liq or liq.get('total_24h_usd', 0) == 0:
        # 无数据=不触发，不影响主链
        return {'id': 'VR08_爆仓反转', 'fired': False, 'score_adj': 0, 'flags': ['爆仓量数据不可用→跳过']}
    
    total_24h = liq.get('total_24h_usd', 0)
    long_24h = liq.get('long_24h_usd', 0)
    short_24h = liq.get('short_24h_usd', 0)
    ratio = liq.get('long_short_ratio', 1)
    
    flags = []
    score_adj = 0
    
    # 大规模爆仓检测
    if total_24h > 500_000_000:  # >$500M
        if long_24h > short_24h * 2:
            # 多单爆仓远大于空单 → 底部反转
            score_adj += 5
            flags.append(f'24h多单爆仓${long_24h/1e6:.0f}M>>空单${short_24h/1e6:.0f}M→底部反转做多+5')
        elif short_24h > long_24h * 2:
            # 空单爆仓远大于多单 → 顶部反转
            score_adj += 5
            flags.append(f'24h空单爆仓${short_24h/1e6:.0f}M>>多单${long_24h/1e6:.0f}M→顶部反转做空+5')
        else:
            flags.append(f'24h爆仓${total_24h/1e6:.0f}M但多空接近→无方向性信号')
    elif total_24h > 100_000_000:  # >$100M
        flags.append(f'24h爆仓${total_24h/1e6:.0f}M→中等规模')
    else:
        flags.append(f'24h爆仓${total_24h/1e6:.1f}M→正常水平')
    
    return {
        'id': 'VR08_爆仓反转',
        'fired': True,
        'score_adj': score_adj,
        'flags': flags,
    }


# ═══════════════════════════════════════════════════════════
# 规则9：盘口吸筹 — 40年老炮：卖盘>买盘2倍但价格不跌=主力吸筹
# ═══════════════════════════════════════════════════════════
def _rule_orderbook_absorption(result: dict, extra: dict, ms: dict) -> dict:
    """
    40年老炮：盘口卖盘>买盘2倍但价格不跌=主力在吸筹做多。
    盘口买盘>卖盘2倍但价格不涨=主力在派发做空。
    这是"看庄家底牌"的核心维度。
    """
    sym = ms.get('symbol', 'BTC')
    sym_usdt = f'{sym}USDT'
    ob = _load_json(DATA_DIR / f'orderbook_{sym_usdt.lower()}')
    if not ob or ob.get('ts', 0) == 0:
        return {'id': 'VR09_盘口吸筹', 'fired': False, 'score_adj': 0, 'flags': ['盘口数据不可用→跳过']}
    
    imbalance = ob.get('imbalance', 0.5)  # >0.5=卖盘多
    bid_ask_ratio = ob.get('bid_ask_ratio', 1.0)
    wall_bid = ob.get('wall_bid', 0)
    wall_ask = ob.get('wall_ask', 0)
    
    flags = []
    score_adj = 0
    
    # 卖盘远大于买盘=潜在吸筹
    if imbalance > 0.67:  # 卖盘占2/3以上
        score_adj += 3
        flags.append(f'盘口卖盘占{imbalance*100:.0f}%→卖压>买盘{bid_ask_ratio:.1f}倍→潜在吸筹做多+3')
        if result.get('signal_dir') == 'LONG':
            score_adj += 2
            flags.append('做多+盘口吸筹=主力在买入+2')
    # 买盘远大于卖盘=潜在派发
    elif imbalance < 0.33:  # 买盘占2/3以上
        score_adj += 3
        flags.append(f'盘口买盘占{(1-imbalance)*100:.0f}%→买压>卖盘{1/bid_ask_ratio:.1f}倍→潜在派发做空+3')
        if result.get('signal_dir') == 'SHORT':
            score_adj += 2
            flags.append('做空+盘口派发=主力在卖出+2')
    else:
        flags.append(f'盘口平衡(卖盘{imbalance*100:.0f}%/买盘{(1-imbalance)*100:.0f}%)')
    
    # 盘口墙检测
    if wall_ask > wall_bid * 2 and wall_ask > 0:
        flags.append(f'卖盘墙${wall_ask:,.0f}>>买盘墙${wall_bid:,.0f}→上方阻力强')
    elif wall_bid > wall_ask * 2 and wall_bid > 0:
        flags.append(f'买盘墙${wall_bid:,.0f}>>卖盘墙${wall_ask:,.0f}→下方支撑强')
    
    return {
        'id': 'VR09_盘口吸筹',
        'fired': True,
        'score_adj': score_adj,
        'flags': flags,
    }


# ═══════════════════════════════════════════════════════════
# 规则10：基差极端值 — 40年老炮：基差>1%=逼空 / <-0.5%=恐慌
# ═══════════════════════════════════════════════════════════
def _rule_basis_extreme(result: dict, extra: dict, ms: dict) -> dict:
    """
    40年老炮：永续vs现货基差是市场情绪温度计。
    基差>1%且持续=逼空行情(空头被迫平仓推高价格)。
    基差<-0.5%=恐慌性做空(市场极度悲观)。
    """
    # 从extra_data获取基差（由Step0计算）
    basis_pct = extra.get('basis_pct', 0)
    if basis_pct == 0:
        return {'id': 'VR10_基差极端值', 'fired': False, 'score_adj': 0, 'flags': ['基差数据不可用→跳过']}
    
    flags = []
    score_adj = 0
    
    if basis_pct > 1.0:
        score_adj += 3
        flags.append(f'基差{basis_pct:+.2f}%>1%→逼空行情→做空+3(逼空将反转)')
        if result.get('signal_dir') == 'SHORT':
            score_adj += 2
            flags.append('做空+逼空反转=顺势+2')
    elif basis_pct < -0.5:
        score_adj += 3
        flags.append(f'基差{basis_pct:+.2f}%<-0.5%→恐慌性做空→做多+3(恐慌将反转)')
        if result.get('signal_dir') == 'LONG':
            score_adj += 2
            flags.append('做多+恐慌反转=顺势+2')
    else:
        flags.append(f'基差{basis_pct:+.2f}%→正常区间')
    
    return {
        'id': 'VR10_基差极端值',
        'fired': True,
        'score_adj': score_adj,
        'flags': flags,
    }


# ═══════════════════════════════════════════════════════════
# 规则11：多TF收敛 — 40年老炮：三周期同向=最高概率交易
# ═══════════════════════════════════════════════════════════
def _rule_multi_tf_convergence(result: dict, extra: dict, ms: dict) -> dict:
    """
    40年老炮：4H+1H+15M趋势同向=最强趋势信号。
    4H+1H同向但15M反向=回调入场机会。
    三周期收敛+共振=接近确定性交易。
    """
    # 从extra_data获取多TF方向（由Step1计算）
    tf_4h = extra.get('tf_4h_dir', '')
    tf_1h = extra.get('tf_1h_dir', '')
    tf_15m = extra.get('tf_15m_dir', '')
    
    if not tf_4h or not tf_1h:
        return {'id': 'VR11_多TF收敛', 'fired': False, 'score_adj': 0, 'flags': ['多TF数据不可用→跳过']}
    
    flags = []
    score_adj = 0
    signal = result.get('signal_dir', '')
    
    # 三周期同向
    if tf_4h == tf_1h == tf_15m and tf_4h:
        score_adj += 5
        dir_cn = '做多' if tf_4h == 'LONG' else '做空'
        flags.append(f'4H+1H+15M三周期同向{dir_cn}→最高概率交易+5')
        if signal == tf_4h:
            score_adj += 2
            flags.append(f'信号与三周期一致=顺势+2')
    # 4H+1H同向，15M反向
    elif tf_4h == tf_1h and tf_4h != tf_15m and tf_4h:
        score_adj += 2
        flags.append(f'4H+1H同向但15M反向→回调入场机会+2')
        if signal == tf_4h:
            score_adj += 1
            flags.append('信号与大周期一致=顺势+1')
    # 4H与1H反向
    elif tf_4h != tf_1h:
        flags.append(f'4H({tf_4h})与1H({tf_1h})反向→趋势不一致，谨慎')
    else:
        flags.append(f'4H={tf_4h} 1H={tf_1h} 15M={tf_15m}→无明确收敛')
    
    return {
        'id': 'VR11_多TF收敛',
        'fired': True,
        'score_adj': score_adj,
        'flags': flags,
    }


# ═══════════════════════════════════════════════════════════
# 规则12：恐贪极端值 — 40年老炮：恐贪+OI+价格=顶/底信号
# ═══════════════════════════════════════════════════════════
def _rule_fear_greed_extreme(result: dict, extra: dict, ms: dict) -> dict:
    """
    40年老炮：恐贪>80+OI下降+价格滞涨=顶部。
    恐贪<20+OI上升+价格止跌=底部。
    恐贪极值+OI方向=反转确认。
    """
    # 从extra_data获取恐贪指数（由Step8 macro提供）
    fg = extra.get('fear_greed', 0) or ms.get('fear_greed', 0)
    if fg == 0:
        return {'id': 'VR12_恐贪极端值', 'fired': False, 'score_adj': 0, 'flags': ['恐贪数据不可用→跳过']}
    
    oi_signal = ms.get('oi_signal', '') or extra.get('oi_signal', '')
    price_24h_change = extra.get('price_24h_change', 0)
    
    flags = []
    score_adj = 0
    
    if fg > 80:
        flags.append(f'恐贪{fg:.0f}>80→极度贪婪')
        if 'UNWIND' in str(oi_signal) or 'SHORT' in str(oi_signal):
            score_adj += 5
            flags.append(f'恐贪{fg:.0f}+OI{oi_signal}+价格24h={price_24h_change:+.1f}%→顶部信号做空+5')
    elif fg < 20:
        flags.append(f'恐贪{fg:.0f}<20→极度恐惧')
        if 'BUILD' in str(oi_signal) and 'LONG' in str(oi_signal):
            score_adj += 5
            flags.append(f'恐贪{fg:.0f}+OI{oi_signal}+价格24h={price_24h_change:+.1f}%→底部信号做多+5')
    else:
        flags.append(f'恐贪{fg:.0f}→中性区间')
    
    return {
        'id': 'VR12_恐贪极端值',
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
        _rule_funding_rate,
        _rule_liquidation_reversal,
        _rule_orderbook_absorption,
        _rule_basis_extreme,
        _rule_multi_tf_convergence,
        _rule_fear_greed_extreme,
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

    # 总分限制：±20分（12规则升级后）
    total_adj = max(-20, min(20, total_adj))

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
