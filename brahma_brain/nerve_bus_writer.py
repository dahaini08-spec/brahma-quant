#!/usr/bin/env python3
"""
nerve_bus_writer.py — 梵天统一神经总线
[2026-09-15 苏摩111] 果蝇脑神经元感知层

统一3个写入点：
1. 分析完成 → emit_analysis_done (score/regime/dims)
2. 信号产生 → emit_signal (direction/entry/sl/tp)
3. 系统异常 → emit_alert (error/warning)

读取：任何模块都能读nerve_bus.jsonl知道"系统刚才发生了什么"
"""

import sys
import json, time, os
from pathlib import Path

BUS_FILE = Path(__file__).parent.parent / 'data' / 'nerve_bus.jsonl'

def emit(event_type: str, module: str, level: str = 'INFO',
         msg: str = '', data: dict = None, urgency: str = 'P4') -> None:
    """
    写入神经总线
    event_type: ANALYSIS_DONE | SIGNAL | ALERT | ENGINE_FAIL | REGIME_SWITCH
    module: brahma | regime_watcher | battlefield | w5_paper | ...
    level: INFO | WARN | ERROR
    urgency: P0(critical) | P1(high) | P2(medium) | P3(low) | P4(routine)
    """
    try:
        record = {
            'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'ts_ms': int(time.time() * 1000),
            'module': module,
            'event': event_type,
            'level': level,
            'msg': msg,
            'urgency': urgency,
            'data': data or {},
            'pid': os.getpid(),
        }
        with open(BUS_FILE, 'a') as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
    except Exception as _e:
        print(f"[WARN] nerve_bus_writer: _e", file=sys.stderr)

def emit_analysis_done(symbol: str, score: float, regime: str, direction: str,
                       dim_count: int, active_dims: list, sleep_dims: list,
                       ev: float = 0, signal: str = 'WATCH') -> None:
    """分析完成事件"""
    emit(
        event_type='ANALYSIS_DONE',
        module='brahma',
        level='INFO',
        msg=f'{symbol} score={score:.1f} regime={regime} dir={direction} signal={signal} EV={ev:+.3f}',
        urgency='P2' if signal != 'WATCH' else 'P4',
        data={
            'symbol': symbol,
            'score': score,
            'regime': regime,
            'direction': direction,
            'signal': signal,
            'ev': ev,
            'dim_count': dim_count,
            'active_dims': active_dims,
            'sleep_dims': sleep_dims,
        }
    )

def emit_signal(symbol: str, direction: str, entry: float, sl: float,
                tp: float, score: float, regime: str, source: str = 'brahma',
                entry_lo: float = None, entry_hi: float = None, signal_id: str = None) -> None:
    """信号产生事件 + 预测记录"""
    emit(
        event_type='SIGNAL',
        module=source,
        level='INFO',
        msg=f'{symbol} {direction} @ ${entry:,.1f} SL=${sl:,.1f} TP=${tp:,.1f} score={score:.1f} regime={regime}',
        urgency='P1',
        data={
            'symbol': symbol,
            'direction': direction,
            'entry': entry,
            'sl': sl,
            'tp': tp,
            'score': score,
            'regime': regime,
        }
    )
    # Phase 2C: 预测编码管道已移除（2026-09-17 苏摩111）

def emit_alert(module: str, msg: str, urgency: str = 'P2', data: dict = None) -> None:
    """系统告警"""
    emit(
        event_type='ALERT',
        module=module,
        level='WARN',
        msg=msg,
        urgency=urgency,
        data=data or {},
    )
