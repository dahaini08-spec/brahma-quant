#!/usr/bin/env python3
"""
梵天系统 Step8 宏观数据采集器
[苏摩111 2026-09-12] 根因修复：Step8接入真实CPI/PPI/利率数据，删除AI主观叙事

数据源：
1. BLS API (api.bls.gov) — CPI/PPI实际值
2. 美联储利率 — 硬编码当前目标利率+下次会议日期
3. CME FedWatch — 替代方案：从CPI/PPI趋势推算加息/降息概率

输出：data/macro_real.json
"""

import json, time, urllib.request
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(__file__).parent.parent / 'data'

# 美联储当前利率（2026年9月，需手动更新或从FOMC结果获取）
# 2026年9月FOMC前的市场基准利率
CURRENT_FED_RATE = 5.25  # 当前联邦基金目标利率上限
NEXT_FOMC_DATE = "2026-09-17"

# BLS Series IDs
BLS_SERIES = {
    'cpi_core':    'CUUR0000SA0L1E',   # Core CPI ( excludes food & energy )
    'cpi_all':     'CUUR0000SA0',       # CPI All Items
    'ppi_final':   'WPSFD4111',          # PPI Final Demand
    'cpi_yoy':     'CUUR0000SA0L1E',    # Same series, calc YoY manually
}

def _fetch_bls(series_id: str) -> dict:
    """从BLS API获取最新数据"""
    try:
        url = f"https://api.bls.gov/publicAPI/v2/timeseries/data/{series_id}?latest=true"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        series = data.get('Results', {}).get('series', [])
        if series and series[0].get('data'):
            d = series[0]['data'][0]
            return {
                'value': float(d.get('value', 0)),
                'period': d.get('period', ''),
                'period_name': d.get('periodName', ''),
                'year': d.get('year', ''),
            }
    except Exception as e:
        return {'error': str(e)}
    return {'error': 'no data'}

def _calc_yoy(series_id: str, months: int = 13) -> float:
    """计算同比变化率"""
    try:
        url = f"https://api.bls.gov/publicAPI/v2/timeseries/data/{series_id}?start={_get_start_year()}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        series = data.get('Results', {}).get('series', [])
        if series and series[0].get('data'):
            d = series[0]['data']
            if len(d) >= months:
                latest = float(d[0]['value'])
                yoy = float(d[months-1]['value']) if len(d) >= months else 0
                if yoy > 0:
                    return round((latest - yoy) / yoy * 100, 2)
    except Exception:
        pass
    return 0.0

def _get_start_year() -> str:
    """获取1年前的年份"""
    return str(datetime.now(timezone.utc).year - 1)

def _calc_rate_expectation(cpi_yoy: float, ppi_yoy: float, current_rate: float) -> dict:
    """
    从CPI/PPI同比推算加息/降息预期
    规则（基于美联储历史行为模式）：
    - CPI YoY > 3.5% + PPI YoY > 3% → 加息预期强
    - CPI YoY 2.5-3.5% + PPI YoY 2-3% → 维持高利率
    - CPI YoY 2-2.5% + PPI YoY < 2% → 降息预期
    - CPI YoY < 2% → 强降息预期
    """
    inflation_pressure = cpi_yoy + (ppi_yoy * 0.3)  # CPI权重70%，PPI权重30%
    
    if inflation_pressure > 4.5:
        bias = 'HAWKISH'
        action = 'HIKE'
        probability = min(80, (inflation_pressure - 4.5) * 30)
        note = f'通胀压力强(CPI={cpi_yoy:.1f}%/PPI={ppi_yoy:.1f}%)→加息预期{probability:.0f}%'
    elif inflation_pressure > 3.5:
        bias = 'HAWKISH'
        action = 'HOLD'
        probability = 60
        note = f'通胀偏高(CPI={cpi_yoy:.1f}%/PPI={ppi_yoy:.1f}%)→维持高利率概率{probability:.0f}%'
    elif inflation_pressure > 2.5:
        bias = 'NEUTRAL'
        action = 'HOLD'
        probability = 50
        note = f'通胀温和(CPI={cpi_yoy:.1f}%/PPI={ppi_yoy:.1f}%)→利率维持概率{probability:.0f}%'
    elif inflation_pressure > 2.0:
        bias = 'DOVISH'
        action = 'CUT_25'
        probability = 60
        note = f'通胀回落(CPI={cpi_yoy:.1f}%/PPI={ppi_yoy:.1f}%)→降息25bp概率{probability:.0f}%'
    else:
        bias = 'DOVISH'
        action = 'CUT_50'
        probability = 70
        note = f'通胀达标(CPI={cpi_yoy:.1f}%/PPI={ppi_yoy:.1f}%)→降息50bp概率{probability:.0f}%'
    
    return {
        'bias': bias,
        'action': action,
        'probability': round(probability, 0),
        'note': note,
        'inflation_pressure': round(inflation_pressure, 2),
    }

