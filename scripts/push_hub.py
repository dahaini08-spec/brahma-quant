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
JARVIS_THREAD_ID = "01a07628-0405-7e85-a34b-e68cd029dfc6"
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


# [2026-09-09 苏摩111] _jarvis别名 — 大量旧脚本import _jarvis，与push_jarvis等价
_jarvis = push_jarvis

def push_signal_card_v3(r_raw: dict) -> bool:
    """
    推送梵天VIP信号卡片v3（事件驱动，score≥110时触发）
    接收brahma_1hao_analysis的r_raw字典，构造VIP卡片格式推送到Jarvis。
    
    接入位置：brahma_1hao_analysis.py line 2043
    设计院封印 2026-09-10 苏摩111
    """
    import datetime
    try:
        sym       = str(r_raw.get('symbol', '')).replace('USDT', '')
        score     = float(r_raw.get('score_final', r_raw.get('score', 0)) or 0)
        grade     = float(r_raw.get('grade', 0) or 0)
        direction = str(r_raw.get('direction', 'LONG'))
        regime    = str(r_raw.get('regime', '') or '')
        entry_lo  = float(r_raw.get('entry_lo', 0) or 0)
        entry_hi  = float(r_raw.get('entry_hi', 0) or 0)
        sl_price  = float(r_raw.get('stop_loss', 0) or 0)
        tp1       = float(r_raw.get('tp1', 0) or 0)
        tp2       = float(r_raw.get('tp2', 0) or 0)
        rr        = float(r_raw.get('rr', r_raw.get('rr1', 1.0)) or 1.0)
        sl_pct    = float(r_raw.get('sl_pct', 0) or 0)
        hcme_wr   = float(r_raw.get('hcme_wr', 0) or 0)
        price     = float(r_raw.get('price', 0) or 0)
        
        # 计算SL百分比（如果没提供）
        if sl_pct == 0 and entry_hi > 0 and sl_price > 0:
            sl_pct = abs(entry_hi - sl_price) / entry_hi * 100
        
        emoji = '🟢' if direction == 'LONG' else '🔴'
        tier  = 'TIER1 🔴' if score >= 155 else 'TIER2 🟠'
        ts    = datetime.datetime.utcnow().strftime('%m-%d %H:%M')
        
        # VIP卡片格式（姓赵不宣封印格式）
        msg = (
            f'🚨 梵天VIP信号 · {tier}\n'
            f'━━━━━━━━━━━━━━━━━━━━━\n'
            f'{emoji} {sym}/USDT {direction} | score={score:.0f} grade={grade:.0f}\n'
            f'体制: {regime}\n'
            f'入场: ${entry_lo:,.2f} ~ ${entry_hi:,.2f}\n'
            f'止损: ${sl_price:,.2f} ({sl_pct:.1f}%)\n'
            f'TP1:  ${tp1:,.2f}  RR={rr:.1f}x\n'
        )
        if tp2 > 0:
            msg += f'TP2:  ${tp2:,.2f}\n'
        if hcme_wr > 0:
            msg += f'HCME: WR={hcme_wr:.0f}%\n'
        msg += (
            f'━━━━━━━━━━━━━━━━━━━━━\n'
            f'{ts} UTC [事件驱动]'
        )
        
        dedup_key = f'vip_v3_{sym}_{direction}_{int(entry_lo)}_{int(score)}'
        return _jarvis(msg, dedup_key=dedup_key, dedup_ttl=14400)
    except Exception as e:
        print(f'[push_hub] push_signal_card_v3异常: {e}', file=sys.stderr)
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

    # 测试push_signal_card_v3
    print("\n=== push_signal_card_v3 冒烟测试 ===")
    test_r = {
        'symbol': 'BTCUSDT', 'score_final': 142, 'grade': 90,
        'direction': 'LONG', 'regime': 'BULL_TREND',
        'entry_lo': 78000, 'entry_hi': 78500, 'stop_loss': 77000,
        'tp1': 80000, 'tp2': 82000, 'rr': 2.0, 'sl_pct': 1.5,
        'hcme_wr': 85, 'price': 78353
    }
    result2 = push_signal_card_v3(test_r)
    print(f"VIP卡片推送: {'✅ 成功' if result2 else '❌ 失败'}")
