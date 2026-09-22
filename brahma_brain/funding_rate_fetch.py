"""
funding_rate_fetch.py — 资金费率按需拉取（非常驻进程）
[2026-09-22 苏摩111封印] 分析时拉取一次，写入缓存，VETERAN VR07消费

Binance API: /fapi/v1/fundingRate (免费, 无需Deribit)
资金费率8h结算一次，按需拉取即可，不需要高频采集器
"""
import json, time, requests
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / 'data'

BINANCE_API = 'https://fapi.binance.com'


def fetch_funding_rate(symbol: str = 'BTCUSDT') -> dict:
    """
    分析时按需拉取资金费率，写入缓存文件。
    返回:
        {
            'symbol': str,
            'current_rate': float,        # 当前费率
            'next_funding_time': int,      # 下次结算时间
            'rate_8h_avg': float,         # 8h平均费率
            'consecutive_positive_h': int, # 连续正费率小时数
            'consecutive_negative_h': int, # 连续负费率小时数
            'rate_extreme_flag': bool,    # 是否极值(>0.1%或<-0.05%)
            'ts': float,                  # 拉取时间
        }
    """
    try:
        # 当前资金费率
        resp = requests.get(
            f'{BINANCE_API}/fapi/v1/premiumIndex',
            params={'symbol': symbol},
            timeout=10
        )
        data = resp.json()
        current_rate = float(data.get('lastFundingRate', 0))
        next_funding_time = int(data.get('nextFundingTime', 0))
        mark_price = float(data.get('markPrice', 0))

        # 历史资金费率（最近8h=1条8h记录）
        resp2 = requests.get(
            f'{BINANCE_API}/fapi/v1/fundingRate',
            params={'symbol': symbol, 'limit': 3},
            timeout=10
        )
        history = resp2.json()
        rates = [float(h.get('fundingRate', 0)) for h in history]
        rate_8h_avg = sum(rates) / len(rates) if rates else 0

        # 连续正/负费率小时数（每条=8h）
        consecutive_positive_h = 0
        consecutive_negative_h = 0
        for r in reversed(rates):
            if r > 0:
                if consecutive_negative_h == 0:
                    consecutive_positive_h += 8
                else:
                    break
            elif r < 0:
                if consecutive_positive_h == 0:
                    consecutive_negative_h += 8
                else:
                    break

        # 极值检测
        rate_extreme_flag = current_rate > 0.001 or current_rate < -0.0005

        result = {
            'symbol': symbol,
            'current_rate': current_rate,
            'next_funding_time': next_funding_time,
            'mark_price': mark_price,
            'rate_8h_avg': rate_8h_avg,
            'consecutive_positive_h': consecutive_positive_h,
            'consecutive_negative_h': consecutive_negative_h,
            'rate_extreme_flag': rate_extreme_flag,
            'ts': time.time(),
        }

        # 写入缓存
        cache_file = DATA_DIR / f'funding_rate_{symbol.lower()}.json'
        cache_file.parent.mkdir(exist_ok=True)
        json.dump(result, open(cache_file, 'w'), indent=2)

        return result

    except Exception as e:
        import sys
        sys.stderr.write(f'[funding_rate_fetch] ERROR: {e}\n')
        return {'symbol': symbol, 'current_rate': 0, 'rate_extreme_flag': False, 'ts': 0}


if __name__ == '__main__':
    for sym in ['BTCUSDT', 'ETHUSDT']:
        r = fetch_funding_rate(sym)
        print(f'{sym}: rate={r["current_rate"]:.6f} extreme={r["rate_extreme_flag"]} consec_pos={r["consecutive_positive_h"]}h')
