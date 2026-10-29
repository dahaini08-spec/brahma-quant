#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
square_multi_voice_worker.py — 三账号差异化发帖后台工作进程
[2026-10-29 苏摩111] 新建修复：文件缺失导致本周multi_voice 0条

用法：python3 square_multi_voice_worker.py <tmp_json_file>
tmp_json_file: {"content": "<原始battlefield帖内容>"}
"""
import sys, json, time, ssl, urllib.request
from pathlib import Path

# 路径设置
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / 'scripts'))
sys.path.insert(0, str(_ROOT / 'scripts' / 'square'))
sys.path.insert(0, str(_ROOT / 'brahma_brain'))

# Square API配置
_KEYS = {
    1: 'c43ebad6a8434d1b91a039dbf43fda29',   # 蓝桉VS释怀鸟
    2: '278f3e81efda4274a1d8e15dbc32ec88',   # 牛来PRO
}
_REFS = {1: 'LZH6E5FO', 2: 'PEZOWSEH'}
_URL  = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'
_LOG  = str(_ROOT / 'logs' / 'multi_voice.log')

_DELAYS = {1: 300, 2: 600}   # 蓝桉等5min，牛来等10min

def _post(content: str, key_idx: int) -> dict:
    api_key = _KEYS[key_idx]
    ctx = ssl.create_default_context()
    body = json.dumps({'bodyTextOnly': content}).encode('utf-8')
    req  = urllib.request.Request(
        _URL,
        data=body,
        headers={
            'X-Square-OpenAPI-Key': api_key,
            'Content-Type': 'application/json',
            'clienttype': 'binanceSkill',
        },
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _log(msg: str):
    print(msg, flush=True)
    try:
        with open(_LOG, 'a', encoding='utf-8') as f:
            f.write(msg + '\n')
    except Exception:
        pass


def main():
    if len(sys.argv) < 2:
        _log('[worker] 缺少参数：需要 tmp_json_file 路径')
        sys.exit(1)

    tmp_path = sys.argv[1]
    try:
        with open(tmp_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        original = data.get('content', '')
    except Exception as e:
        _log(f'[worker] 读取临时文件失败: {e}')
        sys.exit(1)

    if not original:
        _log('[worker] 内容为空，退出')
        sys.exit(0)

    # 导入改写引擎
    try:
        from multi_voice_rewriter import generate_three_versions, _VOICE, _log_post_ext
        has_log_ext = True
    except ImportError:
        from multi_voice_rewriter import generate_three_versions, _VOICE
        has_log_ext = False

    versions = generate_three_versions(original)

    # 只发 KEY1(蓝桉) 和 KEY2(牛来)，KEY0(姓赵不宣)已由 square_auto_post 发过
    for idx in [1, 2]:
        content = versions.get(idx, '')
        if not content:
            _log(f'[worker] KEY{idx} 内容生成失败，跳过')
            continue

        delay = _DELAYS.get(idx, 300)
        name  = _VOICE[idx]['name']
        ref   = _REFS[idx]

        _log(f'[worker] 等待{delay}s → {name}...')
        time.sleep(delay)

        try:
            result = _post(content, idx)
            code   = result.get('code', '?')
            if code == '000000':
                share = result.get('data', {}).get('shareLink', '')
                post_id = result.get('data', {}).get('id', '')
                _log(f'[worker] {name} ✅ id={post_id} {share}')
                # 写入日志
                try:
                    import importlib
                    log_mod = importlib.import_module('square_auto_post')
                    log_mod._log_post(f'multi_voice_key{idx}', content, result)
                except Exception:
                    pass
            else:
                _log(f'[worker] {name} ❌ code={code} msg={result.get("message","")}')
        except Exception as e:
            _log(f'[worker] {name} 异常: {e}')

    # 清理临时文件
    try:
        Path(tmp_path).unlink()
    except Exception:
        pass

    _log('[worker] 完成')


if __name__ == '__main__':
    main()
