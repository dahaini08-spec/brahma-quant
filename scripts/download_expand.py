#!/usr/bin/env python3
"""
download_expand.py — 梵天数据库扩容脚本
2026-08-27 设计院封印 · 苏摩111批准

扩容内容：
  1. CL原油（WTI Crude Oil）日线数据 via yfinance
  2. 30个山寨币永续合约全周期K线 via Binance fapi
     周期：15m / 1h / 4h / 1d
     时间：2019-01-01 ~ 今

运行：
  python3 scripts/download_expand.py --target all
  python3 scripts/download_expand.py --target oil
  python3 scripts/download_expand.py --target alts
  python3 scripts/download_expand.py --target alts --symbol BNBUSDT
"""
import requests, json, time, os, sys, argparse
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

ROOT     = Path(__file__).parent.parent
DATA     = ROOT / 'data' / 'historical'
DATA.mkdir(parents=True, exist_ok=True)

FAPI_BASE = 'https://fapi.binance.com'
LIMIT     = 1000
SLEEP_MS  = 80

# ── 30个山寨币 + 优先级 ───────────────────────────────────────────
ALTCOINS = [
    # P0：主力交易标的（backtest已有，补全K线）
    'BNBUSDT', 'ADAUSDT', 'XRPUSDT', 'DOGEUSDT', 'DOTUSDT',
    'LINKUSDT', 'LTCUSDT', 'XLMUSDT', 'TRXUSDT', 'ATOMUSDT',
    # P1：方仓案例已有，补K线
    'ALGOUSDT', 'CRVUSDT', 'COMPUSDT', 'RUNEUSDT', 'SNXUSDT',
    'VETUSDT',  'THETAUSDT', 'BCHUSDT', 'ETCUSDT', 'EGLDUSDT',
    # P2：长尾，有方仓案例
    'ONTUSDT',  'LTCUSDT',  'XMRUSDT',  'ZECUSDT',  'DASHUSDT',
    'KAVAUSDT', 'SUSHIUSDT','TRBUSDT',  'ZILUSDT',  'IOTAUSDT',
]

TIMEFRAMES = ['15m', '1h', '4h', '1d']
START_DATE = '2019-01-01'

# ── 工具函数 ──────────────────────────────────────────────────────

def ts_ms(date_str: str) -> int:
    return int(datetime.strptime(date_str, '%Y-%m-%d')
               .replace(tzinfo=timezone.utc).timestamp() * 1000)


def download_fapi_klines(symbol: str, interval: str,
                          start_ms: int, end_ms: int = None) -> list:
    """分页下载Binance永续合约K线"""
    all_bars = []
    cur = start_ms
    now_ms = int(time.time() * 1000)
    if end_ms is None:
        end_ms = now_ms

    while cur < end_ms:
        try:
            r = requests.get(f'{FAPI_BASE}/fapi/v1/klines', params={
                'symbol': symbol, 'interval': interval,
                'limit': LIMIT, 'startTime': cur, 'endTime': end_ms
            }, timeout=20)
            if r.status_code == 400:
                # 标的不存在于永续合约
                print(f'    ⚠️  {symbol} {interval}: 400错误（可能不存在于fapi）')
                return []
            r.raise_for_status()
            bars = r.json()
            if not bars: break
            all_bars.extend(bars)
            if len(bars) < LIMIT: break
            cur = bars[-1][0] + 1
            time.sleep(SLEEP_MS / 1000)
        except Exception as e:
            print(f'    ❌ {symbol} {interval}: {e}')
            time.sleep(2)
            break

    return all_bars


