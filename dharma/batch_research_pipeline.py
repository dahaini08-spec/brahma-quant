#!/usr/bin/env python3
"""
batch_research_pipeline.py — 梵天达摩院 全标的系统化研究流水线
设计院 2026-06-14

三阶段流水线：
  Stage 1: 批量下载历史数据（币安永续合约）
  Stage 2: 构建 fixed parquet（预计算指标）
  Stage 3: 批量WFV训练 + 写入 dharma_runtime.json

分级策略（设计院铁律）：
  Tier1 (≤2020上市, 5年+): 标准100K训练, WFV 12/12
  Tier2 (2021上市, 3-5年):  fast模式, WFV 8/12
  Tier3 (2022-2023, 2-3年): fast模式, 标注「待积累」
  质量门控: WR<55% 或 n<200 → 不写入核心参数

用法:
  python3 dharma/batch_research_pipeline.py --stage all
  python3 dharma/batch_research_pipeline.py --stage download
  python3 dharma/batch_research_pipeline.py --stage build
  python3 dharma/batch_research_pipeline.py --stage train
  python3 dharma/batch_research_pipeline.py --stage train --symbols HYPEUSDT,SOLUSDT,XRPUSDT
  python3 dharma/batch_research_pipeline.py --dry-run
"""
import os, sys, json, time, logging, argparse, subprocess, urllib.request
from pathlib import Path
from datetime import datetime, timezone

BASE       = Path(__file__).parent.parent
DHARMA_DIR = BASE / 'dharma'
DATA_DIR   = BASE / 'data'
FIXED_DIR  = DATA_DIR / 'backtest' / 'fixed'
RAW_DIR    = DHARMA_DIR / 'data'
RESULTS    = DHARMA_DIR / 'results'
RUNTIME    = DATA_DIR / 'dharma_runtime.json'
LOG_FILE   = DHARMA_DIR / 'batch_pipeline.log'

FIXED_DIR.mkdir(parents=True, exist_ok=True)
RESULTS.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE),
    ]
)
log = logging.getLogger('pipeline')

# ══════════════════════════════════════════════════════════════════
# 标的分级（设计院 2026-06-14）
# ══════════════════════════════════════════════════════════════════

TIER1_SYMBOLS = [
    # ≤2020年上市，5年+历史数据，标准100K WFV
    'BTCUSDT','ETHUSDT','BNBUSDT','BCHUSDT','LTCUSDT','XRPUSDT','ADAUSDT',
    'DOGEUSDT','DOTUSDT','LINKUSDT','ATOMUSDT','SOLUSDT','AVAXUSDT','UNIUSDT',
    'FILUSDT','AAVEUSDT','SUSHIUSDT','COMPUSDT','NEARUSDT','ZECUSDT',
    'XLMUSDT','XMRUSDT','TRXUSDT','ETCUSDT','DASHUSDT','NEOUSDT','YFIUSDT',
    'SNXUSDT','RUNEUSDT','THETAUSDT','VETUSDT','KSMUSDT','XTZUSDT','ZRXUSDT',
    'ALGOUSDT','BANDUSDT','BATUSDT','BELUSDT','CRVUSDT','EGLDUSDT','ENJUSDT',
    'GRTUSDT','ICXUSDT','IOSTUSDT','KAVAUSDT','KNCUSDT','ONTUSDT','QTUMUSDT',
    'RLCUSDT','RSRUSDT','SKLUSDT','STORJUSDT','TRBUSDT','ZILUSDT','ZENUSDT',
    'IOTAUSDT','AXSUSDT',
]

TIER2_SYMBOLS = [
    # 2021年上市，3-5年历史数据，fast模式
    'SOLUSDT','MATICUSDT','SANDUSDT','MANAUSDT','CHZUSDT','GALAUSDT',
    'FTMUSDT','LUNAUSDT','APEUSDT','OPUSDT','ARBUSDT','INJUSDT','SUIUSDT',
    'APTUSDT','STXUSDT','GMXUSDT','DYDXUSDT','LPTUSDT','FLOWUSDT','IMXUSDT',
    'CELOUSDT','C98USDT','1INCHUSDT','ANKRUSDT','ALICEUSDT','ARPAUSDT',
    'ARUSDT','AXSUSDT','1000SHIBUSDT','ALGOUSDT',
]

