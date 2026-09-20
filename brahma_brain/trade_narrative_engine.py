#!/usr/bin/env python3
"""
交易叙事引擎 — 40年实战合约交易分析师的灵魂
[V2.0 2026-09-20 苏摩111]

根因：当前梵天输出是数据报告，不是交易观点
方案：把94维数据编织成因果链交易叙事

40年经验 = 模式识别 + 因果推理 + 博弈判断
交易叙事五步：
1. 这是什么市场？（体制+Hurst+GEX）
2. 主力在哪？（FVG+OB+清算）
3. 钱在往哪流？（OI+CVD+聪明钱）
4. 谁是猎物？（大户vs散户分歧）
5. 我在哪入场？（共振点+入场区+SL）
"""

from typing import Dict, Any, Optional


def generate_trade_narrative(analysis_result: dict) -> str:
    """
    输入：brahma_core的完整分析结果
    输出：交易叙事（有因果链、有入场点、有止损、有逻辑）
    
    不是罗列数据，而是用因果链串联数据：
    - "因为GEX+13.5M正gamma压制波动，所以散户觉得安全追多"
    - "因为大户68%多但OI全线撤退，所以主力在挂单不是在加仓"
    - "因为清算密集区在$81,500，所以主力会往那里诱多"
    """
    try:
        sym = analysis_result.get('symbol', '?')
        price = analysis_result.get('price', 0)
        regime = analysis_result.get('regime', '?')
        score = analysis_result.get('score', 0)
        hurst = analysis_result.get('hurst', 0)
        action = analysis_result.get('action', 'WAIT')
        direction = analysis_result.get('direction', 'NONE')
        
        # 提取各维度数据
        fvg = analysis_result.get('fvg_consensus', '')
        fvg_magnet = analysis_result.get('fvg_magnet_price', 0)
        oi_signal = analysis_result.get('oi_signal', '')
        smart_money = analysis_result.get('smart_money', {})
        sm_div = smart_money.get('divergence', 0) if isinstance(smart_money, dict) else 0
        sm_dir = smart_money.get('direction', '') if isinstance(smart_money, dict) else ''
        big_player_pct = smart_money.get('long_pct', 0) if isinstance(smart_money, dict) else 0
        retail_pct = smart_money.get('retail_long_pct', 0) if isinstance(smart_money, dict) else 0
        gex = analysis_result.get('gex', 0)
        gex_signal = analysis_result.get('gex_signal', '')
        cvd_1h = analysis_result.get('cvd_1h', 0)
        cvd_dir = analysis_result.get('cvd_dir_1h', 'NEUTRAL')
        resonance = analysis_result.get('resonance_count', 0)
        resonance_max = analysis_result.get('resonance_max', 7)
        resonance_missing = analysis_result.get('resonance_missing', [])
        liq_wall = analysis_result.get('liq_wall_price', 0)
        liq_pool = analysis_result.get('liq_pool_price', 0)
        
        # 入场参数
        entry_lo = analysis_result.get('entry_lo', 0)
        entry_hi = analysis_result.get('entry_hi', 0)
        sl = analysis_result.get('stop_loss', 0)
        sl_pct = analysis_result.get('sl_pct', 0)
        rr1 = analysis_result.get('rr1', 0)
        tp1 = analysis_result.get('tp1', 0)
        tp2 = analysis_result.get('tp2', 0)
        tp3 = analysis_result.get('tp3', 0)
        
        # === Step 1: 这是什么市场？ ===
        market_type = _classify_market(regime, hurst, gex)
        
        # === Step 2: 主力在哪？ ===
        main_force = _locate_main_force(fvg, fvg_magnet, liq_wall, liq_pool, resonance, resonance_missing)
        
        # === Step 3: 钱在往哪流？ ===
        money_flow = _trace_money_flow(oi_signal, cvd_1h, cvd_dir, sm_dir)
        
        # === Step 4: 谁是猎物？ ===
        hunt_target = _identify_prey(big_player_pct, retail_pct, sm_div, oi_signal, cvd_dir)
        
        # === Step 5: 我在哪入场？ ===
        trade_view = _form_trade_view(action, direction, entry_lo, entry_hi, sl, sl_pct, rr1, 
                                       tp1, tp2, tp3, resonance, market_type, main_force, money_flow, hunt_target)
        
        # === 编织叙事 ===
        narrative = f"""【{sym} ${price:,.0f} | {market_type['label']}】
{market_type['story']}

{main_force['story']}

{money_flow['story']}

{hunt_target['story']}

{trade_view['story']}"""
        
        return narrative.strip()
    
    except Exception as e:
        return f"[交易叙事引擎] 生成失败: {e}"


