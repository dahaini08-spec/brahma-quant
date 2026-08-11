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
    _TARGET  = "73295708:thread:019fd9dd-4b0f-71db-87fb-1e192ccb2291"
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
