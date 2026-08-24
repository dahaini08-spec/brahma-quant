# brahma_brain package — v25.0 模块化架构（3模块拆分封印 2026-07-12）
#
# 架构层级（拆分后）:
#   brahma_scoring.py        → 35维评分引擎 confluence_score() 纯函数 (2035行)
#   brahma_trade.py          → 出场参数辅助函数 (49行)
#   brahma_engine.py         → analyze() 主入口 (3271行)
#   brahma_core.py           → 转发兼容层（保留，外部import不断）
#   brahma_orchestrator.py   → 编排器
#   modules/s20_tardis.py    → Tardis清算墙评分
#   modules/s22_gex.py       → GEX Gamma评分
#   modules/signal_gates.py  → 信号门控集中管理
#
# 向后兼容: brahma_core.py 保持原有所有符号，旧import路径100%兼容

# 优先从新拆分模块导入
try:
    from brahma_brain.brahma_engine import analyze
    from brahma_brain.brahma_scoring import confluence_score
    from brahma_brain.brahma_trade import calc_trade_params
except Exception:
    # 降级兼容：从原brahma_core导入
    from brahma_brain.brahma_core import analyze, confluence_score, calc_trade_params

from brahma_brain.brahma_core import format_report
# formatter也可直接import
from brahma_brain.formatter import format_report as format_report_v2

__all__ = ['analyze', 'confluence_score', 'calc_trade_params', 'format_report']

# 向后兼容：部分脚本仍用 from brahma_brain.brahma_brain import analyze
# 通过创建兼容符号解决
import sys as _sys
import brahma_brain.brahma_core as _core
# 注册 brahma_brain.brahma_brain 作为 brahma_core 的别名
_sys.modules['brahma_brain.brahma_brain'] = _core
