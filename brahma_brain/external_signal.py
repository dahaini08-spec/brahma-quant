"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# 外部信号接入，扩展口
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
external_signal.py · 外部研究信号缓存与注入接口 v1.0
====================================================
职责：
  1. 作为 brahma_core.py 的读取接口
     extra_data['research'] = external_signal.get(symbol, direction)
  2. 管理来自 research_bridge 写入的信号缓存（JSON文件，TTL控制）
  3. 汇总多个研究源的 score_delta（上限 MAX_TOTAL_INJECT）

设计原则（STAR.md L0）：
  - 读取纯被动：不做网络请求，只读文件缓存
  - 总注入上限：MAX_TOTAL_INJECT = 8分（硬性截断）
  - TTL = 30分钟，过期返回 {'score': 0, 'reason': 'no_signal'}
  - 任何异常 → score=0，不阻塞主评分流程
  - 同步读取 < 1ms（LRU内存缓存，60秒刷新）

调用方式（brahma_core.py Step 4 之后插入）：
  from brahma_brain.external_signal import get as _ext_get
  extra_data['research'] = _ext_get(symbol, signal_dir)

信号缓存写入方式（由 research_bridge.py 负责）：
  data/research_cache/<SYMBOL>_<DIR>.json

作者：设计院 · 2026-06-17 v1.0
"""

import json
import time
import os
from pathlib import Path
from typing import Optional, Dict, Any

# ── 路径 ──────────────────────────────────────────────────────
_BASE  = Path(__file__).parent.parent  # trading-system/
_CACHE = _BASE / "data" / "research_cache"

# ── 常量（STAR.md §三） ────────────────────────────────────────
MAX_TOTAL_INJECT = 8       # 硬性上限：所有研究源合计最多注入 8 分
TTL_SECONDS      = 1800    # 30 分钟
CACHE_REFRESH_S  = 60      # 内存 LRU 刷新间隔

# ── 内存 LRU 缓存 ──────────────────────────────────────────────
_mem_cache: Dict[str, tuple] = {}  # key -> (data, loaded_at)


def get(symbol: str, direction: str) -> Dict[str, Any]:
    """
    主接口：读取研究信号，供 brahma_core extra_data['research'] 使用

    Returns:
        {
            'score':    int,    # 注入分数（0 = 无信号/过期/CHOP归零）
            'sources':  list,   # 贡献来源列表
            'reason':   str,    # 描述性原因
            'details':  dict,   # 各来源原始数据
        }
    """
    try:
        return _load(symbol.upper(), direction.upper())
    except Exception as e:
        return {'score': 0, 'sources': [], 'reason': f'exception:{str(e)[:60]}', 'details': {}}


def write(symbol: str, direction: str, signal: Dict[str, Any]) -> bool:
    """
    写入研究信号缓存（由 research_bridge.py 调用）
    Returns True on success
    """
    try:
        _CACHE.mkdir(parents=True, exist_ok=True)
        key = f"{symbol.upper()}_{direction.upper()}"
        path = _CACHE / f"{key}.json"

        # 读取现有缓存（合并多个来源）
        existing = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text())
            except Exception:
                existing = {}

        source = signal.get('source', 'unknown')
        existing[source] = {
            'score_delta':  min(signal.get('score_delta', 0), 8),  # 单源上限8
            'confidence':   signal.get('confidence', 0),
            'narrative':    signal.get('narrative', ''),
            'generated_at': signal.get('generated_at', ''),
            'written_at':   time.time(),
        }
        path.write_text(json.dumps(existing, ensure_ascii=False, indent=2))

        # 清除内存缓存，强制下次重新读取
        cache_key = f"{symbol.upper()}_{direction.upper()}"
        _mem_cache.pop(cache_key, None)
        return True
    except Exception:
        return False


def clear(symbol: str, direction: str) -> None:
    """清除指定标的的研究缓存（信号结算后调用）"""
    key = f"{symbol.upper()}_{direction.upper()}"
    path = _CACHE / f"{key}.json"
    if path.exists():
        path.unlink(missing_ok=True)
    _mem_cache.pop(key, None)


def status() -> Dict[str, Any]:
    """查看当前所有有效研究缓存（运维用）"""
    if not _CACHE.exists():
        return {'active': 0, 'files': []}
    files = []
    now = time.time()
    for f in _CACHE.glob("*.json"):
        try:
            d = json.loads(f.read_text())
            total_score = 0
            sources = []
            fresh = True
            for src, v in d.items():
                age = now - v.get('written_at', 0)
                if age > TTL_SECONDS:
                    fresh = False
                    continue
                total_score += v.get('score_delta', 0)
                sources.append(src)
            files.append({
                'file':  f.name,
                'score': min(total_score, MAX_TOTAL_INJECT),
                'sources': sources,
                'fresh': fresh,
            })
        except Exception:
            pass
    return {'active': len([x for x in files if x['fresh']]), 'files': files}


# ── 内部实现 ───────────────────────────────────────────────────

def _load(symbol: str, direction: str) -> Dict[str, Any]:
    key = f"{symbol}_{direction}"
    now = time.time()

    # 内存缓存命中（60秒内不重复读文件）
    if key in _mem_cache:
        data, loaded_at = _mem_cache[key]
        if now - loaded_at < CACHE_REFRESH_S:
            return _aggregate(data, now)

    # 读文件
    path = _CACHE / f"{key}.json"
    if not path.exists():
        return {'score': 0, 'sources': [], 'reason': 'no_cache_file', 'details': {}}

    try:
        data = json.loads(path.read_text())
    except Exception as e:
        return {'score': 0, 'sources': [], 'reason': f'json_parse_error:{e}', 'details': {}}

    _mem_cache[key] = (data, now)
    return _aggregate(data, now)


def _aggregate(data: Dict, now: float) -> Dict[str, Any]:
    """汇总多个研究源，应用 TTL + 总上限"""
    total = 0
    sources = []
    details = {}
    expired_count = 0

    for source, v in data.items():
        written_at = v.get('written_at', 0)
        age = now - written_at
        if age > TTL_SECONDS:
            expired_count += 1
            continue
        delta = int(v.get('score_delta', 0))
        conf  = float(v.get('confidence', 0))
        # 置信度低于 0.5 时折半
        if conf < 0.5:
            delta = delta // 2
        total += delta
        sources.append(source)
        details[source] = {
            'delta':     delta,
            'conf':      conf,
            'narrative': v.get('narrative', ''),
            'age_min':   round(age / 60, 1),
        }

    # 硬性总上限（STAR.md L0）
    total = min(total, MAX_TOTAL_INJECT)
    total = max(total, -MAX_TOTAL_INJECT)  # 负分也有下限

    if not sources and expired_count > 0:
        reason = f'all_expired({expired_count}sources)'
    elif not sources:
        reason = 'no_active_sources'
    else:
        reason = f'ok({len(sources)}sources,+{total}pt)'

    return {
        'score':   total,
        'sources': sources,
        'reason':  reason,
        'details': details,
    }
