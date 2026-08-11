#!/usr/bin/env python3
"""
brahma_mem_compressor.py — 梵天记忆压缩器
对标: claude-mem (会话记忆压缩注入)
改动量: 最小 — 仅新增独立脚本，不修改任何现有文件

═══════════════════════════════════════════════════════════════
设计院深度推理 · 2026-07-10 苏摩111批准

问题根因：
  · live_signal_log.jsonl 60行全量读取（每次分析注入全部历史）
  · brahma_state.json 27KB，含大量无关字段
  · regime_state.json 42KB，大部分标的当前分析不需要
  · 合计~191KB/次注入，大量token浪费在陈旧数据上

解决方案（claude-mem核心思想）：
  1. 压缩历史信号 → 提炼"信号摘要向量"（不是全量行）
  2. 为每次分析提供精准上下文切片（只取相关标的+时间窗口）
  3. 自动蒸馏MEMORY.md（周期性将日志精华写入长期记忆）

最小改动原则：
  · 不修改brahma_core / brahma_analysis_runner
  · 输出格式与现有接口兼容
  · 可选调用 —— 现有系统不依赖本模块也能正常运行
═══════════════════════════════════════════════════════════════
"""
import sys, os, json, time
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

BASE = Path(__file__).parent.parent

# ════════════════════════════════════════════════════════════════
# 核心1: 信号上下文压缩（claude-mem思想）
# ════════════════════════════════════════════════════════════════

def compress_signal_context(symbol: str, max_tokens: int = 500) -> dict:
    """
    为单个标的提取压缩上下文——只注入"真正需要的信息"

    claude-mem的核心逻辑：
      · 不是把所有历史都注入，而是把历史"蒸馏"成关键事实
      · 按相关性排序，只取topK
      · 输出结构化摘要而非原始日志

    参数:
      symbol    — 目标标的
      max_tokens — 上下文预算（控制注入量）

    返回: 压缩后的上下文字典，可直接注入分析器
    """
    ctx = {
        'symbol':          symbol,
        'generated_at':    datetime.now(timezone.utc).isoformat(),
        'context_budget':  max_tokens,

        # 最近信号摘要（不注入全量，只取相关）
        'recent_signals':   _extract_recent_signals(symbol, n=3),

        # 体制状态（只取必要字段）
        'regime_summary':   _extract_regime_summary(symbol),

        # OI状态摘要（压缩版）
        'oi_summary':       _extract_oi_summary(symbol),

        # 持仓状态（关键决策因子）
        'position_summary': _extract_position_summary(symbol),
    }

    # 计算实际token估算
    ctx_str = json.dumps(ctx, ensure_ascii=False)
    estimated_tokens = len(ctx_str) // 4
    ctx['estimated_tokens'] = estimated_tokens
    ctx['compression_ratio'] = round(191 * 1024 / max(len(ctx_str), 1), 1)

    return ctx


def _extract_recent_signals(symbol: str, n: int = 3) -> list:
    """提取最近N条相关信号（压缩为关键字段）"""
    try:
        log_path = BASE / 'data' / 'live_signal_log.jsonl'
        all_lines = open(log_path).readlines()
        # 只过滤该标的，取最近N条
        sym_sigs = []
        for line in reversed(all_lines):
            try:
                s = json.loads(line)
                if s.get('symbol') == symbol:
                    # 只保留决策关键字段（压缩70%+）
                    sym_sigs.append({
                        'ts':      s.get('ts', 0),
                        'dir':     s.get('direction', ''),
                        'score':   s.get('score', 0),
                        'valid':   s.get('valid', False),
                        'regime':  s.get('regime', ''),
                        'rr1':     s.get('rr1', 0),
                        'action':  s.get('action', ''),
                        'age_h':   round((time.time() - float(s.get('ts', 0))) / 3600, 1),
                    })
                    if len(sym_sigs) >= n:
                        break
            except:
                continue
        return sym_sigs
    except:
        return []


def _extract_regime_summary(symbol: str) -> dict:
    """提取体制摘要（只取关键字段，压缩80%）"""
    try:
        rs_path = BASE / 'data' / 'regime_state.json'
        rs = json.loads(rs_path.read_text())
        sym_state = rs.get(symbol, {})
        if isinstance(sym_state, dict):
            return {
                'confirmed':      sym_state.get('confirmed', 'UNKNOWN'),
                'switch_count':   sym_state.get('switch_count_24h', 0),
                'confirmed_at':   sym_state.get('confirmed_at', 0),
                'age_h':          round((time.time() - float(sym_state.get('confirmed_at', time.time()))) / 3600, 1),
            }
    except:
        pass
    return {'confirmed': 'UNKNOWN'}


