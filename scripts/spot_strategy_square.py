#!/usr/bin/env python3
"""
spot_strategy_square.py — 拳头二：现货策略→Square发帖+Jarvis推送
设计院三方封印 2026-09-10 苏摩111
"""
import sys, os, json, ssl, urllib.request, time, subprocess, re
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'scripts'))

SQUARE_KEY = 'd9f19e3f6ba3480584db27b09bec0f27'
SQUARE_URL = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'
JARVIS_TO = '73295708:thread:01a07628-0405-7e85-a34b-e68cd029dfc6'
OPENROUTER_KEY = os.environ.get('OPENROUTER_API_KEY', '')
# 从free_llm_client读取API key
try:
    from free_llm_client import API_KEY
    OPENROUTER_KEY = API_KEY
except Exception:
    pass

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

BLOCKED_WORDS = [
    'BEAR_TREND', 'CHOP_MID', 'BULL_TREND', 'BEAR_EARLY',
    '梵天', 'FVG', 'Kronos', '体制识别',
    'brahma', 'brahma_', '量化系统', '量化引擎',
]


def run_spot(symbol: str) -> dict:
    subprocess.run(
        ['python3', str(BASE / 'scripts' / 'spot_strategy_runner.py'),
         '--symbol', symbol, '--quiet'],
        capture_output=True, text=True, timeout=30, cwd=str(BASE)
    )
    cache = BASE / 'data' / f'spot_strategy_{symbol}USDT.json'
    if cache.exists():
        return json.loads(cache.read_text())
    return {}


def rewrite_for_square(draft: str) -> str:
    """直接调OpenRouter，不注入梵天宪法
    [2026-09-11 苏摩111] LLM重写已废弃，直接返回原稿"""
    return draft
    # ── 以下LLM重写已废弃 ──
    if not OPENROUTER_KEY:
        return draft
    try:
        prompt = (
            f'用40年顶级交易员视角重写以下现货策略帖。'
            f'保留所有数字，禁止Markdown格式，禁止AI腔，'
            f'直接输出重写结果，不要解释：\n\n{draft}'
        )
        payload = json.dumps({
            'model': 'meta-llama/llama-3.3-70b-instruct:free',
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': 400,
            'temperature': 0.2,
        }).encode()
        req = urllib.request.Request(
            'https://openrouter.ai/api/v1/chat/completions', data=payload,
            headers={
                'Authorization': f'Bearer {OPENROUTER_KEY}',
                'Content-Type': 'application/json',
            },
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=20, context=_ctx).read())
        result = resp.get('choices', [{}])[0].get('message', {}).get('content', '')
        if not result or len(result) < 80:
            return draft
        for w in BLOCKED_WORDS:
            result = result.replace(w, '')
        result = re.sub(r'\*\*(.+?)\*\*', r'【\1】', result)
        result = re.sub(r'\*(.+?)\*', r'\1', result)
        result = re.sub(r'^#{1,6}\s+', '', result, flags=re.MULTILINE)
        return result.strip()
    except Exception:
        return draft


def post_to_square(content: str) -> dict:
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
        return json.loads(urllib.request.urlopen(req, timeout=15, context=_ctx).read())
    except Exception as e:
        return {'error': str(e)}


def push_jarvis(message: str):
    try:
        subprocess.run(
            ['openclaw', 'message', 'send',
             '--channel', 'jarvis',
             '--to', JARVIS_TO,
             '--message', message],
            capture_output=True, text=True, timeout=10
        )
    except Exception:
        pass


def run(symbols: list, dry_run: bool = False):
    for sym in symbols:
        print(f'[{sym}] 运行现货策略...', flush=True)
        rec = run_spot(sym)
        if not rec:
            print(f'[{sym}] 策略生成失败，跳过')
            continue

        card = rec.get('card', '')
        if not card or len(card) < 50:
            print(f'[{sym}] 策略内容为空，跳过')
            continue

        print(f'[{sym}] LLM重写...', flush=True)
        content = rewrite_for_square(card)

        if dry_run:
            print(f'[DRY-RUN] {sym} 内容预览:')
            print('-' * 50)
            print(content[:400])
            print('-' * 50)
            continue

        print(f'[{sym}] 发送Square...', flush=True)
        result = post_to_square(content)
        if result.get('code') == '000000':
            post_id = result.get('data', {}).get('id', '')
            print(f'[{sym}] ✅ Square发布成功 id={post_id}')
            push_jarvis(f'📢 拳头二现货策略发帖成功\n\n{sym} id={post_id}')
        else:
            print(f'[{sym}] ❌ Square发布失败: {result}')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', nargs='+', default=['BTC', 'ETH'])
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    run(args.symbols, dry_run=args.dry_run)
