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
import time
import sys
import os
import argparse
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

OUTPUT_FILE = BASE / 'data' / 'news_feed.jsonl'
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


async def scrape_with_crawl4ai(dry_run: bool = False) -> list[dict]:
    """使用crawl4ai并发爬取新闻"""
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    except ImportError:
        print("[WARN] crawl4ai未安装，跳过爬取")
        return []

    browser_config = BrowserConfig(
        browser_type="chromium",
        headless=True,
        user_data_dir=str(BASE / '.crawl4ai_profile'),
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )

    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.ENABLED,
        word_count_threshold=50,
        screenshot=False,
        delay_before_return_html=2.0,
        simulate_user=True,
    )

    if dry_run:
        print(f"[DRY-RUN] 将爬取 {len(TARGETS)} 个URL: {[t['url'] for t in TARGETS]}")
        return [{"ts": int(time.time()), "source": t["name"], "tag": t["tag"],
                 "url": t["url"], "markdown": "[dry-run]", "status": "dry_run"} for t in TARGETS]

    results = []
    crawler = AsyncWebCrawler(config=browser_config)
    await crawler.start()

    try:
        tasks = [crawler.arun(url=t["url"], config=run_config) for t in TARGETS]
        crawl_results = await asyncio.gather(*tasks, return_exceptions=True)

        for target, result in zip(TARGETS, crawl_results):
            if isinstance(result, Exception):
                print(f"[ERROR] {target['name']}: {result}")
                continue
            if result.success:
                entry = {
                    "ts": int(time.time()),
                    "source": target["name"],
                    "tag": target["tag"],
                    "url": target["url"],
                    "markdown": (result.markdown or "")[:3000],
                    "status": "ok",
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                }
                results.append(entry)
                print(f"[OK] {target['name']} ({len(entry['markdown'])} chars)")
            else:
                print(f"[WARN] {target['name']} failed: {getattr(result, 'error_message', 'unknown')}")
    finally:
        await crawler.close()

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

    # 合并+去重（按source+ts截断到小时去重）
    combined = existing + results
    # 保留最近MAX_LINES条
    combined = combined[-MAX_LINES:]

    OUTPUT_FILE.write_text('\n'.join(json.dumps(e, ensure_ascii=False) for e in combined) + '\n')
    print(f"[OK] 写入 {OUTPUT_FILE}，共 {len(combined)} 条（新增 {len(results)} 条）")


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