def _extract_oi_summary(symbol: str) -> dict:
    """提取OI摘要（只保留关键指标）"""
    try:
        oi_path = BASE / 'data' / 'oi_candidates.json'
        oi_data = json.loads(oi_path.read_text())
        cands = oi_data.get('candidates', {})
        if symbol in cands:
            c = cands[symbol]
            return {
                'score':    c.get('oi_score', 0),
                'mode':     c.get('mode', ''),
                'chg_24h':  c.get('chg_24h', 0),
                'chg_7d':   c.get('chg_7d', 0),
                'whale_l':  c.get('whale_l', 50),
                'fr':       c.get('fr', 0),
                'action':   c.get('action', ''),
                'direction': c.get('direction_bias', ''),
            }
    except:
        pass
    return {}


def _extract_position_summary(symbol: str) -> dict:
    """提取持仓摘要"""
    try:
        pos_path = BASE / 'data' / 'wuqu_positions.json'
        if pos_path.exists():
            pos = json.loads(pos_path.read_text())
            if isinstance(pos, dict) and symbol in pos:
                p = pos[symbol]
                return {
                    'has_position': True,
                    'direction':    p.get('direction', ''),
                    'entry_price':  p.get('entry_price', 0),
                    'sl_price':     p.get('sl_price', 0),
                    'tp_price':     p.get('tp_price', 0),
                    'opened_iso':   p.get('opened_iso', ''),
                }
    except:
        pass
    return {'has_position': False}


# ════════════════════════════════════════════════════════════════
# 核心2: 全局状态蒸馏（批量压缩，供分析器使用）
# ════════════════════════════════════════════════════════════════

def get_global_context_snapshot(symbols: list = None) -> dict:
    """
    全局状态快照（蒸馏版）

    vs 原始注入:
      原始: brahma_state(27KB) + regime(42KB) + oi(42KB) = 111KB
      蒸馏: 只取活跃标的关键字段 ≈ 3-5KB (压缩95%+)

    返回: 可直接注入分析器的精简上下文
    """
    now = datetime.now(timezone.utc)

    # 默认只关注主力标的
    if symbols is None:
        symbols = ['BTCUSDT', 'ETHUSDT']

    snapshot = {
        'generated_at': now.isoformat(),
        'symbols':      {},
        'market_state': _get_market_state_summary(),
        'active_positions': _get_active_positions_summary(),
        'recent_executions': _get_recent_executions(n=5),
    }

    for sym in symbols:
        snapshot['symbols'][sym] = compress_signal_context(sym, max_tokens=200)

    # 统计压缩效果
    raw_size = 191 * 1024  # 原始注入估算
    compressed = len(json.dumps(snapshot, ensure_ascii=False))
    snapshot['_meta'] = {
        'raw_bytes':        raw_size,
        'compressed_bytes': compressed,
        'compression_pct':  round((1 - compressed/raw_size)*100, 1),
        'tokens_saved':     (raw_size - compressed) // 4,
    }

    return snapshot


def _get_market_state_summary() -> dict:
    """市场状态摘要（3行搞定全局）"""
    try:
        rs = json.loads((BASE / 'data' / 'regime_state.json').read_text())
        btc_r = rs.get('BTCUSDT', {})
        eth_r = rs.get('ETHUSDT', {})
        return {
            'BTC_regime': btc_r.get('confirmed', '?') if isinstance(btc_r, dict) else '?',
            'ETH_regime': eth_r.get('confirmed', '?') if isinstance(eth_r, dict) else '?',
            'timestamp':  datetime.now(timezone.utc).strftime('%H:%M UTC'),
        }
    except:
        return {}


def _get_active_positions_summary() -> list:
    """当前持仓摘要"""
    try:
        pos_path = BASE / 'data' / 'wuqu_positions.json'
        if pos_path.exists():
            pos = json.loads(pos_path.read_text())
            if isinstance(pos, dict):
                return [
                    {'symbol': sym, 'dir': p.get('direction'),
                     'entry': p.get('entry_price'), 'sl': p.get('sl_price')}
                    for sym, p in pos.items()
                ]
    except:
        pass
    return []


def _get_recent_executions(n: int = 5) -> list:
    """最近N次执行记录（压缩版）"""
    try:
        log = BASE / 'data' / 'auto_executor_log.jsonl'
        if log.exists():
            lines = open(log).readlines()[-n:]
            return [
                {'sym': json.loads(l).get('symbol'),
                 'dir': json.loads(l).get('direction'),
                 'ts':  json.loads(l).get('ts', '')[:10]}
                for l in lines if l.strip()
            ]
    except:
        pass
    return []


# ════════════════════════════════════════════════════════════════
# 核心3: 自动MEMORY蒸馏（周期性精华提取）
# ════════════════════════════════════════════════════════════════

