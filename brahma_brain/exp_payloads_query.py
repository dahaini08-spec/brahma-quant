"""
exp_payloads_query.py — 212K experience_payloads KD-Tree查询接口
设计院 2026-09-20 苏摩111

激活212,727条6维经验数据，支持毫秒级最近邻查询。
替代Qdrant方案（无需额外服务，纯Python scipy.spatial.KDTree）。
"""
import json, sys
import numpy as np
from pathlib import Path
from scipy.spatial import KDTree

_BASE = Path(__file__).parent.parent
_NORM_FILE = _BASE / 'data' / 'exp_payloads_norm.npz'
_META_FILE = _BASE / 'data' / 'exp_payloads_meta.json'

_tree = None
_meta = None
_mean = None
_std = None


def _ensure() -> bool:
    """ensure"""
    global _tree, _meta, _mean, _std
    if _tree is not None:
        return True
    try:
        if not _NORM_FILE.exists() or not _META_FILE.exists():
            print(f'[WARN] {__name__}: experience_payloads数据文件不存在', file=sys.stderr)
            return False
        
        norm = np.load(_NORM_FILE)
        _mean = norm['mean']
        _std = norm['std']
        
        with open(_META_FILE) as f:
            _meta = json.load(f)
        
        # 重建向量矩阵并构建KD-Tree
        # 从meta重建太慢，直接从原始数据加载
        import gzip
        with gzip.open(_BASE / 'data' / 'experience_payloads.json.gz') as f:
            data = json.load(f)
        
        features = []
        for d in data:
            features.append([
                float(d['rsi']), float(d['bbw']), float(d['mg']),
                float(d['ml']), float(d['wl']), float(d['ws'])
            ])
        
        arr = np.array(features)
        arr_norm = (arr - _mean) / _std
        _tree = KDTree(arr_norm)
        return True
    except Exception as e:
        print(f'[WARN] {__name__}: KD-Tree初始化失败: {e}', file=sys.stderr)
        return False


def query_similar(rsi: float, bbw: float, mg: float, ml: float,
                  wl: float, ws: float, top_k: int = 10,
                  sym_filter: str = None, reg_filter: str = None) -> dict:
    """
    查询最相似的历史经验案例。
    
    参数:
      rsi, bbw, mg, ml, wl, ws — 6维特征值
      top_k — 返回数量
      sym_filter — 标的过滤（如'BTC'）
      reg_filter — 体制过滤（如'BULL_TREND'）
    
    返回:
      {n, cases: [{sym, tf, ts, reg, distance}]}
    """
    if not _ensure():
        return {'n': 0, 'cases': []}
    
    # 标准化查询向量
    q = np.array([rsi, bbw, mg, ml, wl, ws])
    q_norm = (q - _mean) / _std
    
    # 查询
    # 如果有过滤条件，多查一些然后过滤
    k = top_k * 10 if (sym_filter or reg_filter) else top_k
    k = min(k, len(_meta))
    
    dist, idx = _tree.query(q_norm, k=k)
    
    # 过滤
    results = []
    for d, i in zip(dist, idx):
        m = _meta[i]
        if sym_filter and m['sym'] != sym_filter:
            continue
        if reg_filter and m['reg'] != reg_filter:
            continue
        results.append({
            'sym': m['sym'],
            'tf': m['tf'],
            'ts': m['ts'],
            'reg': m['reg'],
            'distance': round(float(d), 4),
        })
        if len(results) >= top_k:
            break
    
    return {'n': len(results), 'cases': results}


def get_stats() -> dict:
    """返回数据统计信息"""
    if not _ensure():
        return {'active': False}
    from collections import Counter
    syms = Counter(m['sym'] for m in _meta)
    regs = Counter(m['reg'] for m in _meta)
    tfs = Counter(m['tf'] for m in _meta)
    return {
        'active': True,
        'total': len(_meta),
        'symbols': dict(syms.most_common(10)),
        'regimes': dict(regs.most_common()),
        'timeframes': dict(tfs.most_common()),
    }
