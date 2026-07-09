"""
brahma_v6/runtime/lowmem_guard.py
LowMem Runtime Guard — RSS 监控 + 自动限流/关闭
裁决封印: 2026-07-09
"""
import gc
import os
import time
import logging

logger = logging.getLogger("brahma.lowmem_guard")


def rss_mb() -> float:
    """读取当前进程 RSS (MB)。"""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except (FileNotFoundError, ValueError):
        return -1.0
    return -1.0


class LowMemGuard:
    """
    主循环每轮调用 tick()。
    - warn       → log warning
    - no_new_pos → 通知 mode_policy 停止新开仓
    - shutdown   → 触发优雅退出
    """

    def __init__(self, mode_policy=None):
        self.mode_policy = mode_policy
        self.warn_mb     = int(os.getenv("BRAHMA_RSS_WARN_MB",           "300"))
        self.no_new_mb   = int(os.getenv("BRAHMA_RSS_NO_NEW_POSITION_MB","450"))
        self.shutdown_mb = int(os.getenv("BRAHMA_RSS_SHUTDOWN_MB",       "600"))
        self._last_gc    = 0.0
        self._last_log   = 0.0
        self._gc_interval  = 120   # gc 每 2 分钟一次
        self._log_interval = 60    # rss 日志每 1 分钟一次

    def tick(self) -> float:
        """检查 RSS，触发 gc / 限流 / 关闭。返回当前 rss_mb。"""
        now = time.time()

        # 定期 gc
        if now - self._last_gc > self._gc_interval:
            gc.collect()
            self._last_gc = now

        rss = rss_mb()

        # 定期打印
        if now - self._last_log > self._log_interval:
            logger.info("[LowMemGuard] rss=%.1f MB  warn=%d no_new=%d shutdown=%d",
                        rss, self.warn_mb, self.no_new_mb, self.shutdown_mb)
            self._last_log = now

        if rss < 0:
            return rss  # 读取失败，跳过

        if rss >= self.shutdown_mb:
            logger.error("[LowMemGuard] rss=%.1f >= shutdown=%d → graceful shutdown",
                         rss, self.shutdown_mb)
            raise SystemExit(0)

        if rss >= self.no_new_mb:
            logger.warning("[LowMemGuard] rss=%.1f >= no_new_pos=%d → 暂停新开仓",
                           rss, self.no_new_mb)
            if self.mode_policy and hasattr(self.mode_policy, "no_new_position"):
                self.mode_policy.no_new_position("rss_high")

        elif rss >= self.warn_mb:
            logger.warning("[LowMemGuard] rss=%.1f >= warn=%d", rss, self.warn_mb)

        return rss
