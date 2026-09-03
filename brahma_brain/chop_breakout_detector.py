#!/usr/bin/env python3
"""
chop_breakout_detector.py — CHOP震荡突破预判器
设计院封印 2026-09-03 苏摩111

核心认知（来自64000→81600复盘）：
  CHOP_MID:LONG 全量 WR=31% → 封禁（正确）
  但 CHOP_MID:LONG 高质量子集 WR >> 31% → 应该参与（现在错过了）

区别：
  普通CHOP_MID做多 = 在震荡中间随机做多 → WR=31% 正确封禁
  CHOP突破预埋信号 = CHOP末段+结构共振+聪明钱确认 → 启动前布局

判断条件（所有条件必须同时满足）：
  C1: SMC结构=满分(≥18/20)
  C2: FVG磁铁方向一致（Bull FVG在上方）
  C3: 聪明钱大户多 ≥ 62%（机构已布局）
  C4: CVD ≥ 6（持续买方主导）
  C5: ATR分位 ≤ 20th（压缩期，大行情前兆）
  C6: HCME历史相似情境看涨（相似度 ≥ 0.65）或极端事件UP信号
  C7: 体制 raw_score（HMM BULL概率） ≥ 35%（趋势萌芽）
  
满足 C1+C2+C3+C4+C5 = 5条 → CHOP_BREAKOUT_WATCH（观察，发预警）
满足 C1+C2+C3+C4+C5+C6 = 6条 → CHOP_BREAKOUT_READY（就绪，小仓入场）
满足全部7条 → CHOP_BREAKOUT_EXECUTE（执行，标准仓位）

仓位限制（风控铁律）：
  WATCH:   0%（不入场，只推送预警）
  READY:   1%NAV（小仓埋伏，SL=ATR×2，止损即出）
  EXECUTE: 2%NAV（半仓，等体制确认后加至标准）
  
禁止加码：即使所有条件满足，CHOP解锁仓位上限 = 2%NAV（不是5%）
接入位置：paper_executor.py + auto_executor.py 的死穴检查前
"""
import json, sys
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

