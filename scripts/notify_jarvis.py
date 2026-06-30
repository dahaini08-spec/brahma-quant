#!/usr/bin/env python3
"""
notify_jarvis.py — 统一Jarvis推送入口
设计院 2026-06-04
"""
import subprocess, sys, os

try:
    import sys as _s; _s.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
    from system_config import JARVIS_TARGET
except Exception:
    JARVIS_TARGET = '73295708:thread:019f1797-6c60-7541-ad72-ec34ed14dfc4'  # fallback

def send(msg: str) -> bool:
    """通过openclaw message发送到Jarvis"""
    try:
        r = subprocess.run(
            ['openclaw', 'message', 'send',
             '--channel', 'jarvis',
             '--to', JARVIS_TARGET,
             '--message', msg],
            capture_output=True, text=True, timeout=15
        )
        return r.returncode == 0
    except Exception as e:
        # fallback: print to stdout（cron会通过announce推送）
        print(f'[notify_jarvis] {msg}')
        return False

if __name__ == '__main__':
    if len(sys.argv) > 1:
        msg = ' '.join(sys.argv[1:])
        send(msg)
        print('sent')
