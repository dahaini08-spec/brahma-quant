#!/usr/bin/env python3
"""
analysis_snapshot.py — P4 分析快照持久化（TradingAgents LangGraph Checkpoint启发）
brahma_analyze每次分析结果写入快照，Gateway重启后可复用，无需重算

用法：
  from analysis_snapshot import save_snapshot, load_snapshot, is_fresh
  save_snapshot(symbol, direction, result)
  r = load_snapshot(symbol, direction, max_age_min=15)
"""
import json, time
from pathlib import Path
from datetime import datetime, timezone

SNAPSHOT_DIR = Path(__file__).parent.parent / 'data' / 'analysis_snapshots'
MAX_AGE_DEFAULT = 15  # 分钟

def snapshot_path(symbol: str, direction: str) -> Path:
    sym = symbol.upper().replace('USDT', '')
    dir_tag = direction.upper().replace(' ', '_')
    return SNAPSHOT_DIR / f'{sym}_{dir_tag}.json'

def save_snapshot(symbol: str, direction: str, result: dict) -> Path:
    """保存分析快照"""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = snapshot_path(symbol, direction)
    snapshot = {
        'symbol': symbol,
        'direction': direction,
        'ts': int(time.time()),
        'dt': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        'result': result,
    }
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return path

def load_snapshot(symbol: str, direction: str, max_age_min: int = MAX_AGE_DEFAULT) -> dict:
    """
    加载快照，若过期返回None
    max_age_min: 最大缓存分钟数
    """
    path = snapshot_path(symbol, direction)
    if not path.exists():
        return None
    try:
        snap = json.loads(path.read_text())
        age_min = (time.time() - snap.get('ts', 0)) / 60
        if age_min > max_age_min:
            return None
        snap['_cache_age_min'] = round(age_min, 1)
        return snap
    except Exception:
        return None

def is_fresh(symbol: str, direction: str, max_age_min: int = MAX_AGE_DEFAULT) -> bool:
    return load_snapshot(symbol, direction, max_age_min) is not None

def list_snapshots() -> list:
    """列出所有快照"""
    if not SNAPSHOT_DIR.exists():
        return []
    results = []
    for f in sorted(SNAPSHOT_DIR.glob('*.json')):
        try:
            snap = json.loads(f.read_text())
            age_min = round((time.time() - snap.get('ts', 0)) / 60, 1)
            score = snap.get('result', {}).get('confluence', {}).get('total', 0)
            results.append({
                'file': f.name,
                'symbol': snap.get('symbol'),
                'direction': snap.get('direction'),
                'dt': snap.get('dt'),
                'age_min': age_min,
                'score': score,
                'fresh': age_min <= MAX_AGE_DEFAULT,
            })
        except Exception:
            pass
    return results

def clear_stale(max_age_min: int = 60):
    """清理超过max_age_min分钟的快照"""
    if not SNAPSHOT_DIR.exists():
        return 0
    cleared = 0
    for f in SNAPSHOT_DIR.glob('*.json'):
        try:
            snap = json.loads(f.read_text())
            age_min = (time.time() - snap.get('ts', 0)) / 60
            if age_min > max_age_min:
                f.unlink()
                cleared += 1
        except Exception:
            pass
    return cleared

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'list':
        snaps = list_snapshots()
        if not snaps:
            print('无快照')
        for s in snaps:
            tag = '✅' if s['fresh'] else '🕐'
            print(f"{tag} {s['symbol']} {s['direction']} score={s['score']} age={s['age_min']}min [{s['dt']}]")
    elif len(sys.argv) > 1 and sys.argv[1] == 'clear':
        n = clear_stale()
        print(f'清理 {n} 条过期快照')
    else:
        print('用法: python3 analysis_snapshot.py list|clear')
