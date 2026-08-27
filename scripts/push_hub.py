"""
push_hub.py — 梵天全系统统一推送出口
设计院 根因修复 2026-07-08

所有脚本统一调用此模块推送到 Jarvis，不再依赖外部进程
"""
import subprocess, json, time, os, datetime
from pathlib import Path

# SSOT 路由
# [FIX 2026-08-02] path修复：push_hub在scripts/目录，system_config也在scripts/，直接用parent
try:
    import sys
    _sc_dir = str(Path(__file__).parent)  # = .../scripts/
    if _sc_dir not in sys.path:
        sys.path.insert(0, _sc_dir)
    from system_config import JARVIS_USER_ID, JARVIS_THREAD_ID, JARVIS_CHANNEL
    _TARGET  = f"{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}"
    _CHANNEL = JARVIS_CHANNEL
except Exception as _e:
    # 硬编码fallback（与system_config保持同步）
    _TARGET  = "73295708:thread:01a033af-3697-734a-9f9c-c3e34a00c378"
    _CHANNEL = "jarvis"

_DEDUP_FILE = Path(__file__).parent / "data" / "push_dedup.json"

def _load_dedup():
    try:
        return json.loads(_DEDUP_FILE.read_text())
    except Exception:
        return {}

def _save_dedup(d):
    try:
        _DEDUP_FILE.parent.mkdir(exist_ok=True)
        _DEDUP_FILE.write_text(json.dumps(d))
    except Exception:
        pass

def _jarvis(msg, dedup_key=None, dedup_ttl=3600):
    """推送消息到 Jarvis 当前线程"""
    if not msg or not msg.strip():
        return False
    if dedup_key:
        dedup = _load_dedup()
        now = time.time()
        last = dedup.get(dedup_key, 0)
        if now - last < dedup_ttl:
            print(f"[push_hub] 去重跳过: {dedup_key} (剩余{(dedup_ttl-(now-last))/60:.0f}min)")
            return False
        dedup[dedup_key] = now
        dedup = {k: v for k, v in dedup.items() if now - v < 86400}
        _save_dedup(dedup)
    try:
        r = subprocess.run(
            ["openclaw", "message", "send",
             "--channel", _CHANNEL,
             "--target",  _TARGET,
             "--message", msg],
            capture_output=True, text=True, timeout=15
        )
        ok = r.returncode == 0
        if not ok:
            print(f"[push_hub] 推送失败 rc={r.returncode}: {r.stderr[:100]}")
        return ok
    except Exception as e:
        print(f"[push_hub] 推送异常: {e}")
        return False

