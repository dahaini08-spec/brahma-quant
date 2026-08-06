"""
dharma_online_learner.py — 兼容层 shim
设计院 2026-08-06

原 dharma_online_learner 功能已由 online_learner_v2 实现。
此文件为兼容层，转发 ev_feedback 的调用到 online_learner_v2.run()
"""

from online_learner_v2 import run as _run_v2


def run_online_learning(signals: list = None, dry_run: bool = False) -> dict:
    """
    兼容 ev_feedback 的调用接口。
    转发到 online_learner_v2.run()
    """
    try:
        result = _run_v2(dry_run=dry_run)
        return result if isinstance(result, dict) else {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}
