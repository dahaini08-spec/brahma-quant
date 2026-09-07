"""Brahma OS v7 — research spine, not a live switch."""

from brahma_os.adapter import AdapterError, analyze_to_signal
from brahma_os.config import Settings, load_settings
from brahma_os.contracts import Fill, Intent, Position, Signal
from brahma_os.costs import CostModel
from brahma_os.gates import GateDecision, evaluate_gates
from brahma_os.ledger import EquityLedger
from brahma_os.monte_carlo import MCSummary, run_worlds
from brahma_os.paper_bridge import PaperDecision, decide_from_analyze
from brahma_os.research import ResearchVerdict, verdict
from brahma_os.settlement import SettlementEngine, SettlementRule
from brahma_os.simulator import PathSimulator, SimReport

__all__ = [
    "AdapterError",
    "analyze_to_signal",
    "Settings",
    "load_settings",
    "Signal",
    "Intent",
    "Fill",
    "Position",
    "CostModel",
    "GateDecision",
    "evaluate_gates",
    "EquityLedger",
    "PaperDecision",
    "decide_from_analyze",
    "SettlementEngine",
    "SettlementRule",
    "PathSimulator",
    "SimReport",
    "run_worlds",
    "MCSummary",
    "verdict",
    "ResearchVerdict",
]
__version__ = "7.1.0"