def auto_distill_to_memory(dry_run: bool = True) -> dict:
    """
    自动将日志精华蒸馏到长期记忆
    claude-mem的精髓：不是存原始日志，而是存"已学习的事实"

    参数:
      dry_run — True时只分析不写入（安全模式）

    返回: 待写入MEMORY.md的内容摘要
    """
    now = datetime.now(timezone.utc)
    insights = []

    # 分析最近信号质量
    try:
        sigs = [json.loads(l) for l in open(BASE/'data'/'live_signal_log.jsonl') if l.strip()]
        recent = [s for s in sigs if (time.time()-float(s.get('ts',0)))<24*3600]

        if recent:
            from collections import Counter
            regimes = Counter(s.get('regime','?') for s in recent)
            dirs    = Counter(s.get('direction','?') for s in recent)
            valid_n = sum(1 for s in recent if s.get('valid', False))
            avg_score = sum(float(s.get('score',0)) for s in recent)/len(recent)

            insights.append({
                'category': '信号质量（今日）',
                'fact':     f'总信号{len(recent)}条 | valid={valid_n}条({valid_n/len(recent)*100:.0f}%) | 均分={avg_score:.0f} | 体制={dict(regimes)} | 方向={dict(dirs)}',
                'ts':       now.isoformat(),
            })
    except:
        pass

    # 分析执行质量
    try:
        execs = [json.loads(l) for l in open(BASE/'data'/'auto_executor_log.jsonl') if l.strip()]
        if execs:
            latest = execs[-1]
            insights.append({
                'category': '最近执行',
                'fact':     f'{latest.get("symbol")} {latest.get("direction")} score={latest.get("score")} at {latest.get("ts","")[:10]}',
                'ts':       now.isoformat(),
            })
    except:
        pass

    result = {
        'dry_run':  dry_run,
        'insights': insights,
        'generated': now.isoformat(),
    }

    if not dry_run and insights:
        # 追加到MEMORY.md（只写摘要，不写原始数据）
        memory_path = BASE.parent / 'MEMORY.md'
        entry = f'\n## 梵天自动蒸馏 {now.strftime("%Y-%m-%d %H:%M UTC")}\n'
        for ins in insights:
            entry += f'- [{ins["category"]}] {ins["fact"]}\n'
        try:
            with open(memory_path, 'a') as f:
                f.write(entry)
            result['written'] = True
        except Exception as e:
            result['error'] = str(e)

    return result


# ════════════════════════════════════════════════════════════════
# 主入口 + 效果展示
# ════════════════════════════════════════════════════════════════

def main():
    print('=' * 55)
    print('🧠 brahma_mem_compressor — 梵天记忆压缩器')
    print('   对标: claude-mem (会话记忆压缩注入)')
    print('=' * 55)

    # 演示：BTC上下文压缩
    print('\n[1] BTCUSDT 上下文压缩:')
    ctx = compress_signal_context('BTCUSDT', max_tokens=500)
    raw_est = 191 * 1024
    compressed = len(json.dumps(ctx, ensure_ascii=False))
    print(f'    原始注入估算: {raw_est//1024}KB')
    print(f'    压缩后:       {compressed//1024}KB ({compressed} bytes)')
    print(f'    压缩率:       {(1-compressed/raw_est)*100:.0f}%')
    print(f'    节省tokens:   ~{(raw_est-compressed)//4:,}')
    print(f'    体制: {ctx["regime_summary"].get("confirmed", "?")}')
    print(f'    近期信号: {len(ctx["recent_signals"])}条')

    # 演示：全局快照
    print('\n[2] 全局状态蒸馏快照:')
    snap = get_global_context_snapshot(['BTCUSDT', 'ETHUSDT', 'SLXUSDT'])
    meta = snap.get('_meta', {})
    print(f'    原始: {meta.get("raw_bytes",0)//1024}KB → 压缩: {meta.get("compressed_bytes",0)//1024}KB')
    print(f'    压缩率: {meta.get("compression_pct",0)}%  节省tokens: {meta.get("tokens_saved",0):,}')
    print(f'    活跃持仓: {len(snap.get("active_positions",[]))}个')
    market = snap.get('market_state', {})
    print(f'    BTC体制: {market.get("BTC_regime","?")} | ETH体制: {market.get("ETH_regime","?")}')

    # 演示：记忆蒸馏（dry run）
    print('\n[3] 自动记忆蒸馏 (dry_run=True):')
    distill = auto_distill_to_memory(dry_run=True)
    for ins in distill.get('insights', []):
        print(f'    [{ins["category"]}] {ins["fact"][:80]}')

    print('\n✅ 压缩器就绪。可接入 brahma_analysis_runner 使用。')
    print('   接入方式: 在分析前调用 get_global_context_snapshot()')
    print('   替代原有: brahma_state全量注入 → 压缩摘要注入')

    # 保存快照供其他脚本使用
    snap_path = BASE / 'data' / 'mem_compressed_context.json'
    snap_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2))
    print(f'\n   快照已保存: {snap_path}')

    return snap


if __name__ == '__main__':
    main()