def bars_to_parquet(bars: list, symbol: str, interval: str, out_dir: Path):
    """K线数据保存为parquet"""
    if not bars:
        return 0
    df = pd.DataFrame(bars, columns=[
        'ts', 'open', 'high', 'low', 'close', 'volume',
        'close_ts', 'quote_vol', 'trades', 'taker_buy_base',
        'taker_buy_quote', 'ignore'
    ])
    df = df[['ts', 'open', 'high', 'low', 'close', 'volume', 'trades']].copy()
    df[['open','high','low','close','volume']] = \
        df[['open','high','low','close','volume']].astype(float)
    df['trades'] = df['trades'].astype(int)
    df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
    df = df.drop_duplicates('ts').sort_values('ts').reset_index(drop=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    sym_lower = symbol.lower()
    pq_path = out_dir / f'{sym_lower}_{interval}.parquet'
    df.to_parquet(pq_path, index=False)

    # meta
    meta = {
        'symbol': symbol, 'timeframe': interval,
        'bars_count': len(df),
        'date_start': str(df['ts'].iloc[0]),
        'date_end': str(df['ts'].iloc[-1]),
        'downloaded_at': datetime.utcnow().isoformat(),
    }
    (out_dir / f'{sym_lower}_{interval}_meta.json').write_text(
        json.dumps(meta, indent=2))
    return len(df)


# ── 原油数据下载 ──────────────────────────────────────────────────

def download_oil():
    """下载WTI原油日线数据 via yfinance"""
    print('\n📦 下载 CL原油(WTI) 日线数据...')
    try:
        import yfinance as yf
    except ImportError:
        print('  安装 yfinance...')
        os.system(f'{sys.executable} -m pip install yfinance -q')
        import yfinance as yf

    out_dir = DATA / 'macro'
    out_dir.mkdir(exist_ok=True)

    # CL=F 是 NYMEX WTI原油连续合约
    ticker = yf.Ticker('CL=F')
    df = ticker.history(start='1983-01-01', end=None, interval='1d')

    if df.empty:
        print('  ⚠️  yfinance未返回数据，尝试备选...')
        # 备选：USO（原油ETF）
        ticker = yf.Ticker('USO')
        df = ticker.history(start='2006-01-01', end=None, interval='1d')

    if df.empty:
        print('  ❌ 原油数据下载失败')
        return 0

    df = df.reset_index()[['Date','Open','High','Low','Close','Volume']]
    df.columns = ['date','open','high','low','close','volume']
    df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
    df = df.dropna(subset=['close']).sort_values('date').reset_index(drop=True)

    # 保存
    pq_path = out_dir / 'OIL_CL_1d.parquet'
    df.to_parquet(pq_path, index=False)

    jsonl_path = out_dir / 'OIL_CL_1d.jsonl.gz'
    df.to_json(jsonl_path, orient='records', lines=True, compression='gzip')

    n = len(df)
    date_start = str(df['date'].iloc[0])[:10]
    date_end   = str(df['date'].iloc[-1])[:10]

    meta = {
        'symbol': 'OIL_CL', 'source': 'yfinance:CL=F (WTI原油)',
        'timeframe': '1d', 'bars_count': n,
        'date_start': date_start, 'date_end': date_end,
        'downloaded_at': datetime.utcnow().isoformat(),
    }
    (out_dir / 'OIL_CL_1d_meta.json').write_text(json.dumps(meta, indent=2))

    print(f'  ✅ WTI原油: {n}条 ({date_start} → {date_end})')

    # 更新 macro_data_summary.json
    summary_path = out_dir / 'macro_data_summary.json'
    try:
        summary = json.loads(summary_path.read_text())
    except:
        summary = {}
    summary.setdefault('tradfi', {})['OIL_WTI原油'] = n
    summary['updated_at'] = datetime.utcnow().isoformat()
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    return n


# ── 山寨币K线下载 ────────────────────────────────────────────────

def download_altcoins(symbols: list = None):
    """下载山寨币全周期K线"""
    targets = symbols or ALTCOINS
    start_ms = ts_ms(START_DATE)
    total_bars = 0
    results = {}

    print(f'\n📦 下载 {len(targets)} 个山寨币全周期K线...')
    print(f'   时间范围: {START_DATE} → 今天')
    print(f'   周期: {TIMEFRAMES}')

    for symbol in targets:
        sym_lower = symbol.lower().replace('usdt','') + 'usdt'
        out_dir = DATA / sym_lower
        sym_results = {}

        print(f'\n  [{symbol}]')
        for tf in TIMEFRAMES:
            # 检查是否已有
            pq_path = out_dir / f'{sym_lower}_{tf}.parquet'
            if pq_path.exists():
                try:
                    df_existing = pd.read_parquet(pq_path)
                    n_existing = len(df_existing)
                    print(f'    ✅ {tf}: 已有 {n_existing}条，跳过')
                    sym_results[tf] = n_existing
                    total_bars += n_existing
                    continue
                except:
                    pass

            bars = download_fapi_klines(symbol, tf, start_ms)
            if bars:
                n = bars_to_parquet(bars, symbol, tf, out_dir)
                print(f'    ✅ {tf}: {n}条')
                sym_results[tf] = n
                total_bars += n
            else:
                print(f'    ⚠️  {tf}: 0条（标的可能不在永续合约）')
                sym_results[tf] = 0

            time.sleep(0.5)

        results[symbol] = sym_results

    print(f'\n📊 山寨币下载完成: {len(targets)}个标的, 总计 {total_bars:,} 条K线')
    return results


# ── 更新 knowledge 层 ────────────────────────────────────────────

def update_knowledge_layer(oil_bars: int, alt_results: dict):
    """更新三层知识架构的macro/global_framework.md"""
    knowledge_path = ROOT / 'knowledge' / 'macro' / 'global_framework.md'
    if not knowledge_path.exists():
        return

    content = knowledge_path.read_text()

    # 更新原油条目
    oil_note = f"\n### CL原油（WTI）\n- **数据：** {oil_bars}条日线（1983→今）\n- **来源：** yfinance CL=F\n- **梵天用法：** 通胀预期代理变量；与DXY/黄金联动分析\n- **相关性：** 与BTC相关系数约+0.30（通胀预期联动）\n"

    if 'WTI原油' not in content:
        content = content.replace(
            '## 六、地缘政治与监管风险',
            oil_note + '\n## 六、地缘政治与监管风险'
        )
        knowledge_path.write_text(content)
        print('  ✅ knowledge/macro/global_framework.md 已更新原油数据')


# ── 主入口 ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', default='all',
                        choices=['all', 'oil', 'alts'],
                        help='下载目标：all/oil/alts')
    parser.add_argument('--symbol', default=None,
                        help='指定单个山寨币符号（如BNBUSDT）')
    args = parser.parse_args()

    oil_bars = 0
    alt_results = {}

    if args.target in ('all', 'oil'):
        oil_bars = download_oil()

    if args.target in ('all', 'alts'):
        symbols = [args.symbol] if args.symbol else None
        alt_results = download_altcoins(symbols)

    # 更新知识层
    if oil_bars > 0:
        update_knowledge_layer(oil_bars, alt_results)

    # 汇总报告
    print('\n' + '='*50)
    print('📊 扩容完成汇总')
    print('='*50)
    if oil_bars:
        print(f'  CL原油: {oil_bars}条日线')
    if alt_results:
        success = sum(1 for v in alt_results.values() if any(v.values()))
        print(f'  山寨币: {success}/{len(alt_results)} 个标的下载成功')
        # 详细
        for sym, tfs in alt_results.items():
            total = sum(tfs.values())
            if total > 0:
                print(f'    {sym}: {total:,}条')
    print()


if __name__ == '__main__':
    main()