def push_signal_card(sym, score, grade, direction, entry_lo, entry_hi, sl, tp1,
                     timing="READY", tp2=0, rr=1.0, regime='', sl_basis='', rsi_4h=None,
                     oi_change=None, fr=None, score_tier=None):
    """推送梵天VIP信号卡片 — v2.0 操作指南格式（苏摩看到的是行动，不是评分）"""
    emoji    = "🟢" if direction == "LONG" else "🔴"
    dir_cn   = "做多" if direction == "LONG" else "做空"
    tag      = sym.replace("USDT", "")
    ts       = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%m-%d %H:%M')
    sl_pct   = round(abs(entry_lo - sl) / entry_lo * 100, 1) if entry_lo else 2.0
    tp2_line = f"  TP2: ${tp2:,.4f}\n" if tp2 else ""

    # ── 实时数据行（RSI/OI/FR）
    data_parts = []
    if rsi_4h is not None: data_parts.append(f"RSI_4H={rsi_4h:.1f}")
    if oi_change is not None: data_parts.append(f"OI变化={oi_change:+.1f}%")
    if fr is not None: data_parts.append(f"FR={fr:.4f}%")
    data_line = "  📡 " + "  ".join(data_parts) + "\n" if data_parts else ""

    # ── 止损依据说明
    sl_note = f"（{sl_basis[:30]}）" if sl_basis else "（4H支撑+ATR缓冲）"

    # ── 分级标识
    tier = "TIER1 🔴" if score >= 155 else ("TIER2 🟠" if score >= 130 else "WATCH 🟡")

    # ── 操作指令（核心：告诉苏摩现在该干什么）
    if score >= 155:
        action_block = (
            f"【操作指令】\n"
            f"  ✅ 现在：{dir_cn} {tag}\n"
            f"  入场区：${entry_lo:,.4f} ~ ${entry_hi:,.4f}\n"
            f"  止损：  ${sl:,.4f}  -{sl_pct}% {sl_note}\n"
            f"  止盈：  TP1=${tp1:,.4f}  RR={rr}x\n"
            f"{tp2_line}"
            f"  仓位：  5% NAV  LEV=5x"
        )
    elif score >= 130:
        action_block = (
            f"【操作指令】——待苏摩确认\n"
            f"  ⏳ 等15M触发后入场 ({dir_cn})，回复「执行」开仓\n"
            f"  布局区：${entry_lo:,.4f} ~ ${entry_hi:,.4f}\n"
            f"  触发：  15M出现CHoCH或强势突破\n"
            f"  止损：  ${sl:,.4f}  -{sl_pct}% {sl_note}\n"
            f"  止盈：  TP1=${tp1:,.4f}  RR={rr}x"
        )
    else:
        action_block = (
            f"【操作指令】\n"
            f"  👁 监控中——不开仓\n"
            f"  评分={score:.0f}，需≥155才自动执行\n"
            f"  关注：${entry_lo:,.4f}区间是否出现结构信号"
        )

    msg = (
        f"🚨 **梵天信号 · {tier}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{emoji} **{tag}/USDT {direction}** | score={score:.0f} {grade}\n"
        f"  体制: {regime or 'CHOP_MID'} | 时机: {timing}\n"
        f"{data_line}"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{action_block}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  {ts} UTC  [梵天事件驱动]"
    )
    dedup_key = f"signal_{sym}_{direction}_{int(entry_lo*10000)}_{int(score)}"
    return _jarvis(msg, dedup_key=dedup_key, dedup_ttl=14400)


