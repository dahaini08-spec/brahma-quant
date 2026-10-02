#!/usr/bin/env python3
"""
data_janitor.py — 数据目录自动清理
[2026-10-02 苏摩111封印]

职责：
  1. live_signal_log.jsonl 保留最近7天（防止无限积累）
  2. exp_payloads_meta.json 保持为空列表（每次写入后清理）
  3. data/archive/ 超过30天的归档文件删除
  4. wiring_check_cache.json 超过1天强制失效

接入：crontab 每日 03:05 UTC 静默运行
"""
import json, time, shutil
from pathlib import Path
import sys

DATA    = Path(__file__).parent.parent / 'data'
ARCHIVE = DATA / 'archive'
ARCHIVE.mkdir(exist_ok=True)

def clean_signal_log(keep_days=7) -> str:
    f = DATA / 'live_signal_log.jsonl'
    if not f.exists(): return 'signal_log: 不存在'
    lines  = f.read_text().splitlines()
    cutoff = time.time() - keep_days * 86400
    kept   = [l for l in lines if _ts_ok(l, cutoff)]
    dropped = len(lines) - len(kept)
    f.write_text('\n'.join(kept) + ('\n' if kept else ''))
    return f'signal_log: {len(lines)}→{len(kept)}条 (删{dropped}条>7天) {f.stat().st_size//1024}KB'

def _ts_ok(line: str, cutoff: float) -> bool:
    try: return json.loads(line).get('ts', 0) >= cutoff
    except: return True  # 解析失败的保留

def clean_exp_payloads() -> str:
    f = DATA / 'exp_payloads_meta.json'
    if not f.exists(): return 'exp_payloads: 不存在'
    size = f.stat().st_size
    if size > 1024 * 100:  # >100KB 则清理
        archive = ARCHIVE / f'exp_payloads_{time.strftime("%Y%m%d_%H%M")}.json'
        shutil.copy2(str(f), str(archive))
        f.write_text('[]')
        return f'exp_payloads: {size//1024}KB → 归档+清空'
    return f'exp_payloads: {size}B 无需清理'

def clean_archive(keep_days=30) -> str:
    cutoff = time.time() - keep_days * 86400
    deleted = 0
    for f in ARCHIVE.glob('*.json'):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            deleted += 1
    return f'archive: 删除{deleted}个>{keep_days}天文件'

def clean_wiring_cache(max_age_h=24) -> str:
    f = DATA / 'wiring_check_cache.json'
    if not f.exists(): return 'wiring_cache: 不存在'
    age_h = (time.time() - f.stat().st_mtime) / 3600
    if age_h > max_age_h:
        f.unlink()
        return f'wiring_cache: {age_h:.0f}h旧，已删除（下次wiring运行自动重建）'
    return f'wiring_cache: {age_h:.0f}h 未过期'

def main():
    results = [
        clean_signal_log(),
        clean_exp_payloads(),
        clean_archive(),
        clean_wiring_cache(),
    ]
    # 计算data/总大小
    total_kb = sum(f.stat().st_size for f in DATA.glob('*.json')) // 1024
    results.append(f'data/总计: {total_kb}KB')
    
    report = '\n'.join(f'  {r}' for r in results)
    print(f'[janitor] {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}\n{report}')
    return 0

if __name__ == '__main__':
    sys.exit(main())
