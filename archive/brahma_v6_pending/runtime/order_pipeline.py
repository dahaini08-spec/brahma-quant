"""
brahma_v6/runtime/order_pipeline.py — Full signal→risk→intent→adapter pipeline
Phase 5 | 2026-07-09
"""
from __future__ import annotations
import uuid
import time
import logging
from dataclasses import dataclass, field
from typing import Optional, Any

from brahma_v6.risk.risk_kernel import RiskKernel, Signal, AccountState
from brahma_v6.risk.models import RiskAction
from brahma_v6.runtime.signal_consumer import SignalConsumer, RawSignal, OrderIntentRequest
from brahma_v6.runtime.order_intent_factory import OrderIntentFactory, OrderIntent
from brahma_v6.adapters.live_binance_adapter import LiveBinanceAdapter, AdapterOrderEvent

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Result of processing one signal through the full pipeline."""
    trace_id: str
    signal_id: str
    stage: str                    # FILTERED | BLOCKED | SUBMITTED | UNKNOWN | ERROR
    risk_action: Optional[str] = None
    risk_reason: Optional[str] = None
    intent: Optional[Any] = None
    adapter_event: Optional[AdapterOrderEvent] = None
    error: Optional[str] = None
    ts: float = field(default_factory=time.time)

    def success(self) -> bool:
        return self.stage == "SUBMITTED"


class OrderPipeline:
    """
    Full order pipeline:
      RawSignal → SignalConsumer → RiskKernel → OrderIntentFactory → LiveBinanceAdapter

    Error handling:
      - Filter rejection: returns PipelineResult(stage=FILTERED)
      - Risk block: returns PipelineResult(stage=BLOCKED)
      - Adapter unknown: returns PipelineResult(stage=UNKNOWN), push to DLQ
      - Exceptions: returns PipelineResult(stage=ERROR)
    """

    def __init__(
        self,
        signal_consumer: SignalConsumer,
        risk_kernel: RiskKernel,
        intent_factory: OrderIntentFactory,
        adapter: LiveBinanceAdapter,
        account_state_provider=None,
        dlq=None,
    ) -> None:
        self.signal_consumer = signal_consumer
        self.risk_kernel = risk_kernel
        self.intent_factory = intent_factory
        self.adapter = adapter
        self._account_state_provider = account_state_provider
        self._dlq = dlq

    def _get_account_state(self) -> AccountState:
        if self._account_state_provider:
            return self._account_state_provider()
        return AccountState()

    def process(self, raw_signal: RawSignal) -> PipelineResult:
        """Process a single signal through the entire pipeline."""
        trace_id = uuid.uuid4().hex

        try:
            # Stage 1: Signal consumer (score/regime/ev filter)
            intent_req = self.signal_consumer.consume(raw_signal)
            if intent_req is None:
                return PipelineResult(
                    trace_id=trace_id,
                    signal_id=raw_signal.signal_id,
                    stage="FILTERED",
                )

            # Stage 2: Risk kernel
            signal = Signal(
                symbol=intent_req.symbol,
                side=intent_req.side,
                score=intent_req.score,
                order_type=intent_req.order_type,
                price=intent_req.price,
                quantity=intent_req.quantity,
                ev_bucket_action=intent_req.ev_bucket_action,
                reduce_only=intent_req.reduce_only,
                signal_id=intent_req.signal_id,
            )
            account_state = self._get_account_state()
            decision = self.risk_kernel.evaluate(signal, account_state)

            if decision.is_blocked():
                return PipelineResult(
                    trace_id=trace_id,
                    signal_id=raw_signal.signal_id,
                    stage="BLOCKED",
                    risk_action=decision.action.value,
                    risk_reason=decision.reason,
                )

            # Stage 3: Build OrderIntent
            intent = self.intent_factory.build(intent_req, decision, trace_id=trace_id)
            if intent is None:
                return PipelineResult(
                    trace_id=trace_id,
                    signal_id=raw_signal.signal_id,
                    stage="ERROR",
                    error="intent_factory returned None despite APPROVE decision",
                )

            # Stage 4: Submit to adapter
            proposed = intent.to_proposed_order()
            adapter_event = self.adapter.submit(proposed)

            stage = "SUBMITTED" if adapter_event.event_type in ("SUBMITTED", "TEST_OK") else adapter_event.event_type

            # DLQ for UNKNOWN events
            if stage == "UNKNOWN" and self._dlq:
                self._dlq.push(
                    {"trace_id": trace_id, "intent_id": intent.intent_id, "event": adapter_event.raw},
                    reason=f"adapter_unknown: {adapter_event.error}",
                )

            return PipelineResult(
                trace_id=trace_id,
                signal_id=raw_signal.signal_id,
                stage=stage,
                risk_action=decision.action.value,
                risk_reason=decision.reason,
                intent=intent,
                adapter_event=adapter_event,
            )

        except Exception as e:
            logger.exception(f"OrderPipeline.process error: {e}")
            return PipelineResult(
                trace_id=trace_id,
                signal_id=raw_signal.signal_id,
                stage="ERROR",
                error=str(e),
            )
