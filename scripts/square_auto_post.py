#!/usr/bin/env python3
"""
square_auto_post.py — VIP策略→Binance Square全自动发帖 v2.0
[设计院封印 2026-09-11 苏摩111]

v2.0变更：
  - 废弃LLM重写（free_llm_client 45秒超时+风格漂移）
  - 改用square_template.py纯模板引擎（0秒+0漂移）
  - 旗舰帖接入brahma_manual_analysis 80维输出
  - IP放在后缀，顶端不出现姓赵不宣

接入位置：
  - cron: supercronic brahma_crontab.txt
  - 调用：python3 scripts/square_auto_post.py --sym BTC ETH
"""

import json, os, ssl, sys, time, urllib.request
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
import sys

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / "scripts"))
sys.path.insert(0, str(BASE / "scripts" / "square"))

CST = timezone(timedelta(hours=8))

# Square API
SQUARE_KEY = os.environ.get('SQUARE_KEY_0', 'd9f19e3f6ba3480584db27b09bec0f27')
SQUARE_URL = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'

_ctx = ssl.create_default_context()
_ctx.check_hostname = True
_ctx.verify_mode = ssl.CERT_REQUIRED

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
        except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    return False


def _mark_posted(content: str):
    import hashlib
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
    """跑分析→模板填充→发帖

    [2026-09-27 苏摩111 P0-2] BTC/ETH合并为单帖双币联读：
    - 复盘实锤：早间战场报告周发14帖0爆款、数据开头无钩子
    - 合并后频率减半（省出额度给旗舰帖），主题钩子开头（事件反直觉公式）
    - 2币并行分析→build_battlefield_report_combined单帖发布
    - 接入位置：cron square-auto-post（11:30/17:30 UTC触发，--sym BTC ETH）
    - 兼容性：单币种调用（--sym BTC）自动回退逐币旧路径build_battlefield_report
    """
    from square.square_template import build_battlefield_report, build_battlefield_report_combined, parse_analysis_output, audit_post
    from brahma_manual_analysis import run_analysis
    from pathlib import Path as _P
    import json as _json, time as _time

    # ── [W1 2026-09-28 苏摩111] 信号包SSOT：优先读auto_analysis_latest(≤2h新鲜)，不再重复跑分析 ──
    # 断层1根治：cron触发→先读包，包不新鲜才跑run_analysis(并回写包)
    def _fresh_signal_pkg(syms_needed: list, max_age_s: int = 7200):
        try:
            f = _P(__file__).parent.parent / 'data' / 'auto_analysis_latest.json'
            if not f.exists():
                return {}
            d = _json.loads(f.read_text())
            ts = d.get('timestamp', '')
            from datetime import datetime as _dt, timezone as _tz
            t = _dt.strptime(ts, '%Y-%m-%d %H:%M:%S UTC').replace(tzinfo=_tz.utc)
            age = _time.time() - t.timestamp()
            if age > max_age_s:
                return {}
            out = d.get('output', '')
            globals()['_fresh_out'] = out   # [2026-09-30 苏摩111] 供合并路径切片用
            pkgs = {}
            for sym in syms_needed:
                seg = _extract_sym_segment(out, sym)   # [2026-09-30 苏摩111] 单币段解析，修复串币
                if seg:
                    pkgs[sym] = parse_analysis_output(seg)
            return pkgs
        except Exception:
            return {}

    def _extract_sym_segment(full_out: str, sym: str) -> str:
        """从全文截取单币段：【BTC...到下一个【ETH或结尾
        [2026-09-30 苏摩111 价格串币bug根修] parse_analysis_output正则是全文first-match，
        传全文会让BTC/ETH拿到同一dict（后者覆盖前者），BTC帖发布ETH价位。
        接入位置：square_auto_post.py _fresh_signal_pkg + 合并发帖路径
        """
        import re as _re
        m = _re.search(rf'【{sym}(?:USDT)?[^】]*】', full_out)
        if not m:
            return ''
        start = m.start()
        nxt = _re.search(r'【(?:BTC|ETH|SOL|BNB|XRP|SUI|DOGE|ADA|LTC|LINK|AVAX)(?:USDT)?[^】]*】', full_out[start + 1:])
        end = (start + 1 + nxt.start()) if nxt else len(full_out)
        return full_out[start:end]

    fresh = _fresh_signal_pkg(syms)
    # ── 多币种：合并单帖路径（P0-2）──
    if len(syms) > 1:
        analysis_by_sym = {}
        for sym in syms:
            if sym in fresh:
                print(f'[{sym}] 信号包SSOT命中(≤2h新鲜)，跳过重复分析', flush=True)
                # [2026-09-30 苏摩111 价格串币bug根修] SSOT包=全文，必须切出单币段再parse
                # 根因：直接parse全文→两sym共用同一dict（正则全文first-match=ETH数字覆盖BTC）→BTC用ETH价位发布
                seg = _extract_sym_segment(_fresh_out, sym) if _fresh_out else ''
                if seg:
                    analysis_by_sym[sym] = parse_analysis_output(seg)
                else:
                    analysis_by_sym[sym] = fresh[sym]
                continue
            print(f'[{sym}] 生成分析报告...', flush=True)
            try:
                report = run_analysis(sym, push_jarvis=False)
            except Exception as e:
                print(f'[{sym}] 分析失败: {e}')
                continue
            analysis_by_sym[sym] = parse_analysis_output(report)

        if not analysis_by_sym:
            print('全部分析失败，本轮跳过发帖')
            return

        content = build_battlefield_report_combined(analysis_by_sym)

        ok, issues = audit_post(content)
        if not ok:
            print(f'审计失败: {issues}')
            return

        if _is_duplicate(content):
            print('24h内重复，跳过')
            return

        print(f'准备合并发帖 ({len(content)}字):')
        print(content[:200] + '...')

        if dry_run:
            print('DRY-RUN，跳过发帖')
            return

        resp = _post_to_square(content)
        if 'error' in resp:
            print(f'发帖失败: {resp["error"]}')
        else:
            print('✅ 合并发布成功')
            _mark_posted(content)
            _log_post('battlefield', content, resp)
        return

    # ── 单币种：逐币旧路径（兼容保留）──
    sym = syms[0]
    print(f'[{sym}] 生成分析报告...', flush=True)
    try:
        report = run_analysis(sym, push_jarvis=False)
    except Exception as e:
        print(f'[{sym}] 分析失败: {e}')
        return
    data = parse_analysis_output(report)
    data['price'] = data.get('price', 0)
    content = build_battlefield_report(sym, data)

    ok, issues = audit_post(content)
    if not ok:
        print(f'[{sym}] 审计失败: {issues}')
        return

    if _is_duplicate(content):
        print(f'[{sym}] 24h内重复，跳过')
        return

    print(f'[{sym}] 准备发帖 ({len(content)}字):')
    print(content[:200] + '...')

    if dry_run:
        print(f'[{sym}] DRY-RUN，跳过发帖')
        return

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
