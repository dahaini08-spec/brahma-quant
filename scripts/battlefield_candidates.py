#!/usr/bin/env python3
"""
battlefield_candidates.py — 候选池生成器
Phase B: Layer 2→3 自动分析触发
每小时从battlefield_intel筛选候选池，保存到data/battlefield_candidates.json
供分析引擎读取，实现865标的→10-30候选池的95%算力节省
"""
import sys, json, time
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / 'brahma_brain'))
from battlefield_intel import get_candidates, merge_all

OUT_PATH = BASE / 'data' / 'battlefield_candidates.json'

def generate_and_save():
    """生成候选池并保存"""
    # 先merge最新数据
    merge_all()
    
    # 三档候选池
    pool = {
        '_meta': {
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'ts': time.time(),
        },
        'tier1_strong': [],   # ≥5维共振
        'tier2_medium': [],   # ≥4维共振
        'tier3_watch': [],    # ≥3维共振
    }
    
    for tier, min_dim, key in [
        ('strong', 5, 'tier1_strong'),
        ('medium', 4, 'tier2_medium'),
        ('watch', 3, 'tier3_watch'),
    ]:
        cands = get_candidates(min_dimensions=min_dim)
        # 转为可序列化格式
        for c in cands:
            pool[key].append({
                'symbol': c['symbol'],
                'score': c['score'],
                'dims': c['dim_count'],
                'signals': c['signals'],
                'oi_score': c.get('oi_score', 0),
                'cvd_1h': c.get('cvd_1h'),
                'whale_dir': c.get('whale_direction', ''),
                'rsi_signal': c.get('rsi_signal', ''),
                'breakout': c.get('breakout_signal', ''),
            })
    
    # 原子写入
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(pool, ensure_ascii=False, default=str))
    tmp.rename(OUT_PATH)
    
    return pool

if __name__ == '__main__':
    pool = generate_and_save()
    print(f"候选池生成完成:")
    print(f"  Tier1 强共振(≥5维): {len(pool['tier1_strong'])}个")
    print(f"  Tier2 中共振(≥4维): {len(pool['tier2_medium'])}个")
    print(f"  Tier3 观察(≥3维): {len(pool['tier3_watch'])}个")
    print(f"  保存: {OUT_PATH}")
    
    if pool['tier1_strong']:
        print(f"\n=== Tier1 强共振 ===")
        for c in pool['tier1_strong'][:10]:
            print(f"  {c['symbol']:15s} score={c['score']} dims={c['dims']} {c['signals']}")
