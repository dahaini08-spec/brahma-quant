"""
signal_gates.py — 信号门控检查模块（集中管理所有门）
职责：单一。输入信号参数 → 通过/拦截+原因
"""

def check_gap_gate(gap_pct: float, score: float, gate_cfg: dict) -> tuple[bool, str]:
    """GapGate：入场区偏离检查"""
    if gap_pct < 0.3:
        return False, f"gap={gap_pct:.2f}%<0.3% 入场区已被价格侵蚀"
    if 0.3 <= gap_pct < 0.8 and score < 165:
        return False, f"gap={gap_pct:.2f}%需score≥165，当前{score:.0f}"
    if 0.8 <= gap_pct < 1.5 and score < 150:
        return False, f"gap={gap_pct:.2f}%需score≥150，当前{score:.0f}"
    if gap_pct >= 5.0:
        return False, f"gap={gap_pct:.2f}%>5% 极偏远，拦截"
    return True, "pass"

def check_rr_gate(rr: float, min_rr: float = 1.5) -> tuple[bool, str]:
    """RR门：风险收益比检查"""
    if rr < min_rr:
        return False, f"RR={rr:.2f}<{min_rr}"
    return True, "pass"

def check_grade_gate(grade: float, min_grade: float = 70) -> tuple[bool, str]:  # [v24.2] 50→70
    """BridgeGate：结构质量门（v24.2 全系统grade<70封堵）"""
    if grade < min_grade:
        return False, f"grade={grade:.0f}<{min_grade} 结构噪音"
    return True, "pass"

def check_score_gate(score: float, regime: str, symbol: str,
                     dynamic_gates: dict = None) -> tuple[bool, str]:
    """动态识别门"""
    if dynamic_gates and (symbol, regime) in dynamic_gates:
        cfg = dynamic_gates[(symbol, regime)]
        return True, "dynamic_gate_check_in_commander"
    return True, "pass"
