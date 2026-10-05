#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
flush_flagship_queue.py — 旗舰帖批准队列发布器 [2026-09-28 苏摩111]
================================================================
批准链路的最后一环：
  1. square_deep_post.py 产出→入队 data/flagship_pending/*.json (PENDING)
  2. 推送批准卡片到jarvis → 苏摩回「111」→ 本脚本被调用（openclaw cron add --at）
  3. 本脚本取最新PENDING项→发Square→标PUBLISHED

安全设计：
  - 每次只发1帖（最新PENDING项），防连环自动发
  - 发完自动标记，重复调用幂等
  - 24h去重沿用square_auto_post SSOT

接入位置：
  - 由主session在收到苏摩「111」批准后执行：
    python3 scripts/flush_flagship_queue.py
  - 队列文件：data/flagship_pending/flagship_<ts>.json
"""
# [2026-10-05 P1 苏摩111] 统一路径管理，替代裸 sys.path.insert
try:
    import brahma_path_setup  # noqa
except ImportError:
    pass  # 兜底：原有 sys.path.insert 仍保留

import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'scripts' / 'square'))

QUEUE_DIR = BASE / 'data' / 'flagship_pending'


def flush(dry_run: bool = False) -> int:
    """发布最新一个PENDING项。返回发布数（0=无可发布项）"""
    from square_auto_post import _post_to_square, _is_duplicate, _mark_posted, _log_post

    if not QUEUE_DIR.exists():
        print('[flush] 队列目录不存在')
        return 0

    pending = sorted(QUEUE_DIR.glob('flagship_*.json'))
    target = None
    for f in reversed(pending):  # 最新的优先
        try:
            item = json.loads(f.read_text())
        except Exception as e:
            print(f'[flush] 解析失败 {f.name}: {e}')
            continue
        if item.get('status') == 'PENDING':
            target = (f, item)
            break

    if not target:
        print('[flush] 无PENDING项（可能已全部发布或作废）')
        return 0

    f, item = target
    content = item.get('content', '')
    if not content or len(content) < 600:
        print(f'[flush] {f.name} 正文异常（{len(content)}字），跳过')
        item['status'] = 'INVALID'
        f.write_text(json.dumps(item, ensure_ascii=False, indent=1))
        return 0

    if _is_duplicate(content):
        print('[flush] 24h内重复，标记SKIP并退出')
        item['status'] = 'SKIP_DUP'
        f.write_text(json.dumps(item, ensure_ascii=False, indent=1))
        return 0

    print(f'[flush] 发布 {f.name} ({len(content)}字):')
    print(content[:200] + '...')
    if dry_run:
        print('[flush] DRY-RUN，未发布')
        return 0

    resp = _post_to_square(content)
    if 'error' in resp:
        print(f'[flush] 发帖失败: {resp["error"]}（保持PENDING，可重试）')
        return 0

    item['status'] = 'PUBLISHED'
    item['published_ts'] = time.time()
    item['post_id'] = resp.get('data', {}).get('id', 0) if isinstance(resp.get('data'), dict) else 0
    f.write_text(json.dumps(item, ensure_ascii=False, indent=1))
    _mark_posted(content)
    _log_post('deep_post', content, resp)
    print(f'[flush] ✅ 发布成功 post_id={item["post_id"]}')
    return 1


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    flush(dry_run=args.dry_run)
