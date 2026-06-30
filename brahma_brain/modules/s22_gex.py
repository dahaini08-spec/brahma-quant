"""
s22_gex.py — GEX Gamma Exposure评分模块
职责：单一。输入symbol+方向 → 输出(score, detail, gex_data)
可独立测试、独立替换
"""
import sys, os

def get_score(symbol: str, signal_dir: str) -> tuple[int, str, dict]:
    """
    计算s22 GEX分数
    Returns: (score: int[-10,+8], reason: str, gex_data: dict)
    """
    try:
        _bb_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for p in [_bb_dir, os.path.dirname(_bb_dir)]:
            if p not in sys.path: sys.path.insert(0, p)
        from gex_engine import get_gex_data, score_gex_signal
        gex_data = get_gex_data(symbol)
        res = score_gex_signal(symbol, signal_dir, gex_data)
        s22 = max(-10, min(8, res.get('s22', 0)))
        return s22, res.get('reason',''), gex_data
    except Exception as e:
        return 0, f"s22 unavailable: {e}", {}
