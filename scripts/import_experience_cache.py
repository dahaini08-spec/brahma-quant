#!/usr/bin/env python3
"""
import_experience_cache.py — 把本地缓存批量导入Qdrant
2026-08-27 苏摩111批准

运行：python3 scripts/import_experience_cache.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, gzip, time
from pathlib import Path
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams, PointStruct

ROOT       = Path(__file__).parent.parent
CACHE      = ROOT / 'data' / 'experience_cache'
COLLECTION = 'brahma_experiences'
VECTOR_DIM = 16
BATCH_SIZE = 500

def get_client():
    import os
    qdrant_path = str(ROOT / 'data' / 'qdrant_storage')
    os.makedirs(qdrant_path, exist_ok=True)
    client = QdrantClient(path=qdrant_path)  # 本地持久化，无需Docker
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION not in existing:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE)
        )
        print(f'✅ 创建collection: {COLLECTION}')
    return client

def load_jsonl_gz(path: Path) -> list:
    items = []
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    items.append(json.loads(line))
    except Exception as e:
        print(f'  ⚠️ 读取失败 {path.name}: {e}')
    return items

def rebuild_vector(exp: dict) -> list:
    """从payload重建16维向量"""
    sys.path.insert(0, str(ROOT / 'brahma_brain'))
    from brahma_experience_engine import build_experience_vector, REGIME_MAP
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

def import_all():
    cache_files = sorted(CACHE.glob('*.jsonl.gz'))
    print(f'📦 发现 {len(cache_files)} 个缓存文件')

    if not cache_files:
        print('⚠️ 无缓存文件，请先运行 brahma_experience_engine.py')
        return

    try:
        client = get_client()
    except Exception as e:
        print(f'❌ Qdrant连接失败: {e}')
        print('  请确认Qdrant服务已启动: docker run -p 6333:6333 qdrant/qdrant')
        return

    total_inserted = 0
    for cache_file in cache_files:
        items = load_jsonl_gz(cache_file)
        if not items:
            continue

        points = []
        for exp in items:
            try:
                vec = rebuild_vector(exp)
                uid = abs(hash(f"{exp.get('symbol')}_{exp.get('timeframe')}_{exp.get('ts')}")) % (2**63)
                points.append(PointStruct(id=uid, vector=vec, payload=exp))
            except:
                continue

        inserted = 0
        for i in range(0, len(points), BATCH_SIZE):
            batch = points[i:i+BATCH_SIZE]
            try:
                client.upsert(collection_name=COLLECTION, points=batch)
                inserted += len(batch)
            except Exception as e:
                print(f'  ⚠️ upsert失败: {e}')

        total_inserted += inserted
        sym = items[0].get('symbol','?') if items else '?'
        tf  = items[0].get('timeframe','?') if items else '?'
        print(f'  ✅ {sym} {tf}: {inserted}条写入Qdrant')

    # 验证
    try:
        info = client.get_collection(COLLECTION)
        total_in_qdrant = info.points_count
        print(f'\n✅ 导入完成！Qdrant总计: {total_in_qdrant:,}条经验片段')
    except:
        print(f'\n✅ 导入完成！总写入: {total_inserted:,}条')

    return total_inserted

if __name__ == '__main__':
    import_all()
