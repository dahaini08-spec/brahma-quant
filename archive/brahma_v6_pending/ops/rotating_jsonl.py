"""
brahma_v6/ops/rotating_jsonl.py
滚动 JSONL 日志 — 防止 live_signal_log 等无限增长
裁决封印: 2026-07-09
"""
import gzip
import shutil
from pathlib import Path


class RotatingJsonl:
    """
    追加写 JSONL，超过 max_mb 时自动滚动压缩。
    保留最近 keep 个 .gz 文件。
    """

    def __init__(self, path: str, max_mb: int = 20, keep: int = 5):
        self.path      = Path(path)
        self.max_bytes = max_mb * 1024 * 1024
        self.keep      = keep
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, line: str) -> None:
        self._rotate_if_needed()
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line.rstrip() + "\n")

    def _rotate_if_needed(self) -> None:
        if not self.path.exists():
            return
        if self.path.stat().st_size < self.max_bytes:
            return

        # 找下一个可用序号
        idx = 1
        while True:
            gz = self.path.with_suffix(self.path.suffix + f".{idx}.gz")
            if not gz.exists():
                break
            idx += 1

        with self.path.open("rb") as src, gzip.open(gz, "wb") as dst:
            shutil.copyfileobj(src, dst)
        self.path.unlink()

        # 清理旧归档
        archives = sorted(
            self.path.parent.glob(self.path.name + ".*.gz"),
            key=lambda p: int(p.suffixes[-2].lstrip("."))
            if len(p.suffixes) >= 2 else 0
        )
        for old in archives[: -self.keep]:
            old.unlink(missing_ok=True)
