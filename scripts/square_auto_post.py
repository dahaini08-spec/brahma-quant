#!/usr/bin/env python3
"""
square_auto_post.py — VIP策略→Binance Square全自动发帖 v2.0
[设计院封印 2026-09-11 苏摩111]

v2.0变更：
  - 废弃LLM重写（free_llm_client 45秒超时+风格漂移）
  - 改用square_template.py纯模板引擎（0秒+0漂移）
  - 旗舰帖接入brahma_manual_analysis 80维输出
  - 所有帖必须有🌿姓赵不宣前缀+📊梵天系统后缀

接入位置：
  - cron: supercronic brahma_crontab.txt
  - 调用：python3 scripts/square_auto_post.py --sym BTC ETH
"""

import json, os, ssl, sys, time, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / "scripts"))
sys.path.insert(0, str(BASE / "scripts" / "square"))

CST = timezone(timedelta(hours=8))

# Square API
SQUARE_KEY = os.environ.get('SQUARE_KEY_0', 'd9f19e3f6ba3480584db27b09bec0f27')
SQUARE_URL = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

# 去重
DEDUP_FILE = BASE / 'data' / 'square_post_dedup.json'
LOG_FILE = BASE / 'data' / 'square_post_log.jsonl'


def _post_to_square(content: str) -> dict:
    """POST到Binance Square"""
    payload = json.dumps({'bodyTextOnly': content}).encode()
    req = urllib.request.Request(
        SQUARE_URL, data=payload,
        headers={
            'X-Square-OpenAPI-Key': SQUARE_KEY,
            'Content-Type': 'application/json',
            'clienttype': 'binanceSkill',
        },
    )
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=15, context=_ctx).read())
        return resp
    except Exception as e:
        return {'error': str(e)}


def _is_duplicate(content: str) -> bool:
    import hashlib
    h = hashlib.md5(content.encode()).hexdigest()[:12]
    if DEDUP_FILE.exists():
        try:
            d = json.loads(DEDUP_FILE.read_text())
            now = time.time()
            d = {k: v for k, v in d.items() if now - v < 86400}
            if h in d:
                return True
        except:
            pass
    return False


def _mark_posted(content: str):
    import hashlib
    h = hashlib.md5(content.encode()).hexdigest()[:12]
    d = {}
    if DEDUP_FILE.exists():
        try:
            d = json.loads(DEDUP_FILE.read_text())
        except:
            pass
    now = time.time()
    d = {k: v for k, v in d.items() if now - v < 86400}
    d[h] = now
    DEDUP_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2))


def _log_post(post_type: str, content: str, resp: dict):
    entry = {
        'ts': time.time(),
        'post_type': post_type,
        'post_id': resp.get('data', {}).get('id', 0) if isinstance(resp.get('data'), dict) else 0,
        'chars': len(content),
        'preview': content[:200],
    }
    with open(LOG_FILE, 'a') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def run(syms: list, dry_run: bool = False) -> None:
    """跑分析→模板填充→发帖"""
    from square.square_template import build_battlefield_report, parse_analysis_output, audit_post
    from brahma_manual_analysis import run_analysis

    for sym in syms:
        print(f'[{sym}] 生成分析报告...', flush=True)
        try:
            report = run_analysis(sym, push_jarvis=False)
        except Exception as e:
            print(f'[{sym}] 分析失败: {e}')
            continue

        # 解析分析报告数据
        data = parse_analysis_output(report)
        data['price'] = data.get('price', 0)

        # 如果VIP=WAIT，构建战场报告（不带入场条件）
        # 如果VIP=ENTER，构建带入场条件的战场报告
        content = build_battlefield_report(sym, data)

        # 审计
        ok, issues = audit_post(content)
        if not ok:
            print(f'[{sym}] 审计失败: {issues}')
            continue

        # 去重
        if _is_duplicate(content):
            print(f'[{sym}] 24h内重复，跳过')
            continue

        print(f'[{sym}] 准备发帖 ({len(content)}字):')
        print(content[:200] + '...')

        if dry_run:
            print(f'[{sym}] DRY-RUN，跳过发帖')
            continue

        # 发帖
        resp = _post_to_square(content)
        if 'error' in resp:
            print(f'[{sym}] 发帖失败: {resp["error"]}')
        else:
            print(f'[{sym}] ✅ 发布成功')
            _mark_posted(content)
            _log_post('battlefield', content, resp)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--sym', nargs='+', default=['BTC', 'ETH'])
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    run(args.sym, dry_run=args.dry_run)