def fetch_macro_real() -> dict:
    """采集真实宏观数据"""
    now = datetime.now(timezone.utc)
    
    # 1. 获取CPI数据
    cpi_core = _fetch_bls(BLS_SERIES['cpi_core'])
    cpi_all = _fetch_bls(BLS_SERIES['cpi_all'])
    ppi_final = _fetch_bls(BLS_SERIES['ppi_final'])
    
    # 2. 计算同比
    cpi_yoy = _calc_yoy(BLS_SERIES['cpi_core'], 13)
    ppi_yoy = _calc_yoy(BLS_SERIES['ppi_final'], 13)
    
    # 3. 推算利率预期
    rate_expectation = _calc_rate_expectation(cpi_yoy, ppi_yoy, CURRENT_FED_RATE)
    
    # 4. 宏观日历（下一次重要事件）
    macro_calendar = {
        'next_fomc': NEXT_FOMC_DATE,
        'days_to_fomc': (_parse_date(NEXT_FOMC_DATE) - now).days if _parse_date(NEXT_FOMC_DATE) else 0,
    }
    
    return {
        'data_source': 'BLS API + 推算模型',
        'data_time': now.strftime('%Y-%m-%d %H:%M UTC'),
        'cpi_core': cpi_core,
        'cpi_all': cpi_all,
        'ppi_final': ppi_final,
        'cpi_yoy': cpi_yoy,
        'ppi_yoy': ppi_yoy,
        'fed_rate': CURRENT_FED_RATE,
        'rate_expectation': rate_expectation,
        'macro_calendar': macro_calendar,
        'fear_greed': None,  # 从现有macro_state.json读取
    }

def _parse_date(date_str: str):
    """解析YYYY-MM-DD格式"""
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    except Exception:
        return None

def update_macro_real():
    """采集并写入data/macro_real.json"""
    data = fetch_macro_real()
    
    # 读取现有fear_greed
    macro_state_path = DATA_DIR / 'macro_state.json'
    if macro_state_path.exists():
        try:
            ms = json.loads(macro_state_path.read_text())
            data['fear_greed'] = ms.get('fear_greed', 50)
        except Exception:
            pass
    
    # 写入
    output_path = DATA_DIR / 'macro_real.json'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    print(f"✅ 宏观数据已更新: {output_path}")
    print(f"   数据时间: {data['data_time']}")
    print(f"   CPI Core YoY: {data['cpi_yoy']:.2f}%")
    print(f"   PPI Final YoY: {data['ppi_yoy']:.2f}%")
    print(f"   Fed Rate: {data['fed_rate']:.2f}%")
    print(f"   利率预期: {data['rate_expectation']['bias']} → {data['rate_expectation']['action']} ({data['rate_expectation']['probability']:.0f}%)")
    print(f"   {data['rate_expectation']['note']}")
    
    return data

if __name__ == '__main__':
    update_macro_real()
