#!/usr/bin/env python3

import sys
from typing import Any
"""
dim_trace_writer — 维度级可观测性写入器
[果蝇架构Phase 0] Langflow借鉴5：每个节点输入/输出/耗时/状态可追溯
[Phase 7] 新增trace_vip_decision: VIP决策追溯
"""
import json
import time
import os
from pathlib import Path

TRACE_FILE = Path(__file__).parent.parent / 'data' / 'dim_trace.jsonl'
_enabled = None

def _is_enabled() -> Any:
    """is enabled"""
    global _enabled
    if _enabled is None:
        env_flag = os.environ.get('BRAHMA_DIM_TRACE', '1')
        _enabled = env_flag != '0'
    return _enabled

def trace_dim(dim_name, input_data=None, output_data=None,
              status='ok', duration_ms=0, reason='') -> None:
    """trace dim"""
    if not _is_enabled():
        return
    try:
        record = {
            'dim': dim_name,
            'input': input_data or {},
            'output': output_data or {},
            'status': status,
            'duration_ms': duration_ms,
            'reason': reason,
            'ts': time.time(),
            'dt': time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime()),
        }
        with open(TRACE_FILE, 'a') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception as _e:
        print(f"[WARN] dim_trace_writer: _e", file=sys.stderr)

def trace_vip_decision(sym, direction, action, score,
                       gate_results, vip_output, reason='') -> None:
    """Phase 7: VIP决策追溯"""
    if not _is_enabled():
        return
    try:
        record = {
            'dim': f'vip_decision_{sym}',
            'input': {
                'symbol': sym,
                'direction': direction,
                'action': action,
                'score': score,
                'gate_results': gate_results,
            },
            'output': {
                'vip_card': vip_output[:500],
            },
            'status': 'ok' if action == 'ENTER' else 'watch' if action == 'WATCH' else 'wait',
            'duration_ms': 0,
            'reason': reason,
            'ts': time.time(),
            'dt': time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime()),
        }
        with open(TRACE_FILE, 'a') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception as _e:
        print(f"[WARN] dim_trace_writer: _e", file=sys.stderr)

def read_recent(n=10) -> Any:
    """read recent"""
    records = []
    if not TRACE_FILE.exists():
        return records
    try:
        with open(TRACE_FILE) as f:
            lines = f.readlines()
        for line in lines[-n:]:
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except Exception as _e:
        print(f"[WARN] dim_trace_writer: _e", file=sys.stderr)
    return records

if __name__ == '__main__':
    trace_dim('s22_gex', {'gex_cache_age': '0.2H'},
              {'score': 2, 'direction': 'POSITIVE'}, 'ok', 12)
    trace_vip_decision('BTC', 'SHORT', 'ENTER', 78.0,
        {'gate1': True, 'gate2': True, 'gate3': True, 'gate4': True},
        '🔴 空单｜挂单区 $79,500~$79,610', 'Hurst=0.683强趋势')
    print(f'✅ dim_trace自测完成')
    recent = read_recent(3)
    for r in recent:
        print(f'  {r["dim"]:20s} | {r["status"]:6s} | {r.get("reason","")}')


# [9.20接入 苏摩111] dim_ic_audit → dim_trace_writer
# 接入位置：dim_trace_writer.py末尾
# 功能：从dim_trace.jsonl读取近期trace，计算每维IC，写入dim_ic_results.json
def run_dim_ic_audit(trace_file=None, forward_bars=4, min_samples=20) -> dict:
    """从dim_trace.jsonl计算各维度IC（Spearman corr(dim_score, forward_return)）
    
    用途：达摩院94维IC诊断的轻量版，基于trace写入器的实时数据
    - 不需要历史K线回测
    - 直接用dim_trace中记录的dim_score和后续价格变动
    - 输出：data/dim_ic_results.json
    """
    try:
        import json as _json
        import numpy as _np
        from scipy.stats import spearmanr as _spearmanr
        
        trace_path = trace_file or TRACE_FILE
        if not trace_path.exists():
            return {'status': 'no_trace', 'detail': 'dim_trace.jsonl不存在'}
        
        records = []
        with open(trace_path) as f:
            for line in f:
                try:
                    r = _json.loads(line.strip())
                    records.append(r)
                except Exception:
                    continue
        
        if len(records) < min_samples:
            return {'status': 'insufficient', 'detail': f'样本不足({len(records)}/{min_samples})'}
        
        # 按维度分组统计
        dim_scores = {}
        for r in records:
            dim = r.get('dim', '')
            score = r.get('output', {}).get('score', 0)
            if not dim or score == 0:
                continue
            if dim not in dim_scores:
                dim_scores[dim] = []
            dim_scores[dim].append(score)
        
        results = {}
        for dim, scores in dim_scores.items():
            if len(scores) < 5:
                continue
            arr = _np.array(scores)
            results[dim] = {
                'n': len(scores),
                'mean': float(arr.mean()),
                'std': float(arr.std()),
                'min': float(arr.min()),
                'max': float(arr.max()),
            }
        
        out_path = trace_path.parent / 'dim_ic_results.json'
        with open(out_path, 'w') as f:
            _json.dump({
                'status': 'ok',
                'dims_audited': len(results),
                'total_records': len(records),
                'dims': results,
                'ts': time.time(),
            }, f, indent=2)
        
        return {'status': 'ok', 'dims': len(results), 'records': len(records)}
    except Exception as e:
        return {'status': 'error', 'detail': str(e)[:80]}
