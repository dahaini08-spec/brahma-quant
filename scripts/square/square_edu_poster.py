#!/usr/bin/env python3
"""
square_edu_poster.py — 教育帖自动发帖 v1.0
[设计院封印 2026-09-11 苏摩111]

每周3帖（周一/三/五 UTC03:00）
35条库存 + 当日实盘案例联动
每帖必有🌿姓赵不宣前缀+📊后缀
"""
# [2026-10-05 P1 苏摩111] 统一路径管理，替代裸 sys.path.insert
try:
    import brahma_path_setup  # noqa
except ImportError:
    pass  # 兜底：原有 sys.path.insert 仍保留

import json, os, sys, time, hashlib, ssl, urllib.request
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys

BASE = Path(__file__).parent.parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'scripts' / 'square'))

CST = timezone(timedelta(hours=8))

from square_key_router import get_square_key as _get_sq_key
SQUARE_KEY = _get_sq_key('edu_poster')
API_URL = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'
POOL_FILE = BASE / 'data' / 'square_content_pool.json'
DEDUP_FILE = BASE / 'data' / 'square_post_dedup.json'
LOG_FILE = BASE / 'data' / 'square_post_log.jsonl'
STATE_FILE = BASE / 'data' / 'square_edu_state.json'
_ctx = ssl.create_default_context()
_ctx.check_hostname = True
_ctx.verify_mode = ssl.CERT_REQUIRED


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    return {'last_edu_id': 0, 'posted_ids': []}


def save_state(state):
    # [9.28瘟疫清扫 苏摩111] 原子写: tmp+os.replace 防空读竞态（9.26路线A同款）
    _tmp = STATE_FILE.with_suffix(".tmp")
    _tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(_tmp), str(STATE_FILE))
def load_pool():
    if POOL_FILE.exists():
        return json.loads(POOL_FILE.read_text())
    return {'education': []}


def is_duplicate(content):
    h = hashlib.md5(content.encode()).hexdigest()[:12]
    d = {}
    if DEDUP_FILE.exists():
        try:
            d = json.loads(DEDUP_FILE.read_text())
        except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    now = time.time()
    d = {k: v for k, v in d.items() if now - v < 86400}
    return h in d


def mark_posted(content):
    h = hashlib.md5(content.encode()).hexdigest()[:12]
    d = {}
    if DEDUP_FILE.exists():
        try:
            d = json.loads(DEDUP_FILE.read_text())
        except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    now = time.time()
    d = {k: v for k, v in d.items() if now - v < 86400}
    d[h] = now
    # [9.28瘟疫清扫 苏摩111] 原子写: tmp+os.replace 防空读竞态（9.26路线A同款）
    _tmp = DEDUP_FILE.with_suffix(".tmp")
    _tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(_tmp), str(DEDUP_FILE))
def post_to_square(content):
    payload = json.dumps({'bodyTextOnly': content}).encode()
    req = urllib.request.Request(API_URL, data=payload, headers={
        'X-Square-OpenAPI-Key': SQUARE_KEY,
        'Content-Type': 'application/json',
        'clienttype': 'binanceSkill',
    })
    try:
        return json.loads(urllib.request.urlopen(req, timeout=15, context=_ctx).read())
    except Exception as e:
        return {'error': str(e)}


def log_post(content, resp):
    entry = {
        'ts': time.time(), 'post_type': 'education',
        'post_id': resp.get('data', {}).get('id', 0) if isinstance(resp.get('data'), dict) else 0,
        'chars': len(content), 'preview': content[:200],
    }
    with open(LOG_FILE, 'a') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def fetch_live_fvg(symbol='BTCUSDT'):
    """拉当日实盘FVG数据用于实盘联动"""
    try:
        sys.path.insert(0, str(BASE))
        from brahma_core import analyze
        result = analyze(symbol, '4h')
        if result and result.get('fvg'):
            fvg = result['fvg']
            return {
                'dir': fvg.get('dir', ''),
                'lo': fvg.get('lo', 0),
                'hi': fvg.get('hi', 0),
                'mid': fvg.get('mid', 0) or fvg.get('magnet', 0),
            }
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    return None


def run(dry_run=False, edu_id=None):
    from square_template import build_education_post, audit_post

    state = load_state()
    pool = load_pool()
    edu_list = pool.get('education', [])

    if not edu_list:
        print('无教育帖库存')
        return

    # 选择要发的教育帖
    next_id = edu_id
    if next_id is None:
        # 自动选下一个未发布的
        for i, edu in enumerate(edu_list):
            if i not in state.get('posted_ids', []):
                next_id = i
                break
        if next_id is None:
            print('所有教育帖已发完')
            return

    edu_text = edu_list[next_id]
    if isinstance(edu_text, dict):
        body = edu_text.get('body', edu_text.get('content', ''))
    else:
        body = edu_text

    # 从body提取概念和定义
    lines = [l for l in body.split('\n') if l.strip()]
    concept = ''
    definition = []
    # concept从第一行提取（去掉括号说明，只保留术语名）
    if lines:
        first = lines[0]
        # 提取「术语（...）：」格式中的术语名
        import re as _re
        m = _re.match(r'^([A-Za-z一-鿿·]+)[（(（]', first)
        if m:
            concept = m.group(1).strip()
        elif '：' in first or ':' in first:
            concept = first.split('：')[0].split(':')[0].strip()
        elif '=' in first:
            concept = first.split('=')[0].strip()
        else:
            concept = first[:20].strip()
    # definition只取前3行有效内容
    for line in lines[:6]:
        if line.strip() and not line.startswith('#'):
            definition.append(line)

    if not concept or len(concept) > 30:
        concept = '交易方法'

    # 实盘联动：拉当前BTC/ETH的FVG
    live_fvg = fetch_live_fvg('BTCUSDT')
    live_example = '今日BTC 4H FVG正在走填补'
    if live_fvg and live_fvg['mid']:
        live_example = (f"BTC 4H {live_fvg['dir']} FVG ${live_fvg['lo']:,.0f}-${live_fvg['hi']:,.0f}\n"
                        f"当前磁铁${live_fvg['mid']:,.0f}\n"
                        f"{'回调支撑区' if live_fvg['dir']=='BULL' else '反弹阻力区'}")

    how_to_use = '结合FVG中点+1H收线确认方向再入场\n止损放FVG失效位之外'

    historical = '实盘案例见上述数据'

    content = build_education_post(
        edu_id=state.get('last_edu_id', 0) + 1,
        concept=concept,
        definition_lines=definition[:3],
        live_example=live_example,
        how_to_use=how_to_use,
        historical_case=historical,
    )

    # 审计
    ok, issues = audit_post(content)
    if not ok:
        print(f'审计失败: {issues}')
        print(content)
        return

    if is_duplicate(content):
        print('24h内重复，跳过')
        return

    print(f'准备发教育帖 ({len(content)}字):')
    print(content[:200] + '...')

    if dry_run:
        print('DRY-RUN')
        return

    resp = post_to_square(content)
    if 'error' in resp:
        print(f'发帖失败: {resp["error"]}')
    else:
        print('✅ 发布成功')
        mark_posted(content)
        log_post(content, resp)
        state['posted_ids'].append(next_id)
        state['last_edu_id'] = state.get('last_edu_id', 0) + 1
        save_state(state)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--edu-id', type=int, default=None)
    args = parser.parse_args()
    run(dry_run=args.dry_run, edu_id=args.edu_id)
