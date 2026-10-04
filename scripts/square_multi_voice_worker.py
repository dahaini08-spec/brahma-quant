#!/usr/bin/env python3
"""
square_multi_voice_worker.py — multi_voice后台独立进程 [2026-10-04 苏摩111]
由 square_auto_post.py 用 setsid 启动，脱离cron超时限制
读取临时文件中的原始内容，改写后分发KEY_1/KEY_2
"""
import json, sys, ssl, time, os
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'scripts' / 'square'))

def main():
    if len(sys.argv) < 2:
        print('[worker] 无临时文件参数，退出')
        return

    tmp_file = sys.argv[1]
    try:
        with open(tmp_file, encoding='utf-8') as f:
            data = json.load(f)
        original_content = data['content']
        os.unlink(tmp_file)  # 用完即删
    except Exception as e:
        print(f'[worker] 读取临时文件失败: {e}')
        return

    try:
        from square.multi_voice_rewriter import generate_three_versions
        from square.square_key_router import get_square_key as _gsk
    except Exception as e:
        print(f'[worker] 导入失败: {e}')
        return

    ctx = ssl.create_default_context()
    import urllib.request

    _keys = {1: _gsk('hot_poster'), 2: _gsk('extreme_alert')}
    _names = {1: '蓝桉VS释怀鸟', 2: '牛来PRO'}
    SQUARE_URL = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'
    LOG = BASE / 'data' / 'square_post_log.jsonl'

    def _post(content, key):
        payload = json.dumps({'bodyTextOnly': content}).encode()
        req = urllib.request.Request(SQUARE_URL, data=payload,
            headers={'X-Square-OpenAPI-Key': key,
                     'Content-Type': 'application/json',
                     'clienttype': 'binanceSkill'})
        try:
            return json.loads(urllib.request.urlopen(req, timeout=15, context=ctx).read())
        except Exception as e:
            return {'error': str(e)}

    versions = generate_three_versions(original_content)

    for idx in [1, 2]:
        v = versions.get(idx)
        if not v:
            continue
        name = _names[idx]
        delay = 300 if idx == 1 else 600  # KEY_1等5min，KEY_2等10min
        print(f'[worker] 等待{delay}s → {name}...', flush=True)
        time.sleep(delay)

        key = _keys[idx]
        resp = _post(v, key)
        if resp.get('success') or resp.get('code') == '000000':
            pid = resp.get('data', {}).get('id', '')
            link = resp.get('data', {}).get('shareLink', '')
            print(f'[worker] {name} ✅ id={pid} {link}', flush=True)
            with open(LOG, 'a') as f:
                f.write(json.dumps({
                    'ts': time.time(),
                    'post_type': f'multi_voice_key{idx}',
                    'id': pid,
                    'chars': len(v),
                    'preview': v[:200],
                }, ensure_ascii=False) + '\n')
        else:
            print(f'[worker] {name} ❌ {resp}', flush=True)

if __name__ == '__main__':
    main()
