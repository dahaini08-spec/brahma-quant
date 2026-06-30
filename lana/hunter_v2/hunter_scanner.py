"""hunter_scanner.py — 品种扫描器（存根）"""
import json
from pathlib import Path

def get_active_symbols():
    """返回活跃品种列表，含 is_surge 标记"""
    BASE = Path(__file__).parent.parent.parent
    try:
        with open(BASE / 'data' / 'signal_queue.jsonl') as f:
            signals = [json.loads(l) for l in f if l.strip()]
        result = []
        for s in signals[-50:]:
            result.append({
                'symbol': s.get('symbol', ''),
                'score': s.get('score', 0),
                'is_surge': float(s.get('score', 0)) >= 145
            })
        return result
    except:
        return []
