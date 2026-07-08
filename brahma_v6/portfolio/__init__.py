"""brahma_v6.portfolio — 仓位管理模块"""
# rl_position_ab 实体在 brahma_brain，此处提供兼容别名
try:
    import brahma_brain.rl_position_ab as rl_position_ab
    from brahma_brain.rl_position_ab import (
        decide_position_size,
        evaluate_ab_performance,
        get_rl_suggestion,
    )
except ImportError:
    rl_position_ab = None
    decide_position_size = None
    evaluate_ab_performance = None
    get_rl_suggestion = None

__all__ = ['rl_position_ab', 'decide_position_size', 'evaluate_ab_performance', 'get_rl_suggestion']
