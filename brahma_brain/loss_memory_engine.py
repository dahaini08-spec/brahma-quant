"""
import sys
loss_memory_engine.py — 亏损记忆引擎
设计院 2026-09-20 苏摩111

使命：把每次交易的场景指纹+outcome存入，下次相似场景自动提醒
核心：不是统计聚合（WR矩阵），而是具体场景的记忆

三层结构：
  1. 场景指纹提取 — 从分析结果提取12维指纹
  2. 记忆存储 — JSONL追加写入，永久保存
  3. 相似检索 — 下次分析时查询相似场景的outcome
"""
import json, sys, time, os
from pathlib import Path
from collections import defaultdict

_BASE = Path(__file__).parent.parent
_MEMORY_FILE = _BASE / 'data' / 'loss_memory.jsonl'
_INDEX_FILE = _BASE / 'data' / 'loss_memory_index.json'

# 12维场景指纹
FINGERPRINT_KEYS = [
    'symbol', 'regime', 'signal_dir', 'score_final',
    'rsi_1h', 'rsi_4h', 'bb_width',
    'hurst', 'hurst_1d', 'hurst_4h',
    'fvg_consensus', 'oi_signal',
]


def extract_fingerprint(analysis_result: dict) -> dict:
    """从分析结果提取12维场景指纹"""
    r = analysis_result
    ms = r.get('market_state', r)  # 兼容嵌套
    
    fp = {
        'symbol': r.get('symbol', ''),
        'regime': r.get('regime', ''),
        'signal_dir': r.get('signal_dir', ''),
        'score_final': float(r.get('score_final', r.get('score', 0)) or 0),
        'rsi_1h': float(r.get('rsi_1h', ms.get('rsi_1h', 50)) or 50),
        'rsi_4h': float(r.get('rsi_4h', ms.get('rsi_4h', 50)) or 50),
        'bb_width': float(r.get('bb_width_4h', ms.get('bb_width_4h', 20)) or 20),
        'hurst': float(r.get('hurst', 0.5) or 0.5),
        'hurst_1d': float(r.get('hurst_1d', 0.5) or 0.5),
        'hurst_4h': float(r.get('hurst_4h', 0.5) or 0.5),
        'fvg_consensus': r.get('fvg_consensus', ''),
        'oi_signal': r.get('oi_signal', r.get('sentiment', {}).get('oi_signal', '')),
    }
    return fp


def record_trade(fingerprint: dict, outcome: dict) -> bool:
    """
    记录一笔交易的场景指纹+outcome
    
    outcome格式:
      {
        'result': 'WIN'|'LOSS'|'TIMEOUT',
        'pnl_pct': float,
        'entry_price': float,
        'exit_price': float,
        'stop_loss': float,
        'tp_target': float,
        'hold_hours': float,
        'exit_reason': str,  # 'TP1命中'|'止损'|'超时'|'手动平仓'
        'narrative': str,     # 交易叙事（入场理由）
      }
    """
    try:
        record = {
            'ts': time.time(),
            'ts_iso': time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime()),
            **fingerprint,
            'outcome': outcome,
        }
        with open(_MEMORY_FILE, 'a') as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
        return True
    except Exception as e:
        print(f'[WARN] {__name__}: 记录失败: {e}', file=sys.stderr)
        return False


