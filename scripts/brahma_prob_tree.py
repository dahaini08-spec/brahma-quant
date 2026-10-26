#!/usr/bin/env python3
"""
梵天概率树推理引擎 [封印 2026-10-26 苏摩111 P0矛盾修复]
接入位置：brahma_deep_analysis.py format_vip() 末尾调用
功能：基于format_vip的入场决策计算A/B/C三路径概率

P0修复 2026-10-26：
  根因：概率树用liq_up，format_vip用spike_entry，两套逻辑独立→矛盾
  修复：引入vip_context参数，概率树与VIP入场完全对齐
  = 路线A的触发→入场→止损→目标，与VIP卡片100%一致
"""
import time
from datetime import datetime, timezone

SESSION_WINDOWS = {'ASIA': (0,8), 'EU': (7,16), 'US': (13,22)}

def _current_session() -> str:
    h = datetime.now(timezone.utc).hour
    if 13 <= h < 22: return 'US'
    if 7  <= h < 16: return 'EU'
    return 'ASIA'

def _next_session_open() -> str:
    h = datetime.now(timezone.utc).hour
    if h < 7:  return f"07:00 UTC（欧盘，{7-h}H后）"
    if h < 13: return f"13:30 UTC（美盘，{13-h}H后）"
    if h < 22: return "当前美盘活跃"
    return "次日07:00 UTC"


def calc_prob_tree(d: dict, reasoning: dict, vip_context: dict = None) -> dict:
    """
    计算三路径概率树。
    vip_context: format_vip传入的入场决策，确保概率树与VIP完全对齐
      {entry_lo, entry_hi, sl, tp1, tp2, wait_mode, scan_target}
    """
    px       = d.get('px', 0)
    lsr_g    = d.get('lsr_g', 1.0)
    oi_8h    = d.get('oi_8h', 0)
    tak      = d.get('tak', 1.0)
    tak_avg  = d.get('tak_avg', 1.0)
    bb_w     = d.get('bb_width', 2.0)
    hurst    = d.get('hurst', 0.55)
    liq_up   = d.get('liq_up', px * 1.02)
    liq_dn   = d.get('liq_dn', px * 0.98)
    fvg_b    = d.get('fvg_bear') or {}
    fvg_bull = d.get('fvg_bull') or {}
    direction  = reasoning.get('direction', 'WAIT')
    step11_sl  = reasoning.get('step11_sl', 0)
    step11_tp1 = reasoning.get('step11_tp1', 0)
    step11_tp2 = reasoning.get('step11_tp2', 0)
    session    = _current_session()

    # ── 从vip_context读取入场决策（P0修复核心） ──────────────────────────────
    # 如果没有vip_context（直接调用），用liq_up/dn作为兜底
    if vip_context:
        entry_lo    = vip_context.get('entry_lo', 0)
        entry_hi    = vip_context.get('entry_hi', 0)
        real_sl     = vip_context.get('sl', step11_sl)
        real_tp1    = vip_context.get('tp1', step11_tp1)
        real_tp2    = vip_context.get('tp2', step11_tp2)
        wait_mode   = vip_context.get('wait_mode', False)   # True=等插针模式
        scan_target = vip_context.get('scan_target', liq_up if direction=='SHORT' else liq_dn)
    else:
        # 兜底：未传vip_context时用liq_up/dn（与旧行为兼容）
        entry_lo    = 0
        entry_hi    = step11_sl * 0.997 if step11_sl else 0
        real_sl     = step11_sl
        real_tp1    = step11_tp1
        real_tp2    = step11_tp2
        wait_mode   = True
        scan_target = liq_up if direction in ('SHORT', 'WAIT') else liq_dn

    # ── 路线A：主力扫池路径 ──────────────────────────────────────────────────
    # 语义：主力先推价到scan_target（扫空/多止损池），然后在入场区反向操作
    # 与VIP完全对齐：scan_target=插针目标，入场区=VIP等插针区间
    scan_dist_pct = abs(scan_target - px) / px * 100 if px > 0 else 2.0

    p_a = 0.50
    if lsr_g > 3.0:   p_a += 0.12
    elif lsr_g > 2.5: p_a += 0.07
    if oi_8h > 1.0:   p_a += 0.06
    if bb_w < 1.5:    p_a += 0.05
    if hurst > 0.6:   p_a += 0.04
    p_a = min(round(p_a, 2), 0.80)

    dist_min = max(15, int(scan_dist_pct / 0.3 * 60))
    if session == 'US':
        eta_a = f"≈{min(dist_min,120)}min（美盘活跃）"
    elif session == 'EU':
        eta_a = f"≈{min(dist_min,180)}min（欧盘推进）"
    else:
        eta_a = f"等{_next_session_open()}"

    # 路线A trigger：先到scan_target，然后在entry_lo~entry_hi入场
    if wait_mode and entry_hi > 0:
        trigger_a = (f"先涨至${scan_target:,.0f}扫池 → "
                     f"回落进入${entry_lo:,.0f}~${entry_hi:,.0f}入场")
    elif entry_hi > 0:
        trigger_a = f"价格进入${entry_lo:,.0f}~${entry_hi:,.0f}直接入场"
    else:
        trigger_a = f"价格到${scan_target:,.0f}止损池后拐头"

    path_a = {
        'label':      '主力扫池路径',
        'prob':       p_a,
        'trigger':    trigger_a,
        'scan_price': scan_target,
        'entry_lo':   entry_lo,
        'entry_hi':   entry_hi,
        'sl':         real_sl,
        'tp1':        real_tp1,
        'tp2':        real_tp2,
        'eta':        eta_a,
        'condition':  f"LSR={lsr_g:.2f} OI={oi_8h:+.1f}%",
        'cancel':     f"{'入场区上方+OI大增持续=结构失效' if direction=='SHORT' else '入场区下方+OI大减=结构失效'}",
        'wait_mode':  wait_mode,
    }

    # ── 路线B：直接突破路径 ──────────────────────────────────────────────────
    p_b = 1.0 - p_a - 0.10
    if tak > 1.5:  p_b += 0.05
    if bb_w > 2.5: p_b += 0.03
    p_b = max(0.05, min(round(p_b, 2), 0.40))

    fvg_entry = fvg_b.get('lo', px*0.99) if direction=='SHORT' else fvg_bull.get('hi', px*1.01)
    path_b = {
        'label':     '直接突破路径',
        'prob':      p_b,
        'trigger':   f"Taker>{tak_avg*1.3:.1f}持续3根+价格直破FVG（无需等插针）",
        'entry_lo':  fvg_entry * 0.999,
        'entry_hi':  fvg_entry * 1.001,
        'sl':        real_sl,
        'tp1':       real_tp1,
        'eta':       f"Taker脉冲即触发",
        'condition': f"Taker>{tak_avg*1.3:.1f}（当前{tak:.2f}）",
        'cancel':    "Taker回落<1.0取消",
    }

    # ── 路线C：结构反转路径 ──────────────────────────────────────────────────
    p_c = max(0.05, round(1.0 - p_a - p_b, 2))
    path_c = {
        'label':    '结构反转路径',
        'prob':     p_c,
        'trigger':  f"BB压缩释放+{'多头'if direction=='SHORT' else '空头'}意外突破",
        'entry_lo': 0, 'entry_hi': 0, 'sl': 0, 'tp1': 0,
        'eta':      "观望",
        'condition': "BB突然扩张>3%+Taker极端",
        'cancel':   "触发=取消全部其他路线",
    }

    # 归一化
    total = p_a + p_b + p_c
    if total > 0:
        path_a['prob'] = round(p_a / total, 2)
        path_b['prob'] = round(p_b / total, 2)
        path_c['prob'] = round(1.0 - path_a['prob'] - path_b['prob'], 2)

    return {
        'path_A':   path_a,
        'path_B':   path_b,
        'path_C':   path_c,
        'dominant': 'A' if path_a['prob'] >= path_b['prob'] else 'B',
        'session':  session,
    }


