"""
extreme_event_db.py  —  A2 极端事件库
梵天设计院封印 2026-08-25

功能:
  build_extreme_events()         — 扫描1D K线，识别单日涨跌幅 >8% 的极端事件
  match_current_similarity(sym)  — 当前状态与历史极端事件相似度匹配
  get_extreme_risk_note(sym)     — 供 analyze() 调用的风险注释接口
"""

import ast
import gzip
import json
import math
import os
import sys
from datetime import datetime, timezone

# ── 路径常量 ──────────────────────────────────────────────────────────────────
_DATA_DIR        = os.path.join(os.path.dirname(__file__), '..', 'data')
_HIST_PATH       = os.path.join(_DATA_DIR, 'historical', 'BTCUSDT_1d.jsonl.gz')
_EVENTS_PATH     = os.path.join(_DATA_DIR, 'extreme_events.jsonl')

# ── 参数 ──────────────────────────────────────────────────────────────────────
EXTREME_THRESHOLD_PCT = 8.0   # 绝对值 > 8% 为极端事件
SIMILARITY_WARNING    = 60.0  # 相似度超过此阈值发出警告
TOP_N                 = 3     # 返回最相似的 top3


# ══════════════════════════════════════════════════════════════════════════════
# 内部工具
# ══════════════════════════════════════════════════════════════════════════════

def _rsi_wilder(closes: list, period: int = 14) -> float:
    """Wilder 平滑 RSI，输入至少 period+1 个收盘价，不足则返回 50.0"""
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    return round(100.0 - 100.0 / (1 + ag / al), 2)


def _load_klines_gz(path: str) -> list:
    """读取 .jsonl.gz 历史K线，返回按 ts 升序的 dict 列表"""
    rows = []
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        rows.sort(key=lambda x: x['ts'])
    except Exception as e:
        print(f"[extreme_event_db] 读取K线失败: {e}", file=sys.stderr)
    return rows


def _ts_to_date(ts_ms: int) -> str:
    """毫秒时间戳 → YYYY-MM-DD (UTC)"""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d')


def _euclidean(a1: float, a2: float, b1: float, b2: float) -> float:
    """两个二维向量的欧式距离"""
    return math.sqrt((a1 - b1) ** 2 + (a2 - b2) ** 2)


def _similarity_score(dist: float, max_dist: float) -> float:
    """将欧式距离映射到 0-100 相似度分（距离越小相似度越高）"""
    if max_dist <= 0:
        return 100.0
    return round(max(0.0, 100.0 * (1.0 - dist / max_dist)), 1)


# ══════════════════════════════════════════════════════════════════════════════
# 公共 API
# ══════════════════════════════════════════════════════════════════════════════

def build_extreme_events(symbol: str = 'BTCUSDT') -> list:
    """
    扫描1D K线，识别单日涨跌幅绝对值 > 8% 的极端事件。

    每个 event 字段:
      ts             : K线时间戳 (ms)
      date           : YYYY-MM-DD
      symbol         : 交易对
      change_pct     : 当日涨跌幅 (%)
      direction      : 'UP' / 'DOWN'
      pre_3d_rsi     : 事前3天（含当天前一天）的 RSI(14)
      pre_3d_change  : 事前3天累计涨跌幅 (%)

    结果写入 extreme_events.jsonl，并返回事件列表。
    """
    klines = _load_klines_gz(_HIST_PATH)
    if not klines:
        print("[extreme_event_db] 无可用K线数据", file=sys.stderr)
        return []

    closes = [k['c'] for k in klines]
    events = []

    # 需要至少 14+3 = 17 根前置K线 + 本根
    for i in range(17, len(klines)):
        k = klines[i]
        prev_close = klines[i - 1]['c']
        if prev_close == 0:
            continue
        change_pct = (k['c'] - prev_close) / prev_close * 100.0

        if abs(change_pct) <= EXTREME_THRESHOLD_PCT:
            continue

        # 事前3天 (i-3 ~ i-1 含) 的收盘价
        pre_closes = closes[: i]          # 不含当天
        rsi_window = pre_closes[-(14 + 3):]  # 给 RSI 足够窗口：最近17根
        pre_3d_rsi = _rsi_wilder(rsi_window[-17:], period=14)

        # 3日累计涨跌：从 klines[i-3] close → klines[i-1] close
        base_close_3d = klines[i - 3]['c']
        end_close_3d  = klines[i - 1]['c']
        pre_3d_change = (end_close_3d - base_close_3d) / base_close_3d * 100.0 if base_close_3d else 0.0

        event = {
            'ts'           : k['ts'],
            'date'         : _ts_to_date(k['ts']),
            'symbol'       : symbol,
            'change_pct'   : round(change_pct, 2),
            'direction'    : 'UP' if change_pct > 0 else 'DOWN',
            'pre_3d_rsi'   : round(pre_3d_rsi, 2),
            'pre_3d_change': round(pre_3d_change, 2),
        }
        events.append(event)

    # 写入 JSONL
    try:
        os.makedirs(os.path.dirname(_EVENTS_PATH), exist_ok=True)
        with open(_EVENTS_PATH, 'w', encoding='utf-8') as f:
            for ev in events:
                f.write(json.dumps(ev, ensure_ascii=False) + '\n')
        print(f"[extreme_event_db] 极端事件库已构建: {len(events)} 条 → {_EVENTS_PATH}")
    except Exception as e:
        print(f"[extreme_event_db] 写入事件库失败: {e}", file=sys.stderr)

    return events


