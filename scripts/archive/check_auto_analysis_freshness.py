#!/usr/bin/env python3
"""auto-analysis断供确定性检查 [2026-09-30 苏摩111]
根修：原watcher由LLM做时间算术→时区混淆假阳性（13:31 UTC跑检查，数据12:16:09 UTC
仅74分钟<90分钟阈值，agent把UTC时间戳跟北京时间混算成9小时→误报断供）。
本脚本=唯一裁判：确定性算术，输出最后一行给cron原样转发。
阈值150min（生产班2h + 30min余量）。
接入位置：openclaw cron auto-analysis（every 3h）唯一判定入口。
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

P = Path(__file__).parent.parent / 'data' / 'auto_analysis_latest.json'
THRESHOLD_MIN = 150


def _parse_ts(raw: str):
    s = str(raw).strip()
    if s.endswith(' UTC'):
        s = s[:-4]
    s = s.replace(' ', 'T')
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def main():
    if not P.exists():
        print('auto-analysis断供 [文件不存在]', flush=True)
        return
    try:
        d = json.loads(P.read_text())
        ts_raw = d.get('timestamp') or ''
        ts = _parse_ts(ts_raw)
    except Exception as e:
        print(f'auto-analysis断供 [解析失败: {type(e).__name__}]', flush=True)
        return
    if ts is None:
        print(f'auto-analysis断供 [时间戳不可解析: {ts_raw!r}]', flush=True)
        return
    age = (datetime.now(timezone.utc) - ts).total_seconds() / 60.0
    if age > THRESHOLD_MIN:
        print(f'auto-analysis断供 [{ts_raw}] 数据已{int(age)}分钟未更新(阈值{THRESHOLD_MIN})', flush=True)
    else:
        print('NO_REPLY', flush=True)


if __name__ == '__main__':
    main()