def _classify_market(regime, hurst, gex) -> dict:
    """Step 1: 这是什么市场？"""
    stories = []
    label_parts = []
    
    # 体制
    if 'BULL' in regime:
        label_parts.append('多头市场')
        stories.append(f"体制={regime}，多头主导，回调做多为主。")
    elif 'BEAR' in regime:
        label_parts.append('空头市场')
        stories.append(f"体制={regime}，空头主导，反弹做空为主。")
    elif 'CHOP' in regime:
        label_parts.append('震荡市场')
        stories.append(f"体制={regime}，多空均衡，等趋势确认。")
    else:
        label_parts.append('中性市场')
        stories.append(f"体制={regime}，方向不明。")
    
    # Hurst
    if hurst > 0.6:
        stories.append(f"Hurst={hurst:.2f}已进趋势区，趋势可能持续，顺势操作。")
    elif hurst > 0.55:
        stories.append(f"Hurst={hurst:.2f}趋势性隐现，接近突破临界点。")
    else:
        stories.append(f"Hurst={hurst:.2f}随机游走，趋势未确认，不追涨杀跌。")
    
    # GEX
    if gex > 0:
        stories.append(f"GEX=+{gex:.1f}M正gamma，做市商压制波动，价格被钉住，突破需外力。")
    elif gex < 0:
        stories.append(f"GEX={gex:.1f}M负gamma，波动率放大，做市商对冲加剧趋势，可能急涨急跌。")
    
    return {
        'label': ' · '.join(label_parts),
        'story': ' → '.join(stories),
    }


def _locate_main_force(fvg, fvg_magnet, liq_wall, liq_pool, resonance, missing) -> dict:
    """Step 2: 主力在哪？"""
    stories = []
    
    # FVG磁铁
    if 'BULL' in fvg:
        stories.append(f"FVG共识=BULL，磁铁向上吸引价格到${fvg_magnet:,.0f}，回调到位=多头入场。")
    elif 'BEAR' in fvg:
        stories.append(f"FVG共识=BEAR，磁铁向下吸引价格到${fvg_magnet:,.0f}，反弹到位=空头入场。")
    else:
        stories.append(f"FVG共识={fvg}，方向不明。")
    
    # 清算
    if liq_wall > 0:
        stories.append(f"上方空头止损墙${liq_wall:,.0f}，主力可能诱多到此触发清算瀑布。")
    if liq_pool > 0:
        stories.append(f"下方多头支撑池${liq_pool:,.0f}，主力可能诱空到此接筹。")
    if liq_wall == 0 and liq_pool == 0:
        stories.append("清算数据缺失，无法定位猎杀目标。")
    
    # 共振
    stories.append(f"共振{resonance}/7{'（不足）' if resonance < 5 else '（足够）'}，缺失：{', '.join(missing) if missing else '无'}。")
    
    return {'story': ' → '.join(stories)}