def _load_events() -> list:
    """从磁盘加载已构建的极端事件库"""
    events = []
    try:
        with open(_EVENTS_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[extreme_event_db] 加载事件库失败: {e}", file=sys.stderr)
    return events


def match_current_similarity(symbol: str = 'BTCUSDT') -> dict:
    """
    计算当前市场状态与历史极端事件的相似度。

    返回:
      {
        'current_rsi'     : float,
        'current_3d_change': float,
        'top3'            : [{'event': {...}, 'dist': float, 'similarity': float}, ...],
        'max_similarity'  : float,
        'warning'         : str  (空字符串或警告文本),
      }
    """
    # 1. 加载事件库（不存在则先构建）
    events = _load_events()
    if not events:
        events = build_extreme_events(symbol)

    if not events:
        return {'current_rsi': 50.0, 'current_3d_change': 0.0,
                'top3': [], 'max_similarity': 0.0, 'warning': ''}

    # 2. 计算当前状态（从1D K线取最新数据）
    klines = _load_klines_gz(_HIST_PATH)
    if len(klines) < 17:
        return {'current_rsi': 50.0, 'current_3d_change': 0.0,
                'top3': [], 'max_similarity': 0.0, 'warning': ''}

    closes = [k['c'] for k in klines]
    cur_rsi = _rsi_wilder(closes[-17:], period=14)

    base_3d  = klines[-4]['c']
    end_3d   = klines[-1]['c']
    cur_3d_change = (end_3d - base_3d) / base_3d * 100.0 if base_3d else 0.0

    # 3. 欧式距离，RSI 归一化到 [0,100]，3d_change 通常在 [-30,30]
    #    为使两个维度量纲对齐：RSI/100 × 100 = RSI ; 3d_change 保持原值
    #    计算所有事件的距离
    scored = []
    for ev in events:
        dist = _euclidean(cur_rsi, cur_3d_change,
                          ev['pre_3d_rsi'], ev['pre_3d_change'])
        scored.append({'event': ev, 'dist': dist})

    scored.sort(key=lambda x: x['dist'])

    # 用距离最大值做归一化参考（取前100条的最大距离）
    ref_dists = [s['dist'] for s in scored[:100]]
    max_dist  = max(ref_dists) if ref_dists else 1.0

    top3 = []
    for s in scored[:TOP_N]:
        sim = _similarity_score(s['dist'], max_dist)
        top3.append({
            'event'     : s['event'],
            'dist'      : round(s['dist'], 3),
            'similarity': sim,
        })

    max_sim = top3[0]['similarity'] if top3 else 0.0

    # 4. 生成警告
    warning = ''
    if max_sim > SIMILARITY_WARNING and top3:
        best_ev = top3[0]['event']
        warning = (
            f"⚠️ 当前市场与 {best_ev['date']} 极端事件 {max_sim}% 相似"
            f"（{best_ev['direction']}，{best_ev['change_pct']:+.1f}%）"
        )

    return {
        'current_rsi'      : round(cur_rsi, 2),
        'current_3d_change': round(cur_3d_change, 2),
        'top3'             : top3,
        'max_similarity'   : max_sim,
        'warning'          : warning,
    }


def get_extreme_risk_note(symbol: str = 'BTCUSDT') -> str:
    """
    供 analyze() 调用的风险注释接口。
    - 相似度 > 60 → 返回警告字符串
    - 否则返回空字符串
    """
    try:
        result = match_current_similarity(symbol)
        return result.get('warning', '')
    except Exception as e:
        print(f"[extreme_event_db] get_extreme_risk_note 失败: {e}", file=sys.stderr)
        return ''


# ══════════════════════════════════════════════════════════════════════════════
# 冒烟测试
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    # 语法自检
    with open(__file__, 'r', encoding='utf-8') as _f:
        ast.parse(_f.read())

    print("=== A2 极端事件库 冒烟测试 ===")

    # Step 1: 构建极端事件库
    print("\n[1] build_extreme_events(BTCUSDT)")
    try:
        evs = build_extreme_events('BTCUSDT')
        assert len(evs) > 0, "极端事件列表不能为空"
        print(f"    事件数: {len(evs)}")
        print(f"    样本: {evs[0]}")
        print(f"    最近: {evs[-1]}")
    except Exception as e:
        print(f"    ❌ 失败: {e}")
        sys.exit(1)

    # Step 2: 相似度匹配
    print("\n[2] match_current_similarity(BTCUSDT)")
    try:
        res = match_current_similarity('BTCUSDT')
        print(f"    当前RSI:        {res['current_rsi']}")
        print(f"    当前3日涨跌:    {res['current_3d_change']:.2f}%")
        print(f"    最高相似度:     {res['max_similarity']}%")
        if res['warning']:
            print(f"    警告: {res['warning']}")
        else:
            print("    无极端事件警告")
        print(f"    Top3:")
        for t in res['top3']:
            ev = t['event']
            print(f"      {ev['date']} {ev['direction']} {ev['change_pct']:+.1f}%  "
                  f"pre_rsi={ev['pre_3d_rsi']}  pre_3d={ev['pre_3d_change']:+.1f}%  "
                  f"相似度={t['similarity']}%")
    except Exception as e:
        print(f"    ❌ 失败: {e}")
        sys.exit(1)

    # Step 3: 风险注释
    print("\n[3] get_extreme_risk_note(BTCUSDT)")
    try:
        note = get_extreme_risk_note('BTCUSDT')
        print(f"    note='{note}'")
    except Exception as e:
        print(f"    ❌ 失败: {e}")
        sys.exit(1)

    print("\nA2完成 ✅")