def format_prob_tree(tree: dict, direction: str) -> list:
    """格式化概率树为VIP卡片追加行"""
    if not tree:
        return []
    a = tree['path_A']; b = tree['path_B']; c = tree['path_C']
    dominant = tree.get('dominant', 'A')

    # 路线A完整描述（与VIP入场区对齐）
    a_entry = (f"${a.get('entry_lo',0):,.0f}~${a.get('entry_hi',0):,.0f}"
               if a.get('entry_hi',0) > 0 else "见VIP入场区")
    rr_a = ((a.get('entry_lo',0) - a.get('tp2',0)) /
            (a.get('sl',0) - a.get('entry_lo',0))
            if a.get('sl',0) > a.get('entry_lo',0) > a.get('tp2',0) > 0 else 0)

    lines = [
        "",
        "📊 概率路线图（40年预判型）：",
        f"  路线A {a['prob']:.0%}｜{a['label']}",
        f"    ① 先到: ${a.get('scan_price',0):,.0f}（扫止损池）",
        f"    ② 入场: {a_entry}（与VIP一致）",
        f"    ③ 止损: ${a.get('sl',0):,.1f}  目标: ${a.get('tp1',0):,.0f}→${a.get('tp2',0):,.0f}",
        f"    ⏱ 预计: {a['eta']}",
        f"  路线B {b['prob']:.0%}｜{b['label']}",
        f"    触发: {b['trigger']}",
        f"  路线C {c['prob']:.0%}｜{c['label']}（观望）",
        (f"⚡ 主押路线A（{a['prob']:.0%}）："
         f"{'等价格先扫池再回落入场' if a.get('wait_mode') else '直接等入场区触发'}"),
    ]
    if rr_a > 0:
        lines.append(f"   路线A RR={rr_a:.2f}{'✅' if rr_a>=1.5 else '⚠️'}")
    return lines
