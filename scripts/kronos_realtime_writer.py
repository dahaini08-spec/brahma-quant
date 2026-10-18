#!/usr/bin/env python3
"""
kronos_realtime_writer.py — Kronos L7层实时信号写入器
[设计院封印 2026-10-18 苏摩111]

接入位置：brahma_crontab.txt → 每30分钟运行
用途：恢复梵天 brahma_core.py L7_Kronos 层感知能力

背景：
  L7_Kronos 层读取 /tmp/kronos_signal.json
  该文件自2026-08-12起无人写入（HAR-RV替代后遗孤）
  → L7层静默1000条历史白白浪费
  本脚本：用Kronos-mini(15.7MB,0.2s推理)恢复实时p_up写入

输出格式（兼容brahma_core.py L7接口）：
{
  "p_up": 0.72,
  "confidence": 0.85,
  "method": "kronos_mini",
  "symbol": "BTCUSDT",
  "ts": 1234567890,
  "age_limit_h": 1.0
}
"""
import sys
import os
import json
import time
import traceback
from pathlib import Path

# 路径设置
BASE     = Path(__file__).parent.parent
VENV     = BASE / 'venv/lib/python3.11/site-packages'
KRONOS   = BASE / 'external/Kronos'
DATA     = BASE / 'data'
TOK_PATH = BASE / 'external/data/kronos_cache/models--NeoQuasar--Kronos-Tokenizer-base/snapshots/0e0117387f39004a9016484a186a908917e22426'
MDL_PATH = BASE / 'external/data/kronos_cache/models--NeoQuasar--Kronos-mini/snapshots/f4e68697d9d5aed55cef5c96aabc3376bcad9f81'
OUT_FILE = Path('/tmp/kronos_signal.json')
SHADOW   = DATA / 'kronos_bridge_shadow.jsonl'

sys.path.insert(0, str(VENV))
sys.path.insert(0, str(KRONOS))
os.environ['HF_DATASETS_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['OPENBLAS_NUM_THREADS'] = '1'
os.environ['OMP_NUM_THREADS'] = '1'

SYMBOLS = ['BTCUSDT', 'ETHUSDT']


def get_klines_from_state(symbol: str) -> list:
    """K선 취득: brahma_state → fallback Binance API 직접 호출"""
    sym_short = symbol.replace('USDT', '').lower()
    # 1. brahma_state_{sym}.json
    state_file = DATA / f'brahma_state_{sym_short}.json'
    if state_file.exists():
        try:
            d = json.loads(state_file.read_text())
            k1h = d.get('k1h', [])
            if len(k1h) >= 8:
                return k1h
        except Exception:
            pass
    # 2. 전역 brahma_state
    global_file = DATA / 'brahma_state.json'
    if global_file.exists():
        try:
            d = json.loads(global_file.read_text())
            sym_d = d.get(symbol, {})
            k1h = sym_d.get('k1h', [])
            if len(k1h) >= 8:
                return k1h
        except Exception:
            pass
    # 3. fallback: Binance fapi 직접 호출
    try:
        import urllib.request
        url = f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1h&limit=20'
        raw = json.loads(urllib.request.urlopen(url, timeout=8).read())
        return [(float(x[1]),float(x[2]),float(x[3]),float(x[4]),float(x[5])) for x in raw]
    except Exception as e:
        print(f'[kronos_writer] {symbol} API fallback 실패: {e}', file=sys.stderr)
    return []


def klines_to_df(k1h: list):
    """(o,h,l,c,v) 튜플 리스트 → DataFrame"""
    import pandas as pd
    import numpy as np
    rows = []
    for bar in k1h:
        if isinstance(bar, (list, tuple)) and len(bar) >= 5:
            o, h, l, c, v = float(bar[0]), float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4])
        elif isinstance(bar, dict):
            o = float(bar.get('open', 0))
            h = float(bar.get('high', 0))
            l = float(bar.get('low', 0))
            c = float(bar.get('close', 0))
            v = float(bar.get('volume', 0))
        else:
            continue
        rows.append({'open': o, 'high': h, 'low': l, 'close': c, 'volume': v})
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df['amount'] = df['close'] * df['volume']
    return df


