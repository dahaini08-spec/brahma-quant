"""
push_hub.py — 梵天全系统统一推送出口
设计院 根因修复 2026-07-08

所有脚本统一调用此模块推送到 Jarvis，不再依赖外部进程
"""
import subprocess, json, time, os, datetime
from pathlib import Path

# SSOT 路由
try:
    import sys
    sys.path.insert(0, str(Path(__file__).parent / 'scripts'))
    from system_config import JARVIS_USER_ID, JARVIS_THREAD_ID, JARVIS_CHANNEL
    _TARGET  = f"{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}"
    _CHANNEL = JARVIS_CHANNEL
except Exception:
    _TARGET  = f"{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}"  # 2026-07-25 统一从system_config读取，SSOT
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

def push_signal_card(sym, score, grade, direction, entry_lo, entry_hi, sl, tp1, timing="READY", tp2=0, rr=1.0):
    """推送梵天VIP信号卡片（事件驱动，score≥155立即推送）"""
    emoji   = "🟢" if direction == "LONG" else "🔴"
    tier    = "TIER1 🔴" if score >= 155 else "TIER2 🟠"
    tag     = sym.replace("USDT", "")
    ts      = datetime.datetime.utcnow().strftime('%m-%d %H:%M')
    sl_pct  = round((entry_hi - sl) / entry_hi * 100, 1) if entry_hi else 2.0
    tp2_line = f"  TP2:    ${tp2:,.2f}\n" if tp2 else ""
    msg = (
        f"🚨 **梵天信号 · {tier}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{emoji} **{tag}/USDT {direction}** | score={score:.0f} {grade}\n"
        f"  体制:   BULL_TREND | 时机: {timing}\n"
        f"  入场:   ${entry_lo:,.2f} ~ ${entry_hi:,.2f}\n"
        f"  止损:   ${sl:,.2f}  (-{sl_pct}%)\n"
        f"  TP1:    ${tp1:,.2f}  RR={rr}x\n"
        f"{tp2_line}"
        f"  仓位:   5% NAV  LEV=5x\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  {ts} UTC  [事件驱动]"
    )
    dedup_key = f"signal_{sym}_{direction}_{int(entry_lo)}_{int(score)}"
    return _jarvis(msg, dedup_key=dedup_key, dedup_ttl=14400)