def push_signal_card_v3(result: dict) -> bool:
    """
    [P2潜能激发 2026-08-26] VIP信号卡片v3.0
    包含: 基础信号 + 方仓状态 + 战场预判 + AI议会关键意见
    """
    sym       = result.get('symbol', '')
    score     = float(result.get('score_final') or result.get('score') or 0)
    grade     = result.get('grade', '')
    direction = result.get('direction') or result.get('signal_dir', '')
    regime    = result.get('regime', '')
    timing    = result.get('timing_badge', result.get('timing_status', 'READY'))
    entry_lo  = float(result.get('entry_lo') or 0)
    entry_hi  = float(result.get('entry_hi') or 0)
    sl        = float(result.get('stop_loss') or 0)
    tp1       = float(result.get('tp1') or 0)
    tp2       = float(result.get('tp2') or 0)
    rr        = float(result.get('rr1') or 1.0)
    price     = float(result.get('price') or 0)

    emoji   = '🟢' if direction == 'LONG' else '🔴'
    dir_cn  = '做多' if direction == 'LONG' else '做空'
    tag     = sym.replace('USDT', '')
    ts      = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%m-%d %H:%M')
    sl_pct  = round(abs(entry_lo - sl) / entry_lo * 100, 1) if entry_lo > 0 else 0
    tier    = 'TIER1 🔴' if score >= 155 else ('战場候命 🟠' if score >= 130 else 'WATCH 🟡')

    # ── 方仓块────────────────────────────────────────────
    # [P0修复 2026-08-27] 真实字段名: 'fangcang' (来自brahma_core._result['fangcang'])
    fangcang = result.get('fangcang') or result.get('_fangcang') or result.get('fangcang_result') or {}
    fc_line  = ''
    if isinstance(fangcang, dict) and fangcang.get('confidence'):
        top_sim = fangcang.get('top_similar', [])
        fc_n    = len(top_sim)
        fc_fret = sum(x.get('future_ret',0) for x in top_sim[:5])/max(len(top_sim[:5]),1) if top_sim else 0
        fc_wr   = sum(1 for x in top_sim[:10] if x.get('future_ret',0)>0)/max(len(top_sim[:10]),1) if top_sim else 0
        fc_regime = fangcang.get('current_regime', '')
        fc_line  = f'  📌 方仓[{fc_regime}]: 相似案例n={fc_n} WR={fc_wr:.0%} avg_ret={fc_fret:+.2f}%\n' if fc_n else ''

    # ── 战场预判块────────────────────────────────────────
    pze      = result.get('_price_zones') or result.get('price_zones') or {}
    # [P0修复 2026-08-27] 字段名: high_short/low_long/scenario_prob
    pze_line = ''
    if isinstance(pze, dict):
        hs    = pze.get('high_short') or {}
        ll    = pze.get('low_long') or {}
        probs = pze.get('scenario_prob') or {}
        down_p = float(probs.get('down_first', 0))
        up_p   = float(probs.get('up_first', 0))
        bias   = pze.get('bias', '')
        if hs.get('low') or ll.get('low'):
            hs_lo = hs.get('low', 0); hs_hi = hs.get('high', 0); hs_rr = hs.get('rr', '?')
            ll_lo = ll.get('low', 0); ll_hi = ll.get('high', 0); ll_rr = ll.get('rr', '?')
            pze_line  = f'  ⛳ 战场[{bias}] 下行={down_p:.0%} 上行={up_p:.0%}\n'
            if hs_lo: pze_line += f'     做空区: ${hs_lo:,.1f}~${hs_hi:,.1f} RR={hs_rr}\n'
            if ll_lo: pze_line += f'     做多区: ${ll_lo:,.1f}~${ll_hi:,.1f} RR={ll_rr}\n'

    # ── AI议会块──────────────────────────────────────────
    council  = result.get('_llm_council') or {}
    council_line = ''
    if isinstance(council, dict) and council.get('final_adj') is not None:
        fadj     = council.get('final_adj', 0)
        c_risk   = (council.get('risk') or {}).get('summary', '')
        c_macro  = (council.get('macro') or {}).get('summary', '')
        c_mode   = council.get('council_mode', '')
        fadj_str = f'{fadj:+d}' if isinstance(fadj, (int,float)) else str(fadj)
        council_line  = f'  🤖 议会[{c_mode}] adj={fadj_str}\n'
        if c_risk:  council_line += f'     Risk: {str(c_risk)[:40]}\n'
        if c_macro: council_line += f'     Macro: {str(c_macro)[:40]}\n'

    msg = (
        f'🚨 **梗天信号·{tier}**\n'
        f'━━━━━━━━━━━━━━━━━━━━━━\n'
        f'{emoji} **{tag}/USDT {direction}** | score={score:.0f} {grade}\n'
        f'  体制: {regime} | 时机: {timing} | 当前: ${price:,.2f}\n'
        f'━━━━━━━━━━━━━━━━━━━━━━\n'
        f'  入场区: ${entry_lo:,.4f} ~ ${entry_hi:,.4f}\n'
        f'  止损: ${sl:,.4f} (-{sl_pct}%) | TP1: ${tp1:,.4f} RR={rr}x\n'
        f'{fc_line}'
        f'{pze_line}'
        f'{council_line}'
        f'━━━━━━━━━━━━━━━━━━━━━━\n'
        f'  {ts} CST  [梗天全能力 v3.0]'
    )
    dedup_key = f'v3_{sym}_{direction}_{int(score)}'
    return _jarvis(msg, dedup_key=dedup_key, dedup_ttl=7200)


def push_skip_card(sym, score, direction, regime, reason_top3, next_condition, price=None):
    """推送SKIP状态通知 — 告诉苏摩为什么不入场+下一个窗口"""
    tag  = sym.replace("USDT", "")
    ts   = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%m-%d %H:%M')
    dir_cn = "做多" if direction == "LONG" else "做空"
    price_line = f"  当前价: ${price:.4f}\n" if price else ""
    reasons = "\n".join(f"    {r}" for r in (reason_top3 or [])[:3])
    msg = (
        f"⚫ **梵天扫描 · SKIP**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  {tag}/USDT {dir_cn} | score={score:.0f} | {regime}\n"
        f"{price_line}"
        f"  主要阻制:\n{reasons}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  下一个窗口: {next_condition}\n"
        f"  {ts} CST"
    )
    dedup_key = f"skip_{sym}_{direction}_{int(score)}"
    return _jarvis(msg, dedup_key=dedup_key, dedup_ttl=7200)
