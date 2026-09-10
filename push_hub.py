#!/usr/bin/env python3
"""
push_hub.py — 根目录兼容包装（2026-09-10 苏摩111 P1修复）

所有实际实现已统一到 scripts/push_hub.py
此文件仅作为兼容入口，重导出新版函数，
让旧脚本 `from push_hub import _jarvis` 继续正常工作。

接入位置：
  所有 `from push_hub import ...` 的旧脚本（20+个）
  新脚本应直接用 `from scripts.push_hub import ...`

设计院封印 2026-09-10
"""
import sys
import importlib
from pathlib import Path

# 将scripts/加入path，用importlib导入真正的push_hub（避免同名冲突）
_scripts_dir = str(Path(__file__).parent / "scripts")
sys.path.insert(0, _scripts_dir)

# 用importlib按文件路径导入，避免与当前模块同名冲突
import importlib.util
_spec = importlib.util.spec_from_file_location("scripts_push_hub", Path(_scripts_dir) / "push_hub.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# 重导出所有公共函数
push_jarvis = _mod.push_jarvis
push_jarvis_silent = _mod.push_jarvis_silent
push_signal_card_v3 = _mod.push_signal_card_v3
_jarvis = _mod._jarvis
JARVIS_USER_ID = _mod.JARVIS_USER_ID
JARVIS_THREAD_ID = _mod.JARVIS_THREAD_ID
_TARGET = _mod._TARGET

def push_signal_card(sym, score, grade, direction, entry_lo, entry_hi, sl,
                     tp1, timing="READY", tp2=0, rr=1.0):
    """旧版兼容 — 构造r_raw调用新版push_signal_card_v3"""
    r_raw = {
        'symbol': sym, 'score_final': score, 'grade': grade,
        'direction': direction, 'entry_lo': entry_lo, 'entry_hi': entry_hi,
        'stop_loss': sl, 'tp1': tp1, 'tp2': tp2, 'rr': rr,
        'regime': '', 'price': entry_hi,
    }
    return push_signal_card_v3(r_raw)
