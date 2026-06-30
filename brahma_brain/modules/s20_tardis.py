"""
s20_tardis.py — Tardis清算墙评分模块
职责：单一。输入symbol+方向+入场区 → 输出(score, detail)
可独立测试、独立替换
"""
import sys, os

def get_score(symbol: str, signal_dir: str, entry_lo: float, entry_hi: float) -> tuple[int, str]:
    """
    计算s20 Tardis清算墙分数
    Returns: (score: int[-5,+5], detail: str)
    """
    try:
        _bb_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _root   = os.path.dirname(_bb_dir)
        for p in [_root, os.path.join(_root,'scripts')]:
            if p not in sys.path: sys.path.insert(0, p)
        from liq_scanner import get_tardis_score
        return get_tardis_score(symbol, signal_dir, entry_lo, entry_hi)
    except Exception as e:
        return 0, f"s20 unavailable: {e}"
