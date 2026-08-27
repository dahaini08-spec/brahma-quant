#!/usr/bin/env python3
"""
build_experience_index.py — 梵天经验向量索引构建器
2026-08-27 苏摩111批准

把96个jsonl.gz缓存 → 统一 numpy 索引文件
用于 query_similar_experiences() 快速检索
无需Qdrant服务，本地文件即可运行

输出：
  data/experience_index.npz   — 向量矩阵 + payload索引
  data/experience_meta.json   — 统计摘要
"""
import sys, os, gzip, json, time
import numpy as np
from pathlib import Path

ROOT  = Path(__file__).parent.parent
CACHE = ROOT / 'data' / 'experience_cache'
OUT_NPZ  = ROOT / 'data' / 'experience_index.npz'
OUT_META = ROOT / 'data' / 'experience_meta.json'

sys.path.insert(0, str(ROOT / 'brahma_brain'))
from brahma_experience_engine import build_experience_vector, REGIME_MAP

VECTOR_DIM = 16

def rebuild_vector(exp: dict) -> list:
    return build_experience_vector(
        rsi_1h=exp.get('rsi', 50),
        rsi_4h=exp.get('rsi', 50),
        bbw=exp.get('bbw', 5),
        atr_pct=exp.get('atr_pct', 2),
        regime=exp.get('regime', 'UNKNOWN'),
        ret_7d=exp.get('ret_7d', 0),
        ret_30d=exp.get('ret_7d', 0) * 2,
        volume_ratio=1.0,
        price_vs_ema50=0.0,
    )

def build():
    files = sorted(CACHE.glob('*.jsonl.gz'))
    print(f'📦 发现 {len(files)} 个缓存文件')

    vectors   = []
    payloads  = []
    t0 = time.time()

    for i, f in enumerate(files):
        try:
            with gzip.open(f, 'rt', encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if not line: continue
                    exp = json.loads(line)
                    vec = rebuild_vector(exp)
                    vectors.append(vec)
                    # payload只保留关键字段（节省内存）
                    payloads.append({
                        'sym': exp.get('symbol',''),
                        'tf':  exp.get('timeframe',''),
                        'ts':  exp.get('ts', 0),
                        'reg': exp.get('regime',''),
                        'rsi': exp.get('rsi', 50),
                        'bbw': exp.get('bbw', 5),
                        'mg':  exp.get('max_gain', 0),   # max_gain
                        'ml':  exp.get('max_loss', 0),   # max_loss
                        'wl':  1 if exp.get('win_long') else 0,
                        'ws':  1 if exp.get('win_short') else 0,
                    })
        except Exception as e:
            print(f'  ⚠️ {f.name}: {e}')

        if (i+1) % 20 == 0:
            print(f'  进度: {i+1}/{len(files)} 文件, {len(vectors):,}条')

    if not vectors:
        print('❌ 无数据')
        return

    vec_matrix = np.array(vectors, dtype=np.float32)  # shape: (N, 16)
    # L2归一化（cosine相似度用点积）
    norms = np.linalg.norm(vec_matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    vec_norm = vec_matrix / norms

    print(f'\n✅ 向量矩阵: {vec_norm.shape}')
    print(f'   耗时: {time.time()-t0:.1f}s')

    # 保存
    np.savez_compressed(OUT_NPZ, vectors=vec_norm)
    print(f'   保存: {OUT_NPZ} ({OUT_NPZ.stat().st_size/1024/1024:.1f}MB)')

    # payload单独保存（json太大，用紧凑格式）
    payload_path = ROOT / 'data' / 'experience_payloads.json.gz'
    with gzip.open(payload_path, 'wt', encoding='utf-8') as f:
        json.dump(payloads, f, separators=(',',':'), ensure_ascii=False)
    print(f'   payload: {payload_path} ({payload_path.stat().st_size/1024/1024:.1f}MB)')

    # 统计摘要
    from collections import Counter
    regimes = Counter(p['reg'] for p in payloads)
    symbols = Counter(p['sym'] for p in payloads)
    tfs     = Counter(p['tf']  for p in payloads)
    wr_long  = sum(p['wl'] for p in payloads) / len(payloads)
    wr_short = sum(p['ws'] for p in payloads) / len(payloads)

    meta = {
        'total':     len(payloads),
        'built_at':  time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'vector_dim': VECTOR_DIM,
        'by_regime': dict(regimes.most_common()),
        'by_symbol': dict(symbols.most_common(10)),
        'by_tf':     dict(tfs.most_common()),
        'wr_long_avg':  round(wr_long, 3),
        'wr_short_avg': round(wr_short, 3),
    }
    OUT_META.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    print(f'\n📊 统计:')
    print(f'   总经验片段: {len(payloads):,}条')
    print(f'   体制分布: {dict(list(regimes.most_common()))}')
    print(f'   均值WR_多: {wr_long:.1%} | WR_空: {wr_short:.1%}')
    print(f'   周期: {dict(tfs)}')

if __name__ == '__main__':
    build()