TIER3_SYMBOLS = [
    # 2022-2023年上市，2-3年，fast+标注待积累
    'HYPEUSDT','WLDUSDT','TIAUSDT','DRIFTUSDT','RENDERUSDT','TAOUSDT',
    'TONUSDT','ONDOUSDT','FETUSDT','INJUSDT','BLURUSDT','PENDLEUSDT',
    'JUPUSDT','DYMUSDT','ALTUSDT','STRKUSDT','MNTUSDT','CFXUSDT',
    'SSVUSDT','WIFUSDT','BONKUSDT','1000PEPEUSDT','1000FLOKIUSDT',
    'NFPUSDT','ACEUSDT','MANTAUSDT','ZETAUSDT','AIUSDT','PIXELUSDT',
    'SAGAUSDT','VANRYUSDT','BOMEUSDT','ETHFIUSDT','ENAUSDT','WUSDT',
    'TNSRUSDT','PORTALUSDT','AXLUSDT','XAIUSDT','JTOUSDT',
]

# 质量门控（设计院铁律）
MIN_WR_CORE    = 0.57   # 写入核心参数最低WR
MIN_N_CORE     = 1000   # 写入核心参数最低样本（大样本铁证制）
MIN_WR_REF     = 0.52   # 写入参考级最低WR
MIN_N_REF      = 200    # 写入参考级最低样本
MIN_OOS_CORE   = 10     # 写入核心参数最低OOS通过数（12/12中）
MIN_OOS_REF    = 7      # 写入参考级最低OOS通过数

# ══════════════════════════════════════════════════════════════════
# Stage 1: 批量数据下载
# ══════════════════════════════════════════════════════════════════

def download_klines(symbol: str, interval: str, start_ms: int, output_path: Path) -> int:
    """下载单标的单周期历史K线，返回下载行数"""
    import pandas as pd

    if output_path.exists():
        # 增量更新：只下载最新数据
        df_exist = pd.read_parquet(output_path)
        if len(df_exist) > 100:
            last_ts = int(df_exist.index[-1].timestamp() * 1000)
            start_ms = last_ts + 1
            log.debug(f'  [{symbol}/{interval}] 增量更新从 {df_exist.index[-1]}')

    all_rows = []
    cur_start = start_ms
    now_ms = int(time.time() * 1000)

    while cur_start < now_ms:
        url = (f'https://fapi.binance.com/fapi/v1/klines'
               f'?symbol={symbol}&interval={interval}'
               f'&startTime={cur_start}&limit=1500')
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                rows = json.loads(r.read())
        except Exception as e:
            log.warning(f'  [{symbol}/{interval}] 请求失败: {e}')
            time.sleep(2)
            continue

        if not rows:
            break
        all_rows.extend(rows)
        cur_start = rows[-1][0] + 1
        if len(rows) < 1500:
            break
        time.sleep(0.05)  # 限速

    if not all_rows:
        return 0

    df = pd.DataFrame(all_rows, columns=[
        'open_time','open','high','low','close','volume',
        'close_time','quote_vol','n_trades','taker_buy_base',
        'taker_buy_quote','ignore'
    ])
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
    df = df.set_index('open_time')
    for col in ['open','high','low','close','volume']:
        df[col] = df[col].astype(float)

    # 合并增量
    if output_path.exists():
        df_old = pd.read_parquet(output_path)
        df = pd.concat([df_old, df[~df.index.isin(df_old.index)]]).sort_index()

    df.to_parquet(output_path)
    return len(df)


