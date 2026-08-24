#!/usr/bin/env python3
"""
news_scraper.py — 梵天市场情报采集器 v1.0
[设计院封印 2026-08-13 苏摩111]

功能：
  - 并发爬取 CoinDesk / CoinGecko / Binance公告
  - 输出 brahma_bus 兼容 JSONL
  - 每30min由cron调度，接入35维矩阵第10维（social hype）

用法：
  python3 scripts/news_scraper.py
  python3 scripts/news_scraper.py --dry-run
"""
import asyncio
import json
import subprocess
import time
import sys
import os
import argparse
from pathlib import Path
from datetime import datetime, timezone

# 自动确保依赖安装（防止容器重启后丢失）
for _pkg in ['httpx', 'html2text']:
    try:
        __import__(_pkg.replace('-','_'))
    except ImportError:
        subprocess.run([sys.executable, '-m', 'pip', 'install', _pkg, '-q', '--break-system-packages'], check=False)

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

OUTPUT_FILE   = BASE / 'data' / 'news_feed.jsonl'
WATERMARK_FILE = BASE / 'data' / 'news_last_pushed.json'  # 去重水位线
MAX_LINES = 500  # 保留最近500条，防止文件无限增长

# 目标URL（Binance官方API替代网页爬取）
TARGETS = [
    {
        "name": "coindesk_markets",
        "url": "https://www.coindesk.com/markets/",
        "tag": "market_news",
    },
    {
        "name": "coingecko_news",
        "url": "https://www.coingecko.com/en/news",
        "tag": "market_news",
    },
    {
        "name": "binance_blog",
        "url": "https://www.binance.com/en/blog",
        "tag": "binance_news",
    },
]

# RSS新闻源（CryptoPanic弃用，改用RSS）
# [2026-08-22 苏摩111] CoinDesk RSS + CoinTelegraph RSS 验证可用
RSS_URLS = [
    {"name": "coindesk_rss",      "url": "https://feeds.feedburner.com/CoinDesk",      "tag": "market_news"},
    {"name": "cointelegraph_rss",  "url": "https://cointelegraph.com/rss",             "tag": "market_news"},
]


async def scrape_with_crawl4ai(dry_run: bool = False) -> list[dict]:
    """
    使用 httpx+html2text 并发爬取新闻（轻量方案，无需Chromium系统库）
    crawl4ai Chromium模式在容器环境受限，改用httpx直接抓取
    """
    if dry_run:
        print(f"[DRY-RUN] 将爬取 {len(TARGETS)} 个URL: {[t['url'] for t in TARGETS]}")
        return [{"ts": int(time.time()), "source": t["name"], "tag": t["tag"],
                 "url": t["url"], "markdown": "[dry-run]", "status": "dry_run"} for t in TARGETS]

    try:
        import httpx
        import html2text as _h2t_mod
    except ImportError:
        print("[WARN] httpx/html2text未安装: pip install httpx html2text")
        return []

    h2t = _h2t_mod.HTML2Text()
    h2t.ignore_links = True
    h2t.ignore_images = True
    h2t.body_width = 0

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    results = []
    async with httpx.AsyncClient(headers=headers, timeout=20, follow_redirects=True) as client:
        tasks = [client.get(t["url"]) for t in TARGETS]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        for target, resp in zip(TARGETS, responses):
            if isinstance(resp, Exception):
                print(f"[ERROR] {target['name']}: {resp}")
                continue
            if resp.status_code == 200:
                markdown = h2t.handle(resp.text)
                # BM25风格过滤：只保留有实质内容的行
                lines = [l for l in markdown.splitlines() if len(l.strip()) > 30]
                clean_md = "\n".join(lines[:80])
                entry = {
                    "ts": int(time.time()),
                    "source": target["name"],
                    "tag": target["tag"],
                    "url": target["url"],
                    "markdown": clean_md[:3000],
                    "status": "ok",
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                    "http_status": resp.status_code,
                }
                results.append(entry)
                print(f"[OK] {target['name']} ({len(clean_md)} chars, HTTP {resp.status_code})")
            else:
                print(f"[WARN] {target['name']} HTTP {resp.status_code}")

        # ── RSS新闻源（CoinDesk RSS + CoinTelegraph RSS，补充市场行情新闻）──
        import xml.etree.ElementTree as _ET
        rss_tasks = [client.get(r["url"]) for r in RSS_URLS]
        rss_responses = await asyncio.gather(*rss_tasks, return_exceptions=True)
        rss_added = 0
        for rss_cfg, resp in zip(RSS_URLS, rss_responses):
            if isinstance(resp, Exception):
                print(f"[WARN] {rss_cfg['name']}: {resp}")
                continue
            if resp.status_code == 200:
                try:
                    root = _ET.fromstring(resp.text)
                    items = root.findall('.//item')[:15]
                    now_ts = int(time.time())
                    for item in items:
                        title = (item.findtext('title') or '').strip()
                        link  = (item.findtext('link')  or '').strip()
                        desc  = (item.findtext('description') or '').strip()[:300]
                        if title:
                            results.append({
                                "ts":        now_ts,
                                "source":    rss_cfg["name"],
                                "tag":       rss_cfg["tag"],
                                "url":       link,
                                "markdown":  f"{title}\n{desc}",
                                "title":     title,
                                "status":    "ok",
                                "scraped_at": datetime.now(timezone.utc).isoformat(),
                            })
                            rss_added += 1
                    print(f"[OK] {rss_cfg['name']}: +{len(items)}条")
                except Exception as e:
                    print(f"[WARN] {rss_cfg['name']} parse error: {e}")
            else:
                print(f"[WARN] {rss_cfg['name']} HTTP {resp.status_code}")
        if rss_added:
            print(f"[OK] RSS市场新闻合计: +{rss_added}条")
    return results

def write_output(results: list[dict]):
    """写入JSONL，保留最近MAX_LINES条"""
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    # 读取现有内容
    existing = []
    if OUTPUT_FILE.exists():
        for line in OUTPUT_FILE.read_text().strip().splitlines():
            try:
                existing.append(json.loads(line))
            except Exception:
                pass

    # 合并+去重（按URL去重，相同URL不重复写入）
    existing_urls = {e.get('url','') for e in existing if e.get('url')}
    new_results = [r for r in results if r.get('url','') not in existing_urls]
    combined = existing + new_results
    # 保留最近MAX_LINES条
    combined = combined[-MAX_LINES:]
    new_count = len(new_results)

    OUTPUT_FILE.write_text('\n'.join(json.dumps(e, ensure_ascii=False) for e in combined) + '\n')
    # 更新水位线：记录本次最新ts，下次只推更新内容
    new_ts = max((e.get('ts', 0) for e in results), default=0)
    if new_ts:
        WATERMARK_FILE.write_text(json.dumps({'last_ts': new_ts, 'last_sources': [e['source'] for e in results]}))
    print(f"[OK] 写入 {OUTPUT_FILE}，共 {len(combined)} 条（新增 {new_count} 条，水位线={new_ts})")


def main():
    parser = argparse.ArgumentParser(description='梵天新闻采集器')
    parser.add_argument('--dry-run', action='store_true', help='不实际爬取，仅验证配置')
    args = parser.parse_args()

    t0 = time.time()
    results = asyncio.run(scrape_with_crawl4ai(dry_run=args.dry_run))
    elapsed = time.time() - t0

    if results:
        write_output(results)
        print(f"[完成] 爬取 {len(results)}/{len(TARGETS)} 成功，耗时 {elapsed:.1f}s")
    else:
        print(f"[完成] 无新结果，耗时 {elapsed:.1f}s")


if __name__ == '__main__':
    main()