def _trace_money_flow(oi_signal, cvd_1h, cvd_dir, sm_dir) -> dict:
    """Step 3: 钱在往哪流？"""
    stories = []
    
    # OI
    if 'LONG_BUILD' in oi_signal:
        stories.append("OI多头在建仓，资金流入多单。")
    elif 'SHORT_BUILD' in oi_signal:
        stories.append("OI空头在建仓，资金流入空单。")
    elif 'LONG_UNWIND' in oi_signal:
        stories.append("OI多头在撤退，资金从多单流出——不是加仓，是等猎杀。")
    elif 'SHORT_UNWIND' in oi_signal:
        stories.append("OI空头在撤退，资金从空单流出。")
    
    # CVD
    if cvd_1h > 0:
        stories.append(f"CVD 1H=+{cvd_1h:.0f}（{cvd_dir}），买方主导，资金在买。")
    elif cvd_1h < 0:
        stories.append(f"CVD 1H={cvd_1h:.0f}（{cvd_dir}），卖方主导，资金在卖。")
    
    # 聪明钱
    if 'BULL' in sm_dir or 'LONG' in sm_dir:
        stories.append("聪明钱看多，大户在积累多单。")
    elif 'BEAR' in sm_dir or 'SHORT' in sm_dir:
        stories.append("聪明钱看空，大户在积累空单。")
    
    return {'story': ' → '.join(stories)}


def _identify_prey(big_pct, retail_pct, divergence, oi_signal, cvd_dir) -> dict:
    """Step 4: 谁是猎物？"""
    stories = []
    
    if divergence > 15:
        stories.append(f"大户{big_pct:.0f}%多 vs 散户{retail_pct:.0f}%多，分歧{divergence:.0f}%——散户是猎物。")
        if big_pct > retail_pct:
            stories.append("主力在买散户在空，散户追空将被猎杀。")
        else:
            stories.append("散户追多但大户不跟，多头陷阱风险。")
    elif divergence < 5:
        stories.append(f"大户{big_pct:.0f}% vs 散户{retail_pct:.0f}%，分歧小，无猎杀条件。")
    else:
        stories.append(f"大户{big_pct:.0f}% vs 散户{retail_pct:.0f}%，分歧{divergence:.0f}%，轻度博弈。")
    
    # OI+CVD交叉
    if 'LONG_UNWIND' in oi_signal and 'SELL' in cvd_dir:
        stories.append("OI撤退+CVD卖方主导=资金在撤离，多头疲软。")
    elif 'LONG_BUILD' in oi_signal and 'BUY' in cvd_dir:
        stories.append("OI建仓+CVD买方主导=资金在流入，多头强劲。")
    
    return {'story': ' → '.join(stories)}


def _form_trade_view(action, direction, entry_lo, entry_hi, sl, sl_pct, rr1,
                     tp1, tp2, tp3, resonance, market, main_force, money_flow, hunt) -> dict:
    """Step 5: 我在哪入场？"""
    if action == 'WAIT':
        return {'story': '⏳ 等待——共振不足或趋势未确认，不入场。'}
    
    if action in ('ENTER', 'ENTER_FULL', 'AMBUSCADE', 'AMBUSCADE_WATCH'):
        lines = []
        if direction == 'SHORT':
            lines.append(f"🔴 做空 | 挂单区 ${entry_lo:,.0f}~${entry_hi:,.0f}")
            lines.append(f"止损 ${sl:,.0f}（{sl_pct:.1f}%）| 目标 ${tp1:,.0f}→${tp2:,.0f}→${tp3:,.0f}")
            lines.append(f"RR={rr1:.1f} | 共振{resonance}/7")
        elif direction == 'LONG':
            lines.append(f"🟢 做多 | 挂单区 ${entry_lo:,.0f}~${entry_hi:,.0f}")
            lines.append(f"止损 ${sl:,.0f}（{sl_pct:.1f}%）| 目标 ${tp1:,.0f}→${tp2:,.0f}→${tp3:,.0f}")
            lines.append(f"RR={rr1:.1f} | 共振{resonance}/7")
        else:
            lines.append(f"⏳ 无方向，等待。")
        
        # 逻辑链
        lines.append(f"\n逻辑：{market['label']} → {main_force['story'][:60]}... → {money_flow['story'][:60]}...")
        return {'story': '\n'.join(lines)}
    
    return {'story': f"⏳ {action}——条件不足，等待。"}