def stage_download(symbols: list, start_year: int = 2019, dry_run: bool = False):
    """Stage 1: 批量下载历史数据"""
    log.info(f'=== Stage 1: 批量下载 {len(symbols)} 个标的 ===')
    start_ms = int(datetime(start_year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    intervals = ['1h', '4h', '15m', '1d']

    total = done = failed = skipped = 0
    for i, sym in enumerate(symbols):
        sym_ok = True
        for iv in intervals:
            out = RAW_DIR / f'{sym.lower()}_{iv}_2019_2026.parquet'
            total += 1
            if dry_run:
                log.info(f'  [DRY] {sym}/{iv} → {out.name}')
                continue
            try:
                n = download_klines(sym, iv, start_ms, out)
                if n > 0:
                    done += 1
                    log.info(f'  [{i+1}/{len(symbols)}] {sym}/{iv}: {n}行 ✅')
                else:
                    skipped += 1
                    log.warning(f'  [{i+1}/{len(symbols)}] {sym}/{iv}: 无数据 ⚠️')
                    sym_ok = False
            except Exception as e:
                failed += 1
                log.error(f'  [{i+1}/{len(symbols)}] {sym}/{iv}: 失败 ❌ {e}')
                sym_ok = False
        if not dry_run:
            time.sleep(0.1)  # 标的间间隔

    log.info(f'Stage 1 完成: 成功={done} 跳过={skipped} 失败={failed}')
    return done, failed


# ══════════════════════════════════════════════════════════════════
# Stage 2: 构建 fixed parquet（预计算技术指标）
# ══════════════════════════════════════════════════════════════════

def build_fixed(symbol: str, dry_run: bool = False) -> bool:
    """调用现有 build_fixed_dataset.py 构建单标的 fixed 文件"""
    out_check = FIXED_DIR / f'{symbol.lower()}_1h_fixed.parquet'
    if out_check.exists():
        log.debug(f'  [{symbol}] fixed文件已存在，跳过')
        return True

    if dry_run:
        log.info(f'  [DRY] build_fixed {symbol}')
        return True

    try:
        r = subprocess.run(
            ['python3', str(DHARMA_DIR / 'build_fixed_dataset.py'), '--symbol', symbol],
            capture_output=True, text=True, timeout=120,
            cwd=str(BASE)
        )
        if r.returncode == 0:
            log.info(f'  [{symbol}] build_fixed ✅')
            return True
        else:
            log.error(f'  [{symbol}] build_fixed ❌: {r.stderr[-200:]}')
            return False
    except Exception as e:
        log.error(f'  [{symbol}] build_fixed 异常: {e}')
        return False


def stage_build(symbols: list, dry_run: bool = False):
    """Stage 2: 批量构建 fixed parquet"""
    log.info(f'=== Stage 2: 构建 fixed parquet ({len(symbols)}个标的) ===')
    done = failed = 0
    for i, sym in enumerate(symbols):
        log.info(f'[{i+1}/{len(symbols)}] {sym} 构建 fixed...')
        if build_fixed(sym, dry_run):
            done += 1
        else:
            failed += 1
    log.info(f'Stage 2 完成: 成功={done} 失败={failed}')
    return done, failed


# ══════════════════════════════════════════════════════════════════
# Stage 3: 批量WFV训练 + 写入 dharma_runtime.json
# ══════════════════════════════════════════════════════════════════

def train_symbol(symbol: str, tier: int, dry_run: bool = False) -> dict:
    """单标的WFV训练，返回结果字典"""
    if tier == 1:
        n_random, n_hillclimb = 80000, 20000
        mode_tag = 'standard'
    else:
        n_random, n_hillclimb = 2000, 500
        mode_tag = 'fast'

    if dry_run:
        log.info(f'  [DRY] train {symbol} tier={tier} {mode_tag}')
        return {}

    # 复用 train_100k_v7.py 但针对单标的
    # 由于现在只支持BTC+ETH，需要用 offline_brahma_replay.py 替代
    replay_script = DHARMA_DIR / 'offline_brahma_replay.py'
    if not replay_script.exists():
        log.warning(f'  [{symbol}] offline_brahma_replay.py不存在，尝试anchored_wfv_v7.py')
        script = DHARMA_DIR / 'anchored_wfv_v7.py'
    else:
        script = replay_script

    try:
        r = subprocess.run(
            ['python3', str(script),
             '--symbol', symbol,
             '--n-random', str(n_random),
             '--n-hillclimb', str(n_hillclimb),
             '--json-output'],
            capture_output=True, text=True, timeout=600,
            cwd=str(BASE)
        )
        if r.returncode == 0:
            # 解析JSON结果
            for line in r.stdout.split('\n'):
                if line.startswith('{'):
                    try:
                        return json.loads(line)
                    except:
                        pass
            log.warning(f'  [{symbol}] 训练完成但无JSON输出')
            return {'symbol': symbol, 'status': 'no_json', 'stdout': r.stdout[-500:]}
        else:
            log.error(f'  [{symbol}] 训练失败: {r.stderr[-300:]}')
            return {'symbol': symbol, 'status': 'failed', 'error': r.stderr[-200:]}
    except subprocess.TimeoutExpired:
        log.error(f'  [{symbol}] 训练超时(10min)')
        return {'symbol': symbol, 'status': 'timeout'}
    except Exception as e:
        log.error(f'  [{symbol}] 训练异常: {e}')
        return {'symbol': symbol, 'status': 'error', 'error': str(e)}


def write_to_runtime(symbol: str, result: dict, tier: int):
    """将训练结果写入 dharma_runtime.json（设计院质量门控）"""
    wr   = result.get('wr', 0)
    n    = result.get('n', 0)
    oos  = result.get('oos_pass', 0)
    pf   = result.get('pf', 0)
    ev   = result.get('ev', 0)

    # 质量门控
    if wr >= MIN_WR_CORE and n >= MIN_N_CORE and oos >= MIN_OOS_CORE:
        grade = 'S' if (wr >= 0.65 and n >= 2000) else 'A'
        write_level = 'core'
    elif wr >= MIN_WR_REF and n >= MIN_N_REF and oos >= MIN_OOS_REF:
        grade = 'B' if wr >= 0.55 else 'C'
        write_level = 'reference'
    else:
        grade = 'INSUFFICIENT'
        write_level = 'skip'
        log.warning(f'  [{symbol}] 质量不足，跳过写入 WR={wr:.1%} n={n} OOS={oos}')
        return False

    # 读取运行时
    try:
        with open(RUNTIME) as f:
            dr = json.load(f)
    except:
        dr = {'_meta': {}, 'sym_params': {}}

    entry = {
        'thr':       result.get('threshold', 138),
        'sl_mult':   result.get('sl_mult', 2.0),
        'mh':        result.get('hold_bars', 12),
        'kelly_pos': min(result.get('kelly', 0.04), 0.08),
        'wr':        round(wr, 4),
        'pf':        round(pf, 3),
        'ev':        round(ev, 4),
        'n':         n,
        'grade':     grade,
        'tier':      tier,
        'write_level': write_level,
        'source':    f'batch_pipeline_{datetime.utcnow().strftime("%Y%m%d")}',
        'ts':        datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'),
    }
    if tier >= 3:
        entry['note'] = '待积累，样本不足铁证级'

    dr.setdefault('sym_params', {})[symbol] = entry
    dr['_meta']['updated'] = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')

    with open(RUNTIME, 'w') as f:
        json.dump(dr, f, ensure_ascii=False, indent=2)

    log.info(f'  [{symbol}] ✅ 写入 {write_level} grade={grade} WR={wr:.1%} n={n} OOS={oos}')
    return True


def stage_train(symbols_tier: list, dry_run: bool = False):
    """Stage 3: 批量训练 + 写入"""
    log.info(f'=== Stage 3: 批量WFV训练 ({len(symbols_tier)}个标的) ===')

    summary = {'done': 0, 'skipped': 0, 'failed': 0, 'written': 0}
    start_all = time.time()

    for i, (sym, tier) in enumerate(symbols_tier):
        # 检查 fixed 文件是否存在
        if not (FIXED_DIR / f'{sym.lower()}_1h_fixed.parquet').exists():
            log.warning(f'[{i+1}/{len(symbols_tier)}] {sym} fixed文件不存在，跳过')
            summary['skipped'] += 1
            continue

        log.info(f'[{i+1}/{len(symbols_tier)}] {sym} (Tier{tier}) 训练中...')
        t0 = time.time()
        result = train_symbol(sym, tier, dry_run)
        elapsed = time.time() - t0

        if not result or result.get('status') in ('failed','timeout','error'):
            summary['failed'] += 1
            continue

        summary['done'] += 1

        if not dry_run and result.get('wr'):
            if write_to_runtime(sym, result, tier):
                summary['written'] += 1

        log.info(f'  [{sym}] 耗时={elapsed:.1f}s WR={result.get("wr",0):.1%} n={result.get("n",0)}')

    total_elapsed = time.time() - start_all
    log.info(f'Stage 3 完成: 训练={summary["done"]} 写入={summary["written"]} '
             f'跳过={summary["skipped"]} 失败={summary["failed"]} '
             f'总耗时={total_elapsed/60:.1f}min')
    return summary


# ══════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════

def get_symbols_with_tier(target: list = None) -> list:
    """返回 [(symbol, tier), ...] 列表"""
    result = []
    seen = set()
    for sym in (TIER1_SYMBOLS):
        if sym not in seen and (target is None or sym in target):
            result.append((sym, 1))
            seen.add(sym)
    for sym in TIER2_SYMBOLS:
        if sym not in seen and (target is None or sym in target):
            result.append((sym, 2))
            seen.add(sym)
    for sym in TIER3_SYMBOLS:
        if sym not in seen and (target is None or sym in target):
            result.append((sym, 3))
            seen.add(sym)
    return result


def main():
    ap = argparse.ArgumentParser(description='梵天达摩院 全标的批量研究流水线')
    ap.add_argument('--stage', choices=['all','download','build','train','status'],
                    default='status', help='执行阶段')
    ap.add_argument('--symbols', default=None,
                    help='指定标的（逗号分隔），默认全部')
    ap.add_argument('--start-year', type=int, default=2019)
    ap.add_argument('--tier', type=int, choices=[1,2,3], default=None,
                    help='只处理指定Tier')
    ap.add_argument('--dry-run', action='store_true',
                    help='试运行，不实际下载/训练/写入')
    args = ap.parse_args()

    # 解析目标标的
    target = None
    if args.symbols:
        target = [s.strip().upper() for s in args.symbols.split(',')]

    symbols_with_tier = get_symbols_with_tier(target)
    if args.tier:
        symbols_with_tier = [(s,t) for s,t in symbols_with_tier if t == args.tier]

    all_symbols = [s for s,t in symbols_with_tier]

    # 状态检查
    if args.stage == 'status':
        import json as _j
        try:
            dr = _j.loads(open(RUNTIME).read())
            covered = set(dr.get('sym_params', {}).keys())
        except:
            covered = set()

        t1 = [(s,t) for s,t in symbols_with_tier if t==1]
        t2 = [(s,t) for s,t in symbols_with_tier if t==2]
        t3 = [(s,t) for s,t in symbols_with_tier if t==3]

        print('\n📊 梵天达摩院 全标的研究状态')
        print(f'══════════════════════════════')
        print(f'Tier1 (标准100K): {len(t1)}个 | 已完成: {sum(1 for s,_ in t1 if s in covered)}')
        print(f'Tier2 (fast):     {len(t2)}个 | 已完成: {sum(1 for s,_ in t2 if s in covered)}')
        print(f'Tier3 (待积累):   {len(t3)}个 | 已完成: {sum(1 for s,_ in t3 if s in covered)}')
        print(f'合计覆盖: {len(covered)} / {len(symbols_with_tier)}')
        print()

        not_done = [(s,t) for s,t in symbols_with_tier if s not in covered]
        print(f'待训练: {len(not_done)}个')
        fixed_ok = sum(1 for s,_ in not_done if (FIXED_DIR/f'{s.lower()}_1h_fixed.parquet').exists())
        raw_ok   = sum(1 for s,_ in not_done if (RAW_DIR/f'{s.lower()}_1h_2019_2026.parquet').exists())
        print(f'  有fixed数据: {fixed_ok}个（可直接训练）')
        print(f'  有raw数据:   {raw_ok}个（需build_fixed）')
        print(f'  需下载:      {len(not_done)-raw_ok}个')

        # 时间估算
        t1_pending = sum(1 for s,t in not_done if t==1)
        t2_pending = sum(1 for s,t in not_done if t==2)
        t3_pending = sum(1 for s,t in not_done if t==3)
        est_min = t1_pending * 8 + t2_pending * 1 + t3_pending * 0.5
        print(f'\n预计训练时间: {est_min:.0f}分钟 ({est_min/60:.1f}小时)')
        print(f'  Tier1×{t1_pending}标的×8min + Tier2×{t2_pending}×1min + Tier3×{t3_pending}×0.5min')
        return

    log.info(f'流水线启动: stage={args.stage} symbols={len(all_symbols)} dry_run={args.dry_run}')
    if args.dry_run:
        log.info('⚠️ DRY RUN 模式，不实际执行')

    if args.stage in ('all', 'download'):
        stage_download(all_symbols, args.start_year, args.dry_run)

    if args.stage in ('all', 'build'):
        stage_build(all_symbols, args.dry_run)

    if args.stage in ('all', 'train'):
        stage_train(symbols_with_tier, args.dry_run)

    log.info('流水线完成 ✅')


if __name__ == '__main__':
    main()
