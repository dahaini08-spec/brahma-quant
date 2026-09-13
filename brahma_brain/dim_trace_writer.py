#!/usr/bin/env python3
"""
dim_trace_writer — 维度级可观测性写入器
[果蝇架构Phase 0] Langflow借鉴5：每个节点输入/输出/耗时/状态可追溯

使用方式：
    from brahma_brain.dim_trace_writer import trace_dim
    trace_dim('s22_gex', input_data={...}, output_data={...}, status='ok', duration_ms=12)

    # 或作为上下文管理器
    with trace_dim('s3_rsi') as t:
        ...  # 计算逻辑
        t.set_output(score=8, reason='MACD divergence')
"""
import json
import time
import os
from pathlib import Path

TRACE_FILE = Path(__file__).parent.parent / 'data' / 'dim_trace.jsonl'
_enabled = None

def _is_enabled():
    """检查是否启用trace（避免每次分析都写文件）"""
    global _enabled
    if _enabled is None:
        env_flag = os.environ.get('BRAHMA_DIM_TRACE', '1')
        _enabled = env_flag != '0'
    return _enabled

def trace_dim(dim_name: str, input_data: dict = None, output_data: dict = None,
              status: str = 'ok', duration_ms: int = 0, reason: str = ''):
    """写入一条维度trace记录"""
    if not _is_enabled():
        return
    try:
        record = {
            'dim': dim_name,
            'input': input_data or {},
            'output': output_data or {},
            'duration_ms': duration_ms,
            'status': status,  # ok / sleep / error / skip
            'reason': reason,
            'ts': time.time(),
        }
        with open(TRACE_FILE, 'a') as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
    except Exception:
        pass  # trace写入不能影响主流程

class trace_dim_ctx:
    """上下文管理器版本，自动计时"""
    def __init__(self, dim_name: str, input_data: dict = None):
        self.dim_name = dim_name
        self.input_data = input_data or {}
        self.output_data = {}
        self.status = 'ok'
        self.reason = ''
        self._t0 = 0

    def __enter__(self):
        self._t0 = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration_ms = int((time.time() - self._t0) * 1000)
        if exc_type:
            self.status = 'error'
            self.reason = f'{exc_type.__name__}: {exc_val}'
        trace_dim(self.dim_name, self.input_data, self.output_data,
                  self.status, duration_ms, self.reason)
        return False  # 不吞异常

    def set_output(self, **kwargs):
        self.output_data = kwargs

    def set_status(self, status: str, reason: str = ''):
        self.status = status
        self.reason = reason

def flush_trace():
    """手动flush（cron分析结束后调用）"""
    pass  # jsonl模式不需要flush，每条写入即落地

def read_recent(n: int = 100) -> list:
    """读取最近n条trace记录（诊断用）"""
    if not TRACE_FILE.exists():
        return []
    lines = TRACE_FILE.read_text().strip().split('\n')
    records = []
    for line in lines[-n:]:
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records

if __name__ == '__main__':
    # 自测
    with trace_dim_ctx('s3_rsi', {'rsi_1h': 55}) as t:
        t.set_output(score=8, reason='MACD divergence')
        t.set_output(score=8, reason='MACD divergence')

    trace_dim('s22_gex', {'gex_cache_age': '0.2H'},
              {'score': 2, 'direction': 'POSITIVE'}, 'ok', 12)

    trace_dim('s1_trend', {'regime': 'CHOP_MID'},
              {'score': 0}, 'sleep', 0, 'CHOP_MID休眠s1')

    print(f'✅ dim_trace自测完成，写入3条到 {TRACE_FILE}')
    recent = read_recent(3)
    for r in recent:
        print(f'  {r["dim"]:12s} | {r["status"]:6s} | {r["duration_ms"]:4d}ms | {r.get("reason","")}')