def detect_chop_breakout(state: dict, symbol: str = 'BTCUSDT') -> dict:
    """
    输入：brahma_state.json 内容（单标的）
    输出：
        {
            'signal': 'NONE'|'WATCH'|'READY'|'EXECUTE',
            'conditions_met': [str],     # 满足的条件
            'conditions_failed': [str],  # 未满足的条件
            'nav_pct': float,            # 建议仓位%NAV
            'reason': str,               # 一句话说明
            'score': int,                # 0-7
        }
    """
    bd    = state.get('confluence', {}).get('breakdown', {})
    extra = state.get('extra', {})
    regime= state.get('regime', '')
    
    # 只在CHOP体制下运行
    if 'CHOP' not in regime:
        return {'signal': 'NONE', 'reason': f'非CHOP体制({regime})', 'score': 0,
                'conditions_met': [], 'conditions_failed': [], 'nav_pct': 0}
    
    met    = []
    failed = []
    
    # ── C1: SMC结构满分 ─────────────────────────────────────
    smc_raw = bd.get('SMC结构', 0)
    try:
        smc_score = float(str(smc_raw).split()[0]) if smc_raw else 0
    except Exception:
        smc_score = 0
    if smc_score >= 18:
        met.append(f'C1 SMC结构{smc_score:.0f}/20✅')
    else:
        failed.append(f'C1 SMC结构{smc_score:.0f}<18❌')
    
    # ── C2: Bull FVG磁铁方向一致 ─────────────────────────────
    fvg_4h = str(bd.get('FVG_4H_LONG', '') or '')
    fvg_15m= str(bd.get('FVG_15M_LONG','') or '')
    has_bull_fvg = ('磁铁' in fvg_4h and '+' in fvg_4h) or ('磁铁' in fvg_15m and '+' in fvg_15m)
    if has_bull_fvg:
        met.append('C2 Bull FVG磁铁向上✅')
    else:
        failed.append('C2 无Bull FVG磁铁❌')
    
    # ── C3: 聪明钱大户多 ≥ 62% ──────────────────────────────
    smart = extra.get('smart_money', {})
    big_pos = smart.get('big_pos_long', 0)
    if big_pos >= 0.62:
        met.append(f'C3 聪明钱多{big_pos:.0%}≥62%✅')
    else:
        failed.append(f'C3 聪明钱多{big_pos:.0%}<62%❌')
    
    # ── C4: CVD ≥ 6（持续买方压力）────────────────────────────
    enhanced = extra.get('enhanced', {})
    cvd = enhanced.get('breakdown', {}).get('cvd', 0)
    if cvd >= 6:
        met.append(f'C4 CVD买压+{cvd}≥6✅')
    else:
        failed.append(f'C4 CVD买压{cvd}<6❌')
    
    # ── C5: ATR分位 ≤ 20th（压缩期）───────────────────────────
    atr_p = extra.get('atr_percentile', {})
    atr_pct = atr_p.get('atr_percentile', 50)
    if atr_pct <= 20:
        met.append(f'C5 ATR压缩{atr_pct:.0f}th≤20th✅')
    else:
        failed.append(f'C5 ATR{atr_pct:.0f}th>20th❌')
    
    # ── C6: HCME历史看涨情境 ────────────────────────────────
    extreme = str(bd.get('extreme_event', '') or '')
    hcme_sim = float(bd.get('HCME情境匹配', 0) or 0)
    hcme_ok = ('UP' in extreme and '相似' in extreme) or hcme_sim >= 0.65
    if hcme_ok:
        met.append(f'C6 历史情境看涨(sim={hcme_sim:.2f})✅')
    else:
        failed.append(f'C6 历史情境无看涨信号❌')
    
    # ── C7: HMM BULL概率萌芽 ≥ 35% ─────────────────────────
    # 从regime_hmm_v2获取实时概率（如果可用）
    bull_prob = 0.0
    try:
        sys.path.insert(0, str(BASE / 'brahma_brain'))
        from brahma_brain.regime_hmm_v2 import predict_regime_proba
        # 用缓存数据，不触发新API调用
        hmm_cache = BASE / 'data' / 'regime_hmm_cache.json'
        if hmm_cache.exists():
            hc = json.loads(hmm_cache.read_text())
            bull_prob = hc.get('BTCUSDT', {}).get('BULL_TREND', 0) or hc.get('bull_prob', 0)
    except Exception:
        pass
    
    # fallback：多维指标联合反推BULL趋势萌芽概率
    # 逻辑：score低但聪明钱+CVD+RSI全部偏多 → 趋势萌芽概率仍可以达标
    if bull_prob == 0.0:
        score_f  = float(state.get('score_final') or state.get('score') or 0)
        try: score_f = float(str(score_f).split()[0])
        except: pass
        smart_l  = extra.get('smart_money', {}).get('big_pos_long', 0.5)
        cvd_v    = enhanced.get('breakdown', {}).get('cvd', 0)
        mom      = state.get('momentum') or {}
        rsi_1h   = float(mom.get('rsi_1h', 50) or 50)
        # 各分项贡献（满分=0.85）
        p_score  = min(max((score_f - 20) / 80, 0), 0.30)   # score 20→100 映射0→0.30
        p_smart  = max(smart_l - 0.50, 0) * 1.5              # 大户>50%正贡献
        p_cvd    = min(max(cvd_v, 0) / 40.0, 0.20)           # CVD正贡献上限0.20
        p_rsi    = max(rsi_1h - 50, 0) / 200.0               # RSI>50正贡献
        bull_prob = round(min(p_score + p_smart + p_cvd + p_rsi, 0.85), 3)

    if bull_prob >= 0.35:
        met.append(f'C7 BULL概率{bull_prob:.0%}≥35%✅')
    else:
        failed.append(f'C7 BULL概率{bull_prob:.0%}<35%（多维推算={bull_prob:.0%}）❌')
    
    # ── 综合判断 ────────────────────────────────────────────
    n = len(met)
    
    if n >= 7:
        signal  = 'EXECUTE'
        nav_pct = 0.02  # 2%NAV 硬上限
        reason  = f'CHOP突破全条件达标({n}/7)，2%NAV小仓埋伏，等体制确认后加仓'
    elif n >= 6:
        signal  = 'READY'
        nav_pct = 0.01  # 1%NAV
        reason  = f'CHOP突破6/7条件达标，1%NAV预埋，SL=ATR×2止损即出'
    elif n >= 5:
        signal  = 'WATCH'
        nav_pct = 0.0
        reason  = f'CHOP突破5/7条件，发出预警，不入场，等待剩余条件'
    else:
        signal  = 'NONE'
        nav_pct = 0.0
        reason  = f'CHOP突破条件不足({n}/7)，保持封禁'
    
    return {
        'signal':            signal,
        'score':             n,
        'nav_pct':           nav_pct,
        'reason':            reason,
        'conditions_met':    met,
        'conditions_failed': failed,
        'regime':            regime,
        'bull_prob':         round(bull_prob, 3),
        'smc_score':         smc_score,
        'big_pos':           round(big_pos, 3),
        'cvd':               cvd,
        'atr_pct':           atr_pct,
        'hcme_ok':           hcme_ok,
    }


def format_alert(result: dict, symbol: str) -> str:
    """格式化预警推送"""
    signal = result['signal']
    icon = {'NONE':'⚫','WATCH':'👁️','READY':'🟡','EXECUTE':'🟢'}.get(signal,'⚫')
    
    lines = [
        f'{icon} CHOP突破探测器 | {symbol} | {signal}',
        f'条件: {result["score"]}/7  {result["reason"]}',
        '',
        '满足条件:',
    ]
    for c in result['conditions_met']:
        lines.append(f'  {c}')
    if result['conditions_failed']:
        lines.append('缺失条件:')
        for c in result['conditions_failed'][:3]:
            lines.append(f'  {c}')
    
    if signal in ('READY', 'EXECUTE'):
        lines += [
            '',
            f'⚡ 建议：{signal}',
            f'  仓位: {result["nav_pct"]*100:.0f}%NAV（硬上限，不可超）',
            f'  SL: ATR1H×2.0（结构失效即出）',
            f'  止盈: 等体制切BULL_EARLY后加仓至标准仓位',
            f'  ⚠️ CHOP解锁≠正常信号，严格止损',
        ]
    
    return '\n'.join(lines)


# ── 命令行测试 ─────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    state_path = BASE / 'data' / 'brahma_state.json'
    if state_path.exists():
        state = json.loads(state_path.read_text())
        result = detect_chop_breakout(state, 'BTCUSDT')
        print(format_alert(result, 'BTCUSDT'))
        print()
        print(f'原始结果: {json.dumps(result, ensure_ascii=False, indent=2)}')
