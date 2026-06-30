#!/usr/bin/env python3
"""
signal_utils.py · 设计院标准信号读取层 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
2026-06-02 设计院全局Bug修复 — 统一信号读取入口

所有需要读取信号的脚本必须使用这里的函数，
禁止直接open('data/live_signal_log.jsonl')裸读。

保证:
  1. 只返回指定时间窗口内的信号
  2. 可选过滤 valid=False 的无效信号
  3. 可选过滤 settled=True 的已结算信号
  4. 可选过滤 _data_quality 污染信号
  5. 可选最低评分门槛
"""
import json, os
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(__file__).parent.parent

# ── 全局常量 ──────────────────────────────────────────────
LIVE_SIGNAL_LOG  = BASE / 'data' / 'live_signal_log.jsonl'
SIGNAL_QUEUE     = BASE / 'data' / 'signal_queue.jsonl'
SIGNAL_QUEUE_TTL = 7   # signal_queue 保留天数
EXPIRE_HOURS     = 48  # 超过此时间的未settled信号视为过期


def load_signals(
    hours: float = 8.0,
    min_score: float = 0.0,
    valid_only: bool = False,
    unsettled_only: bool = False,
    exclude_data_quality: bool = True,
    source: str = 'live_signal_log',
) -> list:
    """
    标准信号读取函数。

    参数:
      hours           : 只返回最近 N 小时内的信号（默认8H）
      min_score       : 最低评分过滤
      valid_only      : True → 只返回 valid=True 的信号
      unsettled_only  : True → 只返回 settled=False 的信号
      exclude_data_quality: True → 排除 _data_quality 非空的污染信号
      source          : 'live_signal_log' 或 'signal_queue'

    返回: list[dict]，按 ts 降序排列
    """
    path = LIVE_SIGNAL_LOG if source == 'live_signal_log' else SIGNAL_QUEUE
    if not path.exists():
        return []

    try:
        raw = [json.loads(l) for l in open(path) if l.strip()]
    except Exception:
        return []

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)

    result = []
    for rec in raw:
        # ── 时间窗口过滤（核心修复，之前是死代码）────────────
        try:
            ts = datetime.fromisoformat(rec.get('ts', '').replace('Z', '+00:00'))
            if ts < cutoff:
                continue
        except Exception:
            continue

        # ── [Fix-TTL 2026-06-11] expires_at过滤：已过TTL的信号直接跳过 ──
        expires_at = rec.get('expires_at', '')
        if expires_at:
            try:
                exp_ts = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                if now > exp_ts:
                    continue  # 已超TTL，僵尸信号，跳过
            except Exception:
                pass

        # ── 评分过滤 ─────────────────────────────────────────
        score = float(rec.get('score', 0) or 0)
        if score < min_score:
            continue

        # ── valid过滤 ────────────────────────────────────────
        if valid_only and not rec.get('valid', False):
            continue

        # ── settled过滤 ──────────────────────────────────────
        if unsettled_only and rec.get('settled', False):
            continue

        # ── 数据污染过滤 ─────────────────────────────────────
        if exclude_data_quality and rec.get('_data_quality'):
            continue

        result.append(rec)

    return sorted(result, key=lambda x: x.get('ts', ''), reverse=True)


def load_broadcastable_signals(min_score: float = 145.0, hours: float = 8.0) -> list:  # SSOT broadcast_min=145.0
    """
    专为广播脚本设计：只返回当前可播报的有效信号。
    条件: valid=True + unsettled + 时间窗内 + 无数据污染 + 评分达标
    """
    return load_signals(
        hours=hours,
        min_score=min_score,
        valid_only=True,
        unsettled_only=True,
        exclude_data_quality=True,
    )


def load_all_for_stats(exclude_legacy: bool = True) -> list:
    """
    统计分析专用：读取所有已结算信号。
    exclude_legacy=True 排除历史污染标记的信号。
    """
    path = LIVE_SIGNAL_LOG
    if not path.exists():
        return []
    try:
        raw = [json.loads(l) for l in open(path) if l.strip()]
    except Exception:
        return []

    result = []
    for rec in raw:
        if not rec.get('settled'):
            continue
        if exclude_legacy and rec.get('_data_quality'):
            continue
        result.append(rec)
    return result


def clean_signal_queue(ttl_days: int = SIGNAL_QUEUE_TTL) -> int:
    """清理 signal_queue 中超过 ttl_days 天的旧条目。返回删除条数。"""
    path = SIGNAL_QUEUE
    if not path.exists():
        return 0
    try:
        raw = [json.loads(l) for l in open(path) if l.strip()]
    except Exception:
        return 0

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=ttl_days)
    keep = []
    removed = 0
    for rec in raw:
        try:
            ts = datetime.fromisoformat(rec.get('ts', '').replace('Z', '+00:00'))
            if ts >= cutoff:
                keep.append(rec)
            else:
                removed += 1
        except Exception:
            keep.append(rec)

    if removed > 0:
        tmp = str(path) + '.tmp'
        with open(tmp, 'w') as f:
            for rec in keep:
                f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        os.replace(tmp, str(path))

    return removed


def get_signal_by_id(signal_id: str) -> dict | None:
    """按 signal_id 查找信号。"""
    path = LIVE_SIGNAL_LOG
    if not path.exists():
        return None
    for line in open(path):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            if rec.get('signal_id') == signal_id:
                return rec
        except Exception:
            pass
    return None


if __name__ == '__main__':
    # 自检
    sigs = load_broadcastable_signals(min_score=145)
    print(f'当前可播报信号: {len(sigs)}条')
    for s in sigs:
        sym = s.get('symbol','?'); sc = s.get('score',0); ts2 = s.get('ts','')[:16]; vld = s.get('valid')
        print(f'  {sym} {sc} {ts2} valid={vld}')
    
    removed = clean_signal_queue()
    if removed:
        print(f'清理signal_queue: {removed}条过期数据')
    else:
        print('signal_queue: 无需清理')
