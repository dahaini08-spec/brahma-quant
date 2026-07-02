#!/usr/bin/env python3
"""
memory_watchdog.py — 梵天内存水位监控
设计院封印 2026-07-02

功能：
- 实时监控进程内存占用
- >70% 警告 → 触发智能GC
- >85% 危险 → 清空brahma_bus非必要缓存
- >95% 致命 → 推送Jarvis告警
"""
import os
import gc
import sys
import time
import logging
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

logger = logging.getLogger(__name__)

# 阈值配置
WARN_PCT = 70     # 警告水位
DANGER_PCT = 85   # 危险水位  
CRITICAL_PCT = 95 # 致命水位


def get_memory_mb() -> float:
    """获取当前进程RSS内存(MB)"""
    try:
        with open(f'/proc/{os.getpid()}/status') as f:
            for line in f:
                if 'VmRSS' in line:
                    return int(line.split()[1]) / 1024  # kB → MB
    except Exception:
        pass
    return 0.0


def get_system_memory_pct() -> float:
    """获取系统内存使用率(%)"""
    try:
        with open('/proc/meminfo') as f:
            data = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    data[parts[0].rstrip(':')] = int(parts[1])
        total = data.get('MemTotal', 0)
        available = data.get('MemAvailable', 0)
        if total > 0:
            return 100 * (total - available) / total
    except Exception:
        pass
    return 0.0


def flush_brahma_bus_cache() -> int:
    """清空brahma_bus中的陈旧缓存，返回释放的条目数"""
    freed = 0
    try:
        from brahma_brain.brahma_bus import BrahmaBus
        bus = BrahmaBus.instance() if hasattr(BrahmaBus, 'instance') else None
        if bus and hasattr(bus, '_cache'):
            now = time.time()
            stale_keys = []
            for key, (val, ts, ttl) in list(bus._cache.items()):
                if now - ts > ttl:
                    stale_keys.append(key)
            for key in stale_keys:
                del bus._cache[key]
                freed += 1
            logger.info(f"[MemWatchdog] 清理陈旧缓存 {freed} 条")
    except Exception as e:
        logger.debug(f"[MemWatchdog] 缓存清理跳过: {e}")
    return freed


def check_and_handle(push_alert: bool = False) -> dict:
    """
    检查内存水位并处理
    返回状态字典
    """
    rss_mb = get_memory_mb()
    sys_pct = get_system_memory_pct()
    
    status = {
        'rss_mb': rss_mb,
        'sys_pct': sys_pct,
        'level': 'OK',
        'actions': [],
    }

    if sys_pct >= CRITICAL_PCT:
        status['level'] = 'CRITICAL'
        # 激进清理
        freed = flush_brahma_bus_cache()
        gc.collect()
        status['actions'].append(f'强制GC + 清缓存{freed}条')
        logger.critical(f"[MemWatchdog] 🚨 内存致命 {sys_pct:.1f}% | RSS={rss_mb:.0f}MB")
        if push_alert:
            try:
                import subprocess
                subprocess.Popen([
                    'python3', str(BASE / 'scripts' / 'signal_router.py'),
                    '--alert', f'内存致命警报: {sys_pct:.1f}%占用'
                ])
            except Exception:
                pass

    elif sys_pct >= DANGER_PCT:
        status['level'] = 'DANGER'
        freed = flush_brahma_bus_cache()
        gc.collect()
        status['actions'].append(f'缓存清理{freed}条 + GC')
        logger.warning(f"[MemWatchdog] ⚠️ 内存危险 {sys_pct:.1f}% | RSS={rss_mb:.0f}MB")

    elif sys_pct >= WARN_PCT:
        status['level'] = 'WARN'
        gc.collect()
        status['actions'].append('触发GC')
        logger.info(f"[MemWatchdog] 内存警告 {sys_pct:.1f}%")

    else:
        logger.debug(f"[MemWatchdog] 内存正常 {sys_pct:.1f}% | RSS={rss_mb:.0f}MB")

    return status


def start_daemon(interval_sec: int = 60) -> None:
    """启动内存守护守护（阻塞）"""
    logger.info(f"[MemWatchdog] 启动，监控间隔 {interval_sec}s")
    while True:
        try:
            status = check_and_handle(push_alert=True)
            if status['level'] != 'OK':
                print(f"[{time.strftime('%H:%M:%S')}] {status['level']} "
                      f"sys={status['sys_pct']:.1f}% rss={status['rss_mb']:.0f}MB "
                      f"actions={status['actions']}")
        except Exception as e:
            logger.error(f"[MemWatchdog] 守护异常: {e}")
        time.sleep(interval_sec)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='单次检查')
    parser.add_argument('--daemon', action='store_true', help='守护模式')
    parser.add_argument('--interval', type=int, default=60)
    args = parser.parse_args()

    if args.daemon:
        start_daemon(args.interval)
    else:
        status = check_and_handle()
        sys_pct = status['sys_pct']
        rss_mb = status['rss_mb']
        level = status['level']
        icon = {'OK': '🟢', 'WARN': '🟡', 'DANGER': '🟠', 'CRITICAL': '🔴'}.get(level, '⚪')
        print(f"{icon} 内存状态: {level}")
        print(f"   系统占用: {sys_pct:.1f}%")
        print(f"   进程RSS: {rss_mb:.0f} MB")
        if status['actions']:
            print(f"   已执行: {', '.join(status['actions'])}")
