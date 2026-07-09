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
    _TARGET  = "73295708:thread:019f443a-b891-70f1-8cb0-ed031a80e68b"
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

def push_signal_card(sym, score, grade, direction, entry_lo, entry_hi, sl, tp1, timing="READY"):
    """推送梵天VIP信号卡片"""
    emoji = "🟢" if direction == "LONG" else "🔴"
    tag = sym.replace("USDT", "")
    ts = datetime.datetime.utcnow().strftime('%m-%d %H:%M')
    msg = (
        f"{emoji} **梵天信号 · {tag} {direction}**\n"
        f"  score={score:.0f}  grade={grade}  timing={timing}\n"
        f"  入场区: ${entry_lo:.2f}~${entry_hi:.2f}\n"
        f"  止损: ${sl:.2f}  TP1: ${tp1:.2f}\n"
        f"  时间: {ts} UTC"
    )
    dedup_key = f"signal_{sym}_{direction}_{int(entry_lo)}"
    return _jarvis(msg, dedup_key=dedup_key, dedup_ttl=14400)
