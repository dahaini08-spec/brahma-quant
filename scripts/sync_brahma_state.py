#!/usr/bin/env python3
"""
brahma_state.json SSOT同步脚本
从regime_switch_state.json（实时更新）同步到brahma_state.json
由btc-regime-watcher每5分钟exec调用，防止SSOT滞后
"""
import json
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent.parent
RSW  = BASE / 'data' / 'regime_switch_state.json'
BST  = BASE / 'data' / 'brahma_state.json'

def sync():
    with open(RSW) as f:
        rs = json.load(f)
    
    votes = {}
    for sym, v in rs.items():
        r = v.get('regime', '?')
        votes[r] = votes.get(r, 0) + 1
    
    dominant = max(votes, key=votes.get)
    
    with open(BST) as f:
        bs = json.load(f)
    
    old = bs.get('regime')
    if old != dominant:
        bs['regime'] = dominant
        bs['regime_votes'] = votes
        bs['updated_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S+00:00')
        bs['regime_source'] = 'sync_from_regime_switch_state'
        with open(BST, 'w') as f:
            json.dump(bs, f, indent=2, ensure_ascii=False)
        print(f'[SSOT-SYNC] {old} → {dominant} | votes={votes}')
    else:
        print(f'[SSOT-SYNC] 体制未变 {dominant} (HEARTBEAT_OK)')

if __name__ == '__main__':
    sync()
