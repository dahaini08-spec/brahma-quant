#!/usr/bin/env python3
"""
pump_hunter_executor.py — 暴涨猎手执行桥接器
设计院 封印 2026-08-06 苏摩自主决策授权

职责（漏斗第二层）：
  1. 读取暴涨猎手 exec_eligible=True 的信号
  2. 调用 universal_asset_router.pump_to_brahma_score() 获取体制加权分
  3. weighted_score≥85 → 触发 brahma_engine 定向深度扫描
  4. 主信号 score≥138 AND timing=READY → 写入执行队列（走TIER3）
  5. OBSERVE豁免规则：猎手+主信号双重确认 → OBSERVE门槛 45%→35%

设计哲学：
  猎手 = 候选池筛选器（7日维度97.5%胜率）
  主信号 = 入场精度验证（精确的入场点/止损/TP）
  两者结合 = 双重过滤，胜率叠加

安全原则：
  - 不绕过 circuit_breaker / dead_hole / VaR 门控
  - OBSERVE豁免仅适用于猎手双确认信号
  - 仓位上限 1.5%NAV（TIER3），高波动妖币 SL=3.0%
"""
import sys, os, json, time
from pathlib import Path

_BASE = Path(__file__).parent.parent
for _p in [str(_BASE), str(_BASE/'brahma_brain'), str(_BASE/'scripts')]:
    if _p not in sys.path: sys.path.insert(0, _p)

# ── 内存门控 ──────────────────────────────────────────────
try:
    from brahma_mem_manager import mem_gate
    mem_gate(800)
except (ImportError, SystemExit) as _e:
    if isinstance(_e, SystemExit): raise

# ── 配置 ──────────────────────────────────────────────────
PUSH_RECORD   = _BASE / 'dharma/pump_hunter/signal_push_record.json'
EXEC_QUEUE    = _BASE / 'data/pump_exec_queue.jsonl'
HUNTER_LOG    = _BASE / 'data/hunter_outcome_log.jsonl'
WEIGHTED_MIN  = 85.0   # 体制加权分门槛
BRAHMA_MIN    = 138    # 主信号门槛（TIER3）
OBSERVE_WR    = 0.35   # 猎手双确认时 OBSERVE 豁免门槛（原45%→35%）
MAX_BATCH     = 5      # 每次最多验证5个（控制API消耗）
COOL_SECS     = 4 * 3600  # 同标的4H内不重复执行

# ── 主流程 ────────────────────────────────────────────────
def main():
    if not PUSH_RECORD.exists():
        print("HEARTBEAT_OK"); return

    push_data = json.loads(PUSH_RECORD.read_text(encoding='utf-8'))
    now_ts = time.time()

    # 读取已处理记录（防重复）
    processed = {}
    if EXEC_QUEUE.exists():
        for line in EXEC_QUEUE.read_text().strip().split('\n'):
            try:
                e = json.loads(line)
                if now_ts - e.get('ts', 0) < COOL_SECS:
                    processed[e['symbol']] = e['ts']
            except Exception:
                pass

    # 筛选 exec_eligible 且未冷却的信号
    candidates = [
        (sym, v) for sym, v in push_data.items()
        if v.get('exec_eligible')
        and sym not in processed
        and now_ts - v.get('last_push_ts', 0) < 8 * 3600  # 8H内的新鲜信号
    ]
    candidates.sort(key=lambda x: x[1].get('last_score', 0), reverse=True)
    candidates = candidates[:MAX_BATCH]

    if not candidates:
        print("HEARTBEAT_OK"); return

    print(f"[pump_hunter_executor] {len(candidates)}个候选信号待验证")

    from universal_asset_router import pump_to_brahma_score, get_regime_cached
    import brahma_engine

    executed = 0
    for sym, ph_info in candidates:
        score = ph_info.get('last_score', 0)
        regime = get_regime_cached(sym) or 'BULL_TREND'

        # Step A: 体制加权
        mock_alert = {'symbol': sym, 'score': score}
        result = pump_to_brahma_score(mock_alert, regime)
        w_score = result.get('brahma_weighted_score', 0)
        eligible = result.get('exec_eligible', False)

        print(f"  {sym}: hunter={score} regime={regime} weighted={w_score:.1f} eligible={eligible}")

        if not eligible or w_score < WEIGHTED_MIN:
            continue

        # Step B: 触发主信号深度扫描
        try:
            br = brahma_engine.analyze(sym, signal_dir='LONG', deep=True,
                                       _hunter_triggered=True)
            br_score = br.get('confluence', {}).get('total', 0)
            br_timing = br.get('timing', {}).get('status', 'WAIT')
            br_regime = br.get('regime', '?')

            print(f"    主信号: score={br_score} timing={br_timing} regime={br_regime}")

            if br_score >= BRAHMA_MIN and br_timing in ('READY', ''):
                # Step C: 写入执行队列（走 auto_executor TIER3，豁免OBSERVE）
                entry = {
                    'ts':              now_ts,
                    'symbol':          sym,
                    'source':          'pump_hunter_executor',
                    'hunter_score':    score,
                    'weighted_score':  w_score,
                    'brahma_score':    br_score,
                    'timing':          br_timing,
                    'regime':          br_regime,
                    'observe_bypass':  True,   # 猎手双确认豁免
                    'observe_wr_gate': OBSERVE_WR,
                    'params':          br.get('params', {}),
                }
                EXEC_QUEUE.parent.mkdir(parents=True, exist_ok=True)
                with open(EXEC_QUEUE, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')

                print(f"    ✅ 写入执行队列: {sym} brahma={br_score} observe_bypass=True")
                executed += 1

                # 推送苏摩
                try:
                    _push_to_jarvis(sym, score, w_score, br_score, br.get('params', {}))
                except Exception:
                    pass
            else:
                print(f"    ⏸ 主信号未达标: score={br_score}<{BRAHMA_MIN} 或 timing={br_timing}")

        except Exception as e:
            print(f"    ❌ 主信号扫描异常: {e}")

    print(f"[pump_hunter_executor] 完成: {executed}个信号写入执行队列")
    if executed == 0:
        print("HEARTBEAT_OK")


def _push_to_jarvis(sym, hunter_score, weighted, brahma_score, params):
    """推送双确认信号给苏摩"""
    try:
        import subprocess
        entry_lo = params.get('entry_lo', params.get('entry_mid', '?'))
        tp1 = params.get('tp1', '?')
        sl  = params.get('sl', params.get('sl_price', '?'))
        msg = (
            f"🎯 暴涨猎手×梵天双确认\n"
            f"标的: {sym}\n"
            f"猎手分: {hunter_score} → 加权: {weighted:.0f}\n"
            f"梵天分: {brahma_score} | 观察豁免: ✅\n"
            f"入场: {entry_lo}  TP1: {tp1}  SL: {sl}\n"
            f"[pump_hunter_executor 2026-08-06]"
        )
        from system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
        subprocess.Popen([
            'openclaw', 'msg', 'send',
            '--to', f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}',
            '--channel', 'jarvis', '--message', msg
        ])
    except Exception:
        pass


if __name__ == '__main__':
    main()
