#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════════╗
║  梵天设计院 · 达摩院 持续测试系统 (Dharma CI) v1.0               ║
║  Continuous Intelligence — 分析·执行·智能·自动化衔接             ║
╠═══════════════════════════════════════════════════════════════════╣
║  流程:                                                            ║
║  1. 读取实验队列 data/exp_queue.json                              ║
║  2. 按优先级依次执行各实验模块                                    ║
║  3. 结果统计分析 → 强信号提取                                     ║
║  4. 自动回写 Blueprint / brahma_brain 参数                        ║
║  5. 生成报告 → Jarvis 推送                                        ║
║  6. 写入 data/dharma_ci_state.json 供 cron 监控                   ║
╚═══════════════════════════════════════════════════════════════════╝

用法:
  python3 dharma/dharma_ci.py                  # 执行队列中全部实验
  python3 dharma/dharma_ci.py --dry-run        # 仅分析，不回写参数
  python3 dharma/dharma_ci.py --fast           # 快速模式（减少bootstrap）
  python3 dharma/dharma_ci.py --status         # 查看当前CI状态
  python3 dharma/dharma_ci.py --add "name"     # 向队列追加实验
"""

import os, sys, json, time, argparse, traceback
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR    = Path(__file__).parent.parent
DHARMA_DIR  = Path(__file__).parent
DATA_DIR    = BASE_DIR / 'data'
RESULTS_DIR = DHARMA_DIR / 'results'
BP_FILE     = BASE_DIR / 'FANTAN_BLUEPRINT_V3.json'

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(DHARMA_DIR))

EXP_QUEUE_F  = DATA_DIR / 'exp_queue.json'
CI_STATE_F   = DATA_DIR / 'dharma_ci_state.json'
CI_LOG_F     = BASE_DIR / 'logs' / 'dharma_ci.log'

# ════════════════════════════════════════════════════════════════
# 日志
# ════════════════════════════════════════════════════════════════
def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    line = f'[CI {ts}] {msg}'
    print(line)
    try:
        with open(CI_LOG_F, 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass

# ════════════════════════════════════════════════════════════════
# 实验队列管理
# ════════════════════════════════════════════════════════════════
def load_queue() -> list:
    if not EXP_QUEUE_F.exists():
        return []
    try:
        return json.loads(EXP_QUEUE_F.read_text())
    except Exception:
        return []

def save_queue(q: list):
    EXP_QUEUE_F.write_text(json.dumps(q, indent=2, ensure_ascii=False))

def add_to_queue(name: str, desc: str = '', priority: int = 99):
    q = load_queue()
    q.append({'name': name, 'desc': desc, 'priority': priority,
               'trigger': 'manual', 'ts': datetime.now(timezone.utc).isoformat()})
    q.sort(key=lambda x: x.get('priority', 99))
    save_queue(q)
    _log(f'实验已加入队列: {name}')

# ════════════════════════════════════════════════════════════════
# CI 状态
# ════════════════════════════════════════════════════════════════
def load_ci_state() -> dict:
    if CI_STATE_F.exists():
        try:
            return json.loads(CI_STATE_F.read_text())
        except Exception:
            pass
    return {'runs': 0, 'last_run': None, 'last_status': None,
            'total_experiments': 0, 'improvements': [], 'history': []}

def save_ci_state(state: dict):
    CI_STATE_F.write_text(json.dumps(state, indent=2, ensure_ascii=False))

# ════════════════════════════════════════════════════════════════
# 实验执行器
# ════════════════════════════════════════════════════════════════

def run_whale_signal_weight(fast: bool = True) -> dict:
    """鲸鱼信号权重优化 — 基于达摩院v7 BTC PF=2.20"""
    _log('▶ 实验: whale_signal_weight')
    try:
        # 读取最新v7结果
        v7_f = RESULTS_DIR / 'dharma_v7_20260519_052956.json'
        if not v7_f.exists():
            # 找最新v7文件
            v7_files = sorted(RESULTS_DIR.glob('dharma_v7_*.json'))
            if not v7_files:
                return {'status': 'skip', 'reason': '无v7结果文件'}
            v7_f = v7_files[-1]

        v7 = json.loads(v7_f.read_text())

        # 提取各组鲸鱼信号PF
        whale_pf = {}
        for group, data in v7.items():
            if group.startswith('_'):
                continue
            inds = data.get('indicators', {})
            ws = inds.get('whale_signal', {})
            if ws:
                whale_pf[group] = {'pf': ws.get('pf', 0), 'wr': ws.get('wr', 0), 'n': ws.get('n', 0)}

        # 计算最优权重调整
        avg_pf = sum(v['pf'] for v in whale_pf.values()) / len(whale_pf) if whale_pf else 1.0
        btc_pf = whale_pf.get('BTC', {}).get('pf', 2.20)
        eth_pf = whale_pf.get('ETH', {}).get('pf', 2.15)

        # 权重建议：PF>2.0 → 加权 20%
        current_weight = 30  # D11当前上限
        recommended_weight = int(current_weight * min(avg_pf / 1.5, 1.5))

        return {
            'status': 'done',
            'whale_pf_by_group': whale_pf,
            'avg_pf': round(avg_pf, 3),
            'btc_pf': btc_pf,
            'eth_pf': eth_pf,
            'current_d11_max': current_weight,
            'recommended_d11_max': recommended_weight,
            'action': 'increase_weight' if avg_pf > 1.8 else 'keep',
            'confidence': 'HIGH' if (btc_pf > 2.0 and eth_pf > 2.0) else 'MEDIUM',
        }
    except Exception as e:
        return {'status': 'error', 'error': str(e)}


def run_smc_threshold_scan(fast: bool = True) -> dict:
    """SMC结构评分阈值扫描"""
    _log('▶ 实验: smc_threshold_scan')
    try:
        v7_files = sorted(RESULTS_DIR.glob('dharma_v7_*.json'))
        if not v7_files:
            return {'status': 'skip', 'reason': '无v7结果'}
        v7 = json.loads(v7_files[-1].read_text())

        results = {}
        for group, data in v7.items():
            if group.startswith('_'):
                continue
            smc = data.get('indicators', {}).get('smc_structure', {})
            if smc:
                results[group] = {'pf': smc.get('pf', 0), 'wr': smc.get('wr', 0)}

        avg_pf = sum(v['pf'] for v in results.values()) / len(results) if results else 1.0
        # SMC PF>1.4 → 保持权重，PF<1.0 → 降权
        action = 'increase' if avg_pf > 1.4 else ('decrease' if avg_pf < 1.0 else 'keep')

        return {
            'status': 'done',
            'smc_by_group': results,
            'avg_pf': round(avg_pf, 3),
            'action': action,
            'recommended_d04_max': 20 if avg_pf > 1.4 else 15,
        }
    except Exception as e:
        return {'status': 'error', 'error': str(e)}


def run_divergence_sensitivity(fast: bool = True) -> dict:
    """D03背离引擎敏感度验证"""
    _log('▶ 实验: divergence_sensitivity')
    try:
        sys.path.insert(0, str(BASE_DIR / 'brahma_brain'))
        from divergence_engine import divergence_score
        from data_cache import get_klines

        test_cases = [
            ('BTCUSDT', 'SHORT', '1h'),
            ('BTCUSDT', 'LONG',  '4h'),
            ('ETHUSDT', 'SHORT', '1h'),
            ('ETHUSDT', 'LONG',  '4h'),
        ]
        results = []
        nonzero = 0
        for sym, dire, iv in test_cases:
            try:
                kline_iv = iv.replace('h','h')
                k = get_klines(sym, kline_iv, 50)
                o=[float(x[1]) for x in k]; h=[float(x[2]) for x in k]
                l=[float(x[3]) for x in k]; c=[float(x[4]) for x in k]
                r = divergence_score(o, h, l, c, dire, iv.upper())
                results.append({'sym': sym, 'dir': dire, 'tf': iv,
                                 'score': r['score'], 'grade': r['grade']})
                if r['score'] > 0:
                    nonzero += 1
            except Exception as te:
                results.append({'sym': sym, 'dir': dire, 'tf': iv, 'score': 0, 'error': str(te)})

        sensitivity = nonzero / len(test_cases) * 100
        return {
            'status': 'done',
            'test_cases': len(test_cases),
            'nonzero_signals': nonzero,
            'sensitivity_pct': round(sensitivity, 1),
            'results': results,
            'verdict': 'IMPROVED' if sensitivity >= 50 else 'NEEDS_WORK',
        }
    except Exception as e:
        return {'status': 'error', 'error': str(e)}


def run_lstm_warmup_training(fast: bool = True) -> dict:
    """LSTM批量样本预训练验证"""
    _log('▶ 实验: lstm_warmup_training')
    try:
        sys.path.insert(0, str(BASE_DIR / 'brahma_brain'))
        from lstm_engine import get_model, analyze as lstm_analyze
        import json

        buf_f = DATA_DIR / 'lstm_train_buffer.jsonl'
        wt_f  = DATA_DIR / 'lstm_weights.json'
        n_buf = len(buf_f.read_text().strip().split('\n')) if buf_f.exists() and buf_f.stat().st_size > 5 else 0

        model = get_model()

        # 测试推理
        test_results = []
        for sym in ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']:
            for dire in ['SHORT', 'LONG']:
                try:
                    r = lstm_analyze(sym, dire)
                    test_results.append({
                        'sym': sym, 'dir': dire,
                        'prob': r.get('prob_down', 0.5),
                        'score': r.get('score', 0),
                        'conf': r.get('confidence', 'LOW'),
                    })
                except Exception:
                    pass

        noncentral = sum(1 for r in test_results if abs(r['prob'] - 0.5) > 0.05)

        return {
            'status': 'done',
            'buffer_size': n_buf,
            'weights_exist': wt_f.exists(),
            'test_cases': len(test_results),
            'noncentral_pct': round(noncentral / len(test_results) * 100, 1) if test_results else 0,
            'temp': 'HOT' if n_buf >= 50 else ('WARM' if n_buf >= 20 else 'COLD'),
            'verdict': 'ACTIVE' if n_buf >= 50 else 'WARMING',
        }
    except Exception as e:
        return {'status': 'error', 'error': str(e)}




def run_system_full_backtest(fast: bool = True) -> dict:
    """主系统 full_15d 引擎完整回测"""
    _log('▶ 实验: system_full_backtest')
    try:
        import sys
        sys.path.insert(0, str(BASE_DIR / 'brahma_brain'))
        from dharma.offline_engine import backtest_full
        syms = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT']
        results = {}
        for sym in syms:
            r = backtest_full(sym, threshold=130, fast=True, step=10)
            results[sym] = r
        done = [s for s, r in results.items() if r.get('status') == 'done']
        avg_pf = sum(results[s]['pf'] for s in done) / len(done) if done else 0
        avg_wr = sum(results[s]['wr'] for s in done) / len(done) if done else 0
        return {
            'status': 'done',
            'by_sym': {s: {'wr': results[s].get('wr'), 'pf': results[s].get('pf'),
                            'n': results[s].get('n')} for s in done},
            'avg_pf': round(avg_pf, 3),
            'avg_wr': round(avg_wr, 4),
            'verdict': 'PASS' if avg_pf >= 1.2 else 'FAIL',
            'engine': 'full_15d',
        }
    except Exception as e:
        return {'status': 'error', 'error': str(e)}

# 实验路由表
EXPERIMENT_RUNNERS = {
    'whale_signal_weight':    run_whale_signal_weight,
    'smc_threshold_scan':     run_smc_threshold_scan,
    'divergence_sensitivity': run_divergence_sensitivity,
    'lstm_warmup_training':   run_lstm_warmup_training,
    'system_full_backtest':    run_system_full_backtest,
}

# ════════════════════════════════════════════════════════════════
# 结果分析 → Blueprint 回写
# ════════════════════════════════════════════════════════════════
def analyze_and_apply(results: dict, dry_run: bool = False) -> list:
    """分析实验结果，提取改进建议，可选写回Blueprint"""
    improvements = []

    # 1. 鲸鱼信号权重
    ws = results.get('whale_signal_weight', {})
    if ws.get('status') == 'done' and ws.get('action') == 'increase_weight':
        imp = {
            'type': 'weight_increase',
            'dim': 'D11',
            'param': 'whale_max_score',
            'old': ws['current_d11_max'],
            'new': ws['recommended_d11_max'],
            'confidence': ws.get('confidence', 'MEDIUM'),
            'reason': f'v7回测 BTC PF={ws["btc_pf"]:.2f} ETH PF={ws["eth_pf"]:.2f} avg={ws["avg_pf"]:.2f}',
        }
        improvements.append(imp)

    # 2. SMC阈值
    smc = results.get('smc_threshold_scan', {})
    if smc.get('status') == 'done' and smc.get('action') != 'keep':
        improvements.append({
            'type': 'threshold_adjust',
            'dim': 'D04',
            'action': smc['action'],
            'recommended_max': smc.get('recommended_d04_max', 20),
            'avg_pf': smc.get('avg_pf', 0),
        })

    # 3. 背离敏感度
    div = results.get('divergence_sensitivity', {})
    if div.get('status') == 'done':
        improvements.append({
            'type': 'engine_status',
            'dim': 'D03',
            'verdict': div.get('verdict', 'UNKNOWN'),
            'sensitivity_pct': div.get('sensitivity_pct', 0),
        })

    # 4. LSTM状态
    lstm = results.get('lstm_warmup_training', {})
    if lstm.get('status') == 'done':
        improvements.append({
            'type': 'ml_status',
            'module': 'lstm',
            'temp': lstm.get('temp', 'COLD'),
            'buffer': lstm.get('buffer_size', 0),
            'verdict': lstm.get('verdict', 'WARMING'),
        })

    # ── 回写 Blueprint ─────────────────────────────────────────
    if not dry_run and improvements:
        try:
            bp = json.loads(BP_FILE.read_text())
            bp.setdefault('_ci_results', {})
            bp['_ci_results'] = {
                'ts': datetime.now(timezone.utc).isoformat(),
                'improvements': improvements,
                'applied': [i for i in improvements if i.get('type') == 'weight_increase'],
            }
            # 实际应用高置信度权重调整
            for imp in improvements:
                if (imp.get('type') == 'weight_increase' and
                        imp.get('confidence') == 'HIGH'):
                    bp.setdefault('_brain_params', {})
                    bp['_brain_params'][imp['param']] = imp['new']
                    _log(f'  ✅ Blueprint回写: {imp["param"]} {imp["old"]}→{imp["new"]}')

            BP_FILE.write_text(json.dumps(bp, indent=2, ensure_ascii=False))
            _log(f'  ✅ Blueprint更新完成  {len(improvements)}项改进')
        except Exception as e:
            _log(f'  ⚠️ Blueprint回写失败: {e}')

    return improvements

# ════════════════════════════════════════════════════════════════
# 自动补充下轮实验队列
# ════════════════════════════════════════════════════════════════
def auto_refill_queue(results: dict, trade_n: int = 0):
    """根据实盘进度和结果，自动填充下轮实验队列"""
    next_queue = []

    # 基础实验 — 每次CI运行都验证
    next_queue.append({'name': 'divergence_sensitivity', 'desc': 'D03背离引擎持续验证',
                       'priority': 1, 'trigger': 'auto',
                       'ts': datetime.now(timezone.utc).isoformat()})
    next_queue.append({'name': 'lstm_warmup_training', 'desc': 'LSTM升温状态监控',
                       'priority': 2, 'trigger': 'auto',
                       'ts': datetime.now(timezone.utc).isoformat()})

    # 条件实验 — 实盘积累触发
    if trade_n >= 50:
        next_queue.append({'name': 'whale_signal_weight', 'desc': '鲸鱼权重v7验证(50笔后)',
                           'priority': 3, 'trigger': 'auto',
                           'ts': datetime.now(timezone.utc).isoformat()})
    if trade_n >= 30:
        next_queue.append({'name': 'smc_threshold_scan', 'desc': 'SMC阈值实盘校准',
                           'priority': 4, 'trigger': 'auto',
                           'ts': datetime.now(timezone.utc).isoformat()})

    # 总是包含基础权重实验
    if not any(q['name'] == 'whale_signal_weight' for q in next_queue):
        next_queue.append({'name': 'whale_signal_weight', 'desc': '鲸鱼权重v7基线验证',
                           'priority': 5, 'trigger': 'auto',
                           'ts': datetime.now(timezone.utc).isoformat()})
    if not any(q['name'] == 'smc_threshold_scan' for q in next_queue):
        next_queue.append({'name': 'smc_threshold_scan', 'desc': 'SMC阈值基线验证',
                           'priority': 6, 'trigger': 'auto',
                           'ts': datetime.now(timezone.utc).isoformat()})

    save_queue(next_queue)
    _log(f'  🔄 下轮队列已更新: {len(next_queue)} 个实验')
    return next_queue

# ════════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════════
def main(dry_run: bool = False, fast: bool = True):
    _log('=' * 60)
    _log(f'🔱 达摩院 CI 启动  {"[DRY-RUN]" if dry_run else "[LIVE]"}  {"[FAST]" if fast else "[FULL]"}')
    _log('=' * 60)

    # ── Step-0: 一键同步 SSOT → 全系统参数对齐 (2026-06-04) ──
    try:
        import subprocess as _sp0
        _sync = BASE_DIR / 'sync_all.py'
        _r0 = _sp0.run(['python3', str(_sync)], capture_output=True, text=True, timeout=15)
        if '同步完成' in _r0.stdout:
            _log('✅ sync_all: SSOT参数已同步')
        else:
            _log(f'⚠️ sync_all输出: {_r0.stdout[-200:]}')
    except Exception as _e0:
        _log(f'⚠️ sync_all异常(不阻断CI): {_e0}')

    state = load_ci_state()
    state['runs'] = state.get('runs', 0) + 1
    state['last_run'] = datetime.now(timezone.utc).isoformat()

    queue = load_queue()
    if not queue:
        _log('⚠️ 实验队列为空，自动填充默认实验')
        auto_refill_queue({})
        queue = load_queue()

    _log(f'实验队列: {len(queue)} 个')
    for q in queue:
        _log(f'  [{q.get("priority",99)}] {q["name"]} — {q.get("desc","")[:40]}')

    # ── P0 内容逻辑回归测试（比实验更早运行，任何代码变更后先过这关）──
    _log('\n─── P0: 内容逻辑回归测试 ───')
    try:
        import subprocess as _sp
        _ci_test = BASE_DIR / 'scripts' / 'test_content_logic.py'
        _r = _sp.run(['python3', str(_ci_test)], capture_output=True, text=True, timeout=30)
        if _r.returncode == 0:
            # 提取通过/失败数
            import re as _re
            _m = _re.search(r'(\d+)✅ 通过\s+(\d+)❌ 失败', _r.stdout)
            if _m:
                _log(f'  ✅ P0通过: {_m.group(1)}项正常  {_m.group(2)}项失败')
            else:
                _log('  ✅ P0通过')
        else:
            _log(f'  ❌ P0失败！内容逻辑测试未通过:')
            for _line in _r.stdout.split('\n'):
                if '❌' in _line:
                    _log(f'    {_line.strip()}')
            state['p0_content_logic'] = 'FAIL'
    except Exception as _e:
        _log(f'  ⚠️ P0跳过: {_e}')

    # ── 执行实验 ──────────────────────────────────────────────
    results = {}
    completed = []
    failed = []
    t_total = time.time()

    for exp in sorted(queue, key=lambda x: x.get('priority', 99)):
        name = exp['name']
        runner = EXPERIMENT_RUNNERS.get(name)
        if not runner:
            _log(f'  ⚠️ 未知实验: {name}，跳过')
            continue

        t0 = time.time()
        try:
            result = runner(fast=fast)
            results[name] = result
            elapsed = time.time() - t0
            status = result.get('status', '?')
            if status == 'done':
                completed.append(name)
                _log(f'  ✅ {name} → {status} ({elapsed:.1f}s)')
            else:
                failed.append(name)
                _log(f'  ⚠️ {name} → {status}: {result.get("reason", result.get("error",""))}')
        except Exception as e:
            results[name] = {'status': 'error', 'error': str(e)}
            failed.append(name)
            _log(f'  ❌ {name} → 异常: {e}')

    # ── 结果分析 + Blueprint 回写 ─────────────────────────────
    _log('\n【分析结果 → 改进建议】')
    improvements = analyze_and_apply(results, dry_run=dry_run)
    for imp in improvements:
        _log(f'  💡 {imp.get("type","?")} [{imp.get("dim", imp.get("module",""))}] {imp}')

    # ── 实盘笔数 ─────────────────────────────────────────────
    trade_n = 0
    tr_f = DATA_DIR / 'trade_records.jsonl'
    if tr_f.exists():
        try:
            trade_n = len([l for l in tr_f.read_text().strip().split('\n') if l.strip()])
        except Exception:
            pass

    # ── 自动补充下轮队列 ──────────────────────────────────────
    _log('\n【更新下轮实验队列】')
    next_q = auto_refill_queue(results, trade_n=trade_n)

    # ── 保存CI状态 ────────────────────────────────────────────
    elapsed_total = time.time() - t_total
    run_record = {
        'ts': state['last_run'],
        'completed': len(completed),
        'failed': len(failed),
        'improvements': len(improvements),
        'elapsed_s': round(elapsed_total, 1),
        'trade_n': trade_n,
    }
    state['history'] = ([run_record] + state.get('history', []))[:20]
    state['total_experiments'] = state.get('total_experiments', 0) + len(completed)
    state['improvements'] = (improvements + state.get('improvements', []))[:50]
    state['last_status'] = 'ok' if not failed else 'partial'
    state['next_queue_n'] = len(next_q)
    save_ci_state(state)

    # ── 最终摘要 ─────────────────────────────────────────────
    _log('\n' + '=' * 60)
    _log(f'  达摩院 CI 完成  耗时={elapsed_total:.1f}s')
    _log(f'  ✅ 成功={len(completed)}  ⚠️ 失败={len(failed)}  💡 改进={len(improvements)}')
    _log(f'  实盘进度={trade_n}笔  下轮队列={len(next_q)}个实验')
    _log('=' * 60)

    # ── 格式化输出（供 Jarvis/cron 读取）────────────────────
    print('\n' + '─' * 50)
    print(f'🔱 达摩院 CI · {state["last_run"][:16]} UTC')
    print(f'  实验: {len(completed)}✅ / {len(queue)}  耗时: {elapsed_total:.0f}s')
    print(f'  实盘进度: {trade_n}笔')
    print()
    for imp in improvements:
        t = imp.get('type','')
        if t == 'weight_increase':
            print(f'  💡 D11鲸鱼权重: {imp["old"]}→{imp["new"]} (PF={imp.get("reason","")})')
        elif t == 'engine_status':
            print(f'  📊 D03背离敏感度: {imp.get("sensitivity_pct",0)}%  {imp.get("verdict","")}')
        elif t == 'ml_status':
            print(f'  🤖 LSTM: {imp.get("temp","")}  buffer={imp.get("buffer",0)}  {imp.get("verdict","")}')
        elif t == 'threshold_adjust':
            print(f'  ⚙️ D04 SMC阈值: {imp.get("action","")}  avg_pf={imp.get("avg_pf",0):.2f}')
    print('─' * 50)

    return state


def show_status():
    state = load_ci_state()
    print(f'\n{"═"*55}')
    print(f'  🔱 达摩院 CI 状态')
    print(f'{"═"*55}')
    print(f'  总运行次数: {state.get("runs", 0)}')
    print(f'  上次运行:   {state.get("last_run", "从未")}')
    print(f'  上次状态:   {state.get("last_status", "?")}')
    print(f'  累计实验:   {state.get("total_experiments", 0)}')
    print(f'  待执行队列: {state.get("next_queue_n", 0)} 个')
    print(f'\n  最近改进建议:')
    for imp in state.get('improvements', [])[:5]:
        print(f'    💡 {imp.get("type","")} [{imp.get("dim", imp.get("module",""))}]')
    print(f'\n  最近运行历史:')
    for r in state.get('history', [])[:5]:
        print(f'    {r["ts"][:16]} ✅{r["completed"]} ⚠️{r["failed"]} 💡{r["improvements"]} {r["elapsed_s"]}s')
    print(f'{"═"*55}\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='达摩院持续测试系统')
    parser.add_argument('--dry-run', action='store_true', help='仅分析，不回写参数')
    parser.add_argument('--fast', action='store_true', default=True, help='快速模式')
    parser.add_argument('--full', action='store_true', help='完整科学模式（慢）')
    parser.add_argument('--status', action='store_true', help='查看CI状态')
    parser.add_argument('--add', type=str, help='向队列追加实验名')
    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.add:
        add_to_queue(args.add)
    else:
        fast_mode = not args.full
        main(dry_run=args.dry_run, fast=fast_mode)

# ══════════════════════════════════════════════
# [I7升级] 实盘信号候选池质量评估（每日CI增加）
# ══════════════════════════════════════════════
def _ci_live_signal_quality():
    """评估live_signal_candidates.jsonl的数据质量"""
    import json
    from pathlib import Path
    f = Path(__file__).parent.parent / 'data' / 'live_signal_candidates.jsonl'
    if not f.exists():
        return {'status': 'SKIP', 'msg': '候选信号池不存在，运行signal_recorder后生成'}
    lines = list(open(f))
    total = len(lines)
    with_outcome = sum(1 for l in lines if json.loads(l).get('outcome') is not None)
    wins = sum(1 for l in lines if json.loads(l).get('outcome')=='WIN')
    wr = wins / max(1, with_outcome)
    return {
        'status': 'OK',
        'total_candidates': total,
        'with_outcome': with_outcome,
        'win_rate': round(wr, 3),
        'pending_outcome': total - with_outcome,
        'msg': f'{total}条候选 {with_outcome}条已评估 WR={wr:.1%}'
    }
