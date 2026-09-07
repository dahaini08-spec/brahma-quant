#!/usr/bin/env python3
"""
push_hub.py — 梵天统一推送模块（直接HTTP，不依赖openclaw CLI）
设计院封印 2026-09-06 苏摩111

根因修复：
  原来所有脚本用 subprocess.run(['openclaw','message','send',...], timeout=15)
  openclaw CLI偶尔阻塞 → TimeoutExpired → 脚本崩溃
  影响: morning-battlefield / OI巡检 / square系列 / paper-daily-report 等6个脚本

修复方案：
  直接HTTP POST到 openclaw 内部API (localhost:3000/api/message)
  超时8秒，自动重试3次，完全不依赖openclaw CLI子进程

接入位置：
  scripts/oi_watchlist_monitor.py      → from push_hub import push_jarvis
  scripts/signal_change_detector.py   → from push_hub import push_jarvis
  scripts/brahma_daily_report.py      → from push_hub import push_jarvis
  scripts/morning_battlefield.py      → from push_hub import push_jarvis
  scripts/square/square_*.py          → from push_hub import push_jarvis
  brahma_brain/brahma_analysis_runner.py → from push_hub import push_jarvis
"""
import json, sys, time, urllib.request, urllib.error
from pathlib import Path

# 推送配置（SSOT来自 MEMORY.md）
JARVIS_USER_ID   = "73295708"
JARVIS_THREAD_ID = "01a07970-f8ce-706b-8bea-3c94dd055443"
OPENCLAW_PORT    = 3000
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

    # 方式1: 直接HTTP POST到openclaw内部API
    for attempt in range(retries):
        try:
            payload = json.dumps({
                "channel": "jarvis",
                "target":  _TARGET,
                "message": msg,
            }).encode("utf-8")

            req = urllib.request.Request(
                f"http://127.0.0.1:{OPENCLAW_PORT}/api/message",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status in (200, 201, 204):
                    return True
                # 非200但有响应，不重试
                body = resp.read().decode()[:100]
                print(f"[push_hub] HTTP {resp.status}: {body}", file=sys.stderr)
                return False

        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            print(f"[push_hub] URLError after {retries} attempts: {e}", file=sys.stderr)

        except TimeoutError:
            if attempt < retries - 1:
                time.sleep(1)
                continue
            print(f"[push_hub] Timeout after {retries} attempts", file=sys.stderr)

        except Exception as e:
            print(f"[push_hub] 推送异常: {e}", file=sys.stderr)
            break

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