def run_kronos(symbol: str, predictor, df, regime: str = '') -> dict:
    """Kronos mini 추론 → p_up 계산"""
    import pandas as pd
    import numpy as np

    n = len(df)
    x_ts = pd.Series(pd.date_range('2026-01-01', periods=n, freq='1h'))
    y_start = pd.Timestamp('2026-01-01') + pd.Timedelta(hours=n)
    y_ts = pd.Series(pd.date_range(y_start, periods=24, freq='1h'))

    try:
        pred = predictor.predict(
            df=df, x_timestamp=x_ts, y_timestamp=y_ts,
            pred_len=24, T=1.0, top_p=0.9, sample_count=1, verbose=False
        )
        cur_close = float(df['close'].iloc[-1])
        pred_closes = pred['close'].values
        p_up = float((pred_closes > cur_close).mean())
        vol  = float(np.std(pred_closes) / max(cur_close, 1))
        # confidence = 분포의 집중도 (중앙에서 벗어난 정도)
        confidence = abs(p_up - 0.5) * 2  # 0=완전중립, 1=완전확실
        return {
            'p_up':       round(p_up, 4),
            'confidence': round(confidence, 4),
            'volatility': round(vol, 6),
            'method':     'kronos_mini',
            'symbol':     symbol,
            'regime':     regime,
            'ts':         int(time.time()),
            'age_limit_h': 1.0,
        }
    except Exception as e:
        return {'error': str(e)[:80], 'symbol': symbol, 'ts': int(time.time())}


def append_shadow(record: dict):
    """kronos_bridge_shadow.jsonl에 추가 (history 유지)"""
    try:
        SHADOW.parent.mkdir(exist_ok=True)
        with open(SHADOW, 'a') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception:
        pass


def atomic_write(path: Path, data: dict):
    """원자적 파일 쓰기"""
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(path)


def main():
    t_start = time.time()
    print(f'[kronos_writer] 시작 {time.strftime("%H:%M:%S UTC", time.gmtime())}', flush=True)

    # 모델 로드 (0.2s)
    try:
        from model.kronos import KronosTokenizer, Kronos, KronosPredictor
        tokenizer = KronosTokenizer.from_pretrained(str(TOK_PATH), local_files_only=True)
        model     = Kronos.from_pretrained(str(MDL_PATH), local_files_only=True)
        predictor = KronosPredictor(model, tokenizer, max_context=512)
        print(f'[kronos_writer] 모델 로드: {time.time()-t_start:.1f}s', flush=True)
    except Exception as e:
        print(f'[kronos_writer] 모델 로드 실패: {e}', file=sys.stderr)
        return 1

    results = {}
    for symbol in SYMBOLS:
        k1h = get_klines_from_state(symbol)
        if len(k1h) < 8:
            print(f'[kronos_writer] {symbol}: K선 부족({len(k1h)}근)', flush=True)
            continue

        df = klines_to_df(k1h)
        if df is None or len(df) < 8:
            continue

        # regime 읽기 (옵션)
        regime = ''
        try:
            sym_short = symbol.replace('USDT', '').lower()
            sf = DATA / f'brahma_state_{sym_short}.json'
            if sf.exists():
                regime = json.loads(sf.read_text()).get('regime', '')
        except Exception:
            pass

        t2 = time.time()
        rec = run_kronos(symbol, predictor, df, regime)
        elapsed = time.time() - t2

        if 'error' in rec:
            print(f'[kronos_writer] {symbol} 추론 실패: {rec["error"]}', flush=True)
            continue

        print(f'[kronos_writer] {symbol}: p_up={rec["p_up"]:.3f} '
              f'conf={rec["confidence"]:.3f} vol={rec["volatility"]:.4f} '
              f'({elapsed:.1f}s)', flush=True)

        results[symbol] = rec
        append_shadow(rec)

    # /tmp/kronos_signal.json 쓰기 (BTC 기준, L7 호환)
    if 'BTCUSDT' in results:
        atomic_write(OUT_FILE, results['BTCUSDT'])
        print(f'[kronos_writer] /tmp/kronos_signal.json 갱신 ✅', flush=True)
    elif results:
        # BTC 없으면 첫 번째 결과 사용
        first = next(iter(results.values()))
        atomic_write(OUT_FILE, first)

    # ETH 별도 저장 (L7층 ETH분석용)
    if 'ETHUSDT' in results:
        eth_path = Path('/tmp/kronos_signal_eth.json')
        atomic_write(eth_path, results['ETHUSDT'])

    total = time.time() - t_start
    print(f'[kronos_writer] 완료 {total:.1f}s', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
