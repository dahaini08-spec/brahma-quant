#!/usr/bin/env python3
"""
达摩院 M01 分批训练器
每次运行处理 BATCH_SIZE 个品种，保存进度到 /tmp/train_m01_progress.json
Gateway重启也不怕：下次从断点继续

用法:
  python3 dharma/train_m01_batch.py          # 跑下一批5个品种
  python3 dharma/train_m01_batch.py --status # 查看进度
  python3 dharma/train_m01_batch.py --reset  # 重置进度
"""
import sys, json, time, logging, argparse
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dharma.dharma_system_backtest import add_indicators, score_signal

BASE       = Path(__file__).parent.parent
DATA       = Path(__file__).parent / 'data'
RESULTS    = Path(__file__).parent / 'results'
PROGRESS_F = Path('/tmp/train_m01_progress.json')
RESULTS.mkdir(exist_ok=True)

BATCH_SIZE  = 3
THRESHOLDS  = [145, 150, 155, 160]
SL_MULTS    = [1.5, 2.0]
HOLD_HOURS  = [8, 12, 16]
STEP        = 3

logging.basicConfig(level=logging.INFO,
    format='[M01-BATCH %(asctime)s] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger('M01')

def load_progress():
    if PROGRESS_F.exists():
        return json.loads(PROGRESS_F.read_text())
    return {'results': {}, 'started_at': time.time()}

def save_progress(p):
    tmp = str(PROGRESS_F) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(p, f, ensure_ascii=False, indent=2)
    import os; os.replace(tmp, str(PROGRESS_F))

def all_symbols():
    return sorted([f.name.split('_')[0].upper() for f in DATA.glob('*_1h_*.parquet')])

def backtest_sym(df, thr, mh, sl_m, tp_m=3.0):
    pnls = []
    n = len(df)
    for i in range(200, n - mh - 1, STEP):
        row = df.iloc[i]
        for d in ['LONG', 'SHORT']:
            s = score_signal(row, d)
            if s['total'] < thr: continue
            c = row['close']; atr = row.get('atr', c * 0.015)
            if pd.isna(atr) or atr <= 0: atr = c * 0.015
            slp = c - atr*sl_m if d=='LONG' else c + atr*sl_m
            tpp = c + atr*sl_m*tp_m if d=='LONG' else c - atr*sl_m*tp_m
            pnl = 0.0
            for j in range(i+1, min(i+mh, n-1)):
                fh=df.iloc[j]['high']; fl=df.iloc[j]['low']
                if d=='LONG':
                    if fl<=slp: pnl=(slp-c)/c; break
                    if fh>=tpp: pnl=(tpp-c)/c; break
                else:
                    if fh>=slp: pnl=(c-slp)/c; break
                    if fl<=tpp: pnl=(c-tpp)/c; break
            else:
                pnl=(df.iloc[min(i+mh-1,n-1)]['close']-c)/c*(1 if d=='LONG' else -1)
            pnls.append(pnl)
    return pnls

def pf_wr(pnls):
    if not pnls: return {'pf':0,'wr':0,'n':0,'ev':0}
    wins=[p for p in pnls if p>0]; loss=[p for p in pnls if p<=0]
    pf=sum(wins)/(abs(sum(loss))+1e-9)
    return {'pf':round(pf,3),'wr':round(len(wins)/len(pnls),3),'n':len(pnls),
            'ev':round(sum(pnls)/len(pnls)*100,4)}

def run_batch():
    progress = load_progress()
    syms = all_symbols()
    done = set(progress['results'].keys())
    remaining = [s for s in syms if s not in done]

    if not remaining:
        log.info('✅ 所有%d品种已完成！', len(syms))
        finalize(progress, syms)
        return True

    batch = remaining[:BATCH_SIZE]
    log.info('本批: %s  (已完成%d/%d)', batch, len(done), len(syms))

    for sym in batch:
        f = DATA / f'{sym.lower()}_1h_2018_2026.parquet'
        if not f.exists():
            log.warning('%s 数据文件不存在，跳过', sym)
            progress['results'][sym] = {'pf':0,'params':'no_data','n':0}
            continue
        t0=time.time()
        df = pd.read_parquet(f)
        df = add_indicators(df)
        best = {'pf':0,'params':'','n':0,'wr':0}
        for thr in THRESHOLDS:
            for sl in SL_MULTS:
                for mh in HOLD_HOURS:
                    pnls = backtest_sym(df, thr, mh, sl)
                    r = pf_wr(pnls)
                    if r['pf'] > best['pf']:
                        best = {'pf':r['pf'],'params':f'thr={thr},sl={sl},mh={mh}',
                                'n':r['n'],'wr':r['wr'],'ev':r.get('ev',0)}
        progress['results'][sym] = best
        save_progress(progress)
        log.info('  %s: PF=%.3f %s (n=%d) %.1fs', sym, best['pf'], best['params'],
                 best['n'], time.time()-t0)

    done_now = len(progress['results'])
    remaining_after = len(syms) - done_now
    log.info('批次完成: %d/%d 已完成，还剩 %d 个品种', done_now, len(syms), remaining_after)

    if remaining_after == 0:
        finalize(progress, syms)
        return True
    return False

def finalize(progress, syms):
    results = progress['results']
    pf_list = [v['pf'] for v in results.values() if v['pf'] > 0]
    avg_pf = round(sum(pf_list)/len(pf_list), 3) if pf_list else 0
    good = [s for s,v in results.items() if v['pf'] >= 1.2]
    best = max(results.items(), key=lambda x: x[1]['pf'], default=('?',{'pf':0}))

    log.info('='*60)
    log.info('🏆 M01 全部完成！')
    log.info('   总品种: %d  均PF: %.3f', len(syms), avg_pf)
    log.info('   PF≥1.2优质品种(%d): %s', len(good), good)
    log.info('   全局最优: %s PF=%.3f %s', best[0], best[1]['pf'], best[1].get('params',''))

    # 保存最终结果
    from datetime import datetime, timezone
    tag = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    out = RESULTS / f'train_100k_M01_{tag}.json'
    with open(out, 'w') as f:
        json.dump({'node':'M01','avg_pf':avg_pf,'good_syms':good,
                   'best':{'sym':best[0],'pf':best[1]['pf'],'params':best[1].get('params','')},
                   'results':results}, f, ensure_ascii=False, indent=2)
    log.info('   结果已保存: %s', out)
    # 清理进度文件
    PROGRESS_F.unlink(missing_ok=True)

def show_status():
    progress = load_progress()
    syms = all_symbols()
    done = progress['results']
    elapsed = (time.time() - progress.get('started_at', time.time())) / 3600
    print(f'进度: {len(done)}/{len(syms)} 品种')
    print(f'已用时: {elapsed:.1f}小时')
    if done:
        pf_list = [v['pf'] for v in done.values() if v['pf'] > 0]
        print(f'当前均PF: {sum(pf_list)/len(pf_list):.3f}' if pf_list else '无有效数据')
    remaining = [s for s in syms if s not in done]
    print(f'剩余品种: {remaining}')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--reset', action='store_true')
    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.reset:
        PROGRESS_F.unlink(missing_ok=True)
        print('进度已重置')
    else:
        done = run_batch()
        if done:
            print('M01_ALL_DONE')
        else:
            print('M01_BATCH_DONE')