def query_similar_memory(fingerprint: dict, top_k: int = 5, sym_filter: bool = True) -> dict:
    """
    查询相似的历史交易记忆
    
    返回:
      {
        'n': int,
        'win_rate': float,
        'avg_pnl': float,
        'cases': [{...fingerprint, ...outcome, similarity}]
        'warning': str,  # 如果亏损率>60%，生成警告
      }
    """
    if not _MEMORY_FILE.exists():
        return {'n': 0, 'win_rate': 0.5, 'avg_pnl': 0, 'cases': [], 'warning': ''}
    
    try:
        records = []
        with open(_MEMORY_FILE) as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line))
    except Exception as e:
        print(f'[WARN] {__name__}: 读取失败: {e}', file=sys.stderr)
        return {'n': 0, 'win_rate': 0.5, 'avg_pnl': 0, 'cases': [], 'warning': ''}
    
    if not records:
        return {'n': 0, 'win_rate': 0.5, 'avg_pnl': 0, 'cases': [], 'warning': ''}
    
    # 计算相似度（简单加权距离）
    scored = []
    for r in records:
        # 标的过滤
        if sym_filter and r.get('symbol') != fingerprint.get('symbol'):
            continue
        
        # 体制+方向必须匹配
        if r.get('regime') != fingerprint.get('regime'):
            continue
        if r.get('signal_dir') != fingerprint.get('signal_dir'):
            continue
        
        # 计算数值特征距离
        dist = 0
        for key in ['rsi_1h', 'rsi_4h', 'bb_width', 'hurst', 'hurst_1d', 'hurst_4h', 'score_final']:
            v1 = float(r.get(key, 0) or 0)
            v2 = float(fingerprint.get(key, 0) or 0)
            dist += abs(v1 - v2)
        
        # FVG共识匹配
        if r.get('fvg_consensus') == fingerprint.get('fvg_consensus'):
            dist -= 5  # 匹配则减距离
        
        scored.append((dist, r))
    
    scored.sort(key=lambda x: x[0])
    top = scored[:top_k]
    
    if not top:
        return {'n': 0, 'win_rate': 0.5, 'avg_pnl': 0, 'cases': [], 'warning': ''}
    
    # 统计
    wins = sum(1 for _, r in top if r.get('outcome', {}).get('result') == 'WIN')
    losses = sum(1 for _, r in top if r.get('outcome', {}).get('result') == 'LOSS')
    pnls = [float(r.get('outcome', {}).get('pnl_pct', 0) or 0) for _, r in top]
    avg_pnl = sum(pnls) / len(pnls) if pnls else 0
    wr = wins / len(top) if top else 0.5
    
    cases = []
    for dist, r in top:
        cases.append({
            'ts': r.get('ts_iso', ''),
            'symbol': r.get('symbol', ''),
            'regime': r.get('regime', ''),
            'signal_dir': r.get('signal_dir', ''),
            'score_final': r.get('score_final', 0),
            'rsi_1h': r.get('rsi_1h', 0),
            'result': r.get('outcome', {}).get('result', ''),
            'pnl_pct': r.get('outcome', {}).get('pnl_pct', 0),
            'exit_reason': r.get('outcome', {}).get('exit_reason', ''),
            'narrative': r.get('outcome', {}).get('narrative', ''),
            'similarity_dist': round(dist, 2),
        })
    
    # 生成警告
    warning = ''
    if len(top) >= 3 and losses > wins:
        if wr <= 0.3:
            warning = f'⚠️ 亏损记忆警告: 相似{len(top)}个场景中{losses}亏{wins}胜 (WR={wr:.0%})，建议减仓或观望'
        elif wr <= 0.4:
            warning = f'⚠️ 亏损记忆提醒: 相似{len(top)}个场景中{losses}亏{wins}胜 (WR={wr:.0%})，建议谨慎'
    
    return {
        'n': len(top),
        'win_rate': round(wr, 3),
        'avg_pnl': round(avg_pnl, 2),
        'cases': cases,
        'warning': warning,
    }


def get_stats() -> dict:
    """返回记忆库统计"""
    if not _MEMORY_FILE.exists():
        return {'total': 0, 'active': True}
    try:
        count = 0
        wins = 0
        losses = 0
        with open(_MEMORY_FILE) as f:
            for line in f:
                if line.strip():
                    count += 1
                    r = json.loads(line)
                    result = r.get('outcome', {}).get('result', '')
                    if result == 'WIN':
                        wins += 1
                    elif result == 'LOSS':
                        losses += 1
        return {
            'total': count,
            'wins': wins,
            'losses': losses,
            'win_rate': round(wins / count, 3) if count > 0 else 0,
            'active': True,
        }
    except Exception as _e:
        return {'total': 0, 'active': True}
