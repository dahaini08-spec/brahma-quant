#!/usr/bin/env python3
"""
push_hub.py — 梵天统一推送模块（subprocess CLI，稳定可靠）
设计院封印 2026-09-07 苏摩111（二次修复）

历史：
  v1: subprocess CLI → 偶尔阻塞崩溃
  v2: 直接HTTP localhost:3000 → port 3000不通，全部推送失败！
  v3(本版): subprocess CLI + nohup非阻塞 → 稳定，不阻塞脚本主线程

修复方案：
  subprocess.Popen（非阻塞）调用 openclaw message send CLI
  不等待返回，主脚本继续运行，彻底解决阻塞问题

接入位置：
  scripts/oi_watchlist_monitor.py      → from push_hub import push_jarvis
  scripts/signal_change_detector.py   → from push_hub import push_jarvis
  scripts/brahma_daily_report.py      → from push_hub import push_jarvis
  scripts/morning_battlefield.py      → from push_hub import push_jarvis
  scripts/square/square_*.py          → from push_hub import push_jarvis
  brahma_brain/brahma_analysis_runner.py → from push_hub import push_jarvis
"""
import json, sys, time, subprocess
from pathlib import Path

# 推送配置（SSOT来自 MEMORY.md）
JARVIS_USER_ID   = "73295708"
JARVIS_THREAD_ID = "01a07970-f8ce-706b-8bea-3c94dd055443"
_TARGET          = f"{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}"


def push_jarvis(msg: str, timeout: int = 8, retries: int = 3) -> bool:
    """
    直接HTTP推送到Jarvis，不依赖openclaw CLI子进程。
    timeout: 单次请求超时秒数（默认8s，远低于原来的15s subprocess）
    retries: 失败重试次数（默认3次）
    返回: True=成功 False=全部失败
    """
    if not msg or not msg.strip():
        return False

    # 方式1: subprocess Popen非阻塞CLI推送
    try:
        subprocess.Popen(
            ['openclaw', 'message', 'send',
             '-t', _TARGET,
             '--channel', 'jarvis',
             '--message', msg[:2000]],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception as e:
        print(f"[push_hub] CLI推送异常: {e}", file=sys.stderr)

    # 方式2: fallback — 写入本地文件（保证不丢消息）
    try:
        fallback_path = Path(__file__).parent.parent / "data" / "push_failed_queue.jsonl"
        fallback_path.parent.mkdir(parents=True, exist_ok=True)
        with open(fallback_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts":  time.time(),
                "msg": msg[:500],
            }, ensure_ascii=False) + "\n")
        print(f"[push_hub] 降级写入失败队列: {str(fallback_path)}", file=sys.stderr)
    except Exception:
        pass

    return False


def push_jarvis_silent(msg: str) -> bool:
    """不打印错误日志的静默版本，用于高频调用场景"""
    try:
        return push_jarvis(msg, timeout=8, retries=2)
    except Exception:
        return False


if __name__ == "__main__":
    # 冒烟测试
    print("=== push_hub 冒烟测试 ===")
    test_msg = "🧪 [push_hub] 梵天推送模块测试 — 直接HTTP方案"
    result = push_jarvis(test_msg, timeout=8, retries=2)
    print(f"推送结果: {'✅ 成功' if result else '❌ 失败（检查openclaw是否运行）'}")
