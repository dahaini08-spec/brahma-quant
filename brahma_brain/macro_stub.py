#!/usr/bin/env python3
"""
macro_stub.py — 恐慌贪婪指数存根 v1.2
设计院重建 · 2026-07-09

优先从本地缓存读取，缓存失效时调用 Alternative.me API。
TTL = 3600s（1H刷新）
"""

import json, time, os, requests
from pathlib import Path

_CACHE_FILE = Path(__file__).parent.parent / 'data' / 'macro_cache.json'
_TTL = 3600  # 1H


def get_fear_greed() -> int:
    """
    返回恐慌贪婪指数 (0-100)。
    失败时返回 50（中性）。
    """
    # 1. 读缓存
    try:
        if _CACHE_FILE.exists():
            cached = json.loads(_CACHE_FILE.read_text())
            if time.time() - cached.get('ts', 0) < _TTL:
                return int(cached.get('fg', 50))
    except Exception:
        pass

    # 2. 调用 API
    try:
        r = requests.get(
            'https://api.alternative.me/fng/?limit=1',
            timeout=5
        )
        data = r.json()
        fg = int(data['data'][0]['value'])
        # 写缓存
        try:
            _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _CACHE_FILE.write_text(json.dumps({'fg': fg, 'ts': time.time()}))
        except Exception:
            pass
        return fg
    except Exception:
        pass

    # 3. 降级：返回中性 50
    return 50


if __name__ == '__main__':
    fg = get_fear_greed()
    label = '极度贪婪' if fg > 75 else '贪婪' if fg > 55 else '中性' if fg > 45 else '恐慌' if fg > 25 else '极度恐慌'
    print(f'Fear & Greed: {fg} ({label})')
