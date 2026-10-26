#!/usr/bin/env python3
"""
梵天概率树推理引擎 [封印 2026-10-26 苏摩111]
接入位置：brahma_deep_analysis.py format_vip() → vip_output拼接前
功能：基于实时信号计算A/B/C三路径概率，输出预判型路线图
= 从"空单$X"升级为"主路线A(65%)$X 预计09:30触发"
"""
import time
from datetime import datetime, timezone


# Session时间段 UTC
SESSION_WINDOWS = {
    'ASIA':   (0,  8),    # 00~08 UTC
    'EU':     (7,  16),   # 07~16 UTC
    'US':     (13, 22),   # 13~22 UTC
}

def _current_session() -> str:
    h = datetime.now(timezone.utc).hour
    if 13 <= h < 22: return 'US'
    if 7  <= h < 16: return 'EU'
    return 'ASIA'

def _next_session_open() -> str:
    """预计下一个活跃开盘时间（UTC）"""
    h = datetime.now(timezone.utc).hour
    if h < 7:  return f"07:00 UTC（欧盘开盘，{7-h}H后）"
    if h < 13: return f"13:30 UTC（美盘开盘，{13-h}H后）"
    if h < 22: return "当前美盘活跃"
    return "次日07:00 UTC（欧盘）"


def calc_prob_tree(d: dict, reasoning: dict) -> dict:
    """
    计算三路径概率树。
    返回：{path_A, path_B, path_C}，每路包含prob/label/entry/trigger/eta/rr
    """
    px      = d.get('px', 0)
    lsr_g   = d.get('lsr_g', 1.0)
    oi_8h   = d.get('oi_8h', 0)
    tak     = d.get('tak', 1.0)
    tak_avg = d.get('tak_avg', 1.0)
    bb_w    = d.get('bb_width', 2.0)
    hurst   = d.get('hurst', 0.55)
    liq_up  = d.get('liq_up', px * 1.02)
    liq_dn  = d.get('liq_dn', px * 0.98)
    fvg_b   = d.get('fvg_bear') or {}
    fvg_bull= d.get('fvg_bull') or {}
    regime  = d.get('regime', 'CHOP_MID')
    direction = reasoning.get('direction', 'WAIT')
    step11_sl = reasoning.get('step11_sl', 0)
    step11_tp1= reasoning.get('step11_tp1', 0)
    step11_tp2= reasoning.get('step11_tp2', 0)
    session = _current_session()

    # ── 路线A：主力扫池路径（概率核心驱动：LSR敞口×OI方向）────────────────
    # 主力先扫止损池（释放流动性），再反向发力
    scan_target  = liq_up  if direction in ('SHORT', 'WAIT') else liq_dn
    scan_dist_pct= abs(scan_target - px) / px * 100 if px > 0 else 2.0

    # 概率计算：LSR越极端 + OI建仓 → 路线A概率越高
    p_a = 0.50
    if lsr_g > 3.0:    p_a += 0.12
    elif lsr_g > 2.5:  p_a += 0.07
    if oi_8h > 1.0:    p_a += 0.06   # OI建仓 = 主力有备而来
    if bb_w < 1.5:     p_a += 0.05   # BB压缩 = 蓄力待发
    if hurst > 0.6:    p_a += 0.04   # 趋势性强 = 扫池后延伸
    p_a = min(round(p_a, 2), 0.80)

    # 预期触发时间（Session+距离推算）
    dist_h = max(0.5, scan_dist_pct / 0.3)   # 每小时约0.3% ATR移动
    if session == 'US' and scan_dist_pct < 1.0:
        eta_a = f"≈{max(15,int(dist_h*60))}min（美盘波动大）"
    elif session == 'EU':
        eta_a = f"≈{max(30,int(dist_h*60))}min（欧盘稳步推进）"
    else:
        eta_a = f"等待{_next_session_open()}"

    path_a = {
        'label':   '主力扫池路径',
        'prob':    p_a,
        'trigger': f"价格→${scan_target:,.0f}止损池→拐头",
        'entry':   step11_sl or (liq_up * 0.998 if direction == 'SHORT' else liq_dn * 1.002),
        'tp':      step11_tp2 or step11_tp1,
        'eta':     eta_a,
        'condition': f"LSR={lsr_g:.2f}+OI={oi_8h:+.1f}%",
        'cancel':  f"{'突破止损池不回头' if direction in ('SHORT','WAIT') else '跌破止损池不回头'}",
    }

    # ── 路线B：直接突破路径（Taker脉冲×BB宽度）──────────────────────────────
    p_b = 1.0 - p_a - 0.10   # 剩余概率按比例
    if tak > 1.5:   p_b += 0.05   # Taker主买 = 直接突破可能
    if bb_w > 2.5:  p_b += 0.03   # BB已扩张 = 趋势延续
    p_b = max(0.05, min(round(p_b, 2), 0.40))

    fvg_entry = fvg_b.get('lo', px * 0.99) if direction == 'SHORT' else fvg_bull.get('hi', px * 1.01)
    path_b = {
        'label':   '直接突破路径',
        'prob':    p_b,
        'trigger': f"Taker>{tak_avg:.1f}持续3根+价格直破FVG",
        'entry':   fvg_entry,
        'tp':      step11_tp1,
        'eta':     f"Taker脉冲触发即入，{_next_session_open()}",
        'condition': f"Taker>{tak_avg*1.3:.1f}（当前{tak:.2f}）",
        'cancel':  "Taker回落<1.0后取消",
    }

    # ── 路线C：反转/结构破坏路径（FVG磁铁×CVD背离）────────────────────────
    p_c = max(0.05, round(1.0 - p_a - p_b, 2))
    path_c = {
        'label':   '结构反转路径',
        'prob':    p_c,
        'trigger': f"BB压缩释放+方向与{'SHORT' if direction=='SHORT' else 'LONG'}相反",
        'entry':   None,
        'tp':      None,
        'eta':     "观望，信号出现再判断",
        'condition': f"BB宽度突然>3.0且Taker单向连续",
        'cancel':  "此路线触发=取消其他路线",
    }

    # ── 归一化概率 ──────────────────────────────────────────────────────────
    total = p_a + p_b + p_c
    if total > 0:
        path_a['prob'] = round(p_a / total, 2)
        path_b['prob'] = round(p_b / total, 2)
        path_c['prob'] = round(1.0 - path_a['prob'] - path_b['prob'], 2)

    return {
        'path_A': path_a,
        'path_B': path_b,
        'path_C': path_c,
        'dominant': 'A' if path_a['prob'] >= path_b['prob'] else 'B',
        'session':  session,
    }


def format_prob_tree(tree: dict, direction: str) -> list:
    """
    格式化概率树为VIP输出行（追加到VIP卡片末尾）
    """
    if not tree:
        return []
    a = tree['path_A']; b = tree['path_B']; c = tree['path_C']
    dominant = tree.get('dominant', 'A')
    lines = [
        "",
        "📊 概率路线图（40年预判型）：",
        f"  路线A {a['prob']:.0%}｜{a['label']}",
        f"    触发：{a['trigger']}",
        f"    预计：{a['eta']}",
        f"    条件：{a['condition']}",
        f"  路线B {b['prob']:.0%}｜{b['label']}",
        f"    触发：{b['trigger']}",
        f"  路线C {c['prob']:.0%}｜{c['label']}",
        f"⚡ 押注路线{'A' if dominant=='A' else 'B'}（{a['prob']:.0%}概率），"
        f"{'路线B出现追加仓位' if dominant=='A' else '路线A扫池完成后加仓'}",
    ]
    return lines
