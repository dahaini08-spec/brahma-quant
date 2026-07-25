"""
P1: 信号日志基础设施
每次 brahma_engine.analyze() 完成后自动写入 signal_log.jsonl
字段：ts / sym / score / regime / direction / price / kronos_env / kronos_p_up /
       timing_status / grade / valid / result(未知时为null)

集成方式：在 brahma_analysis_runner.py 的 run_analysis() 末尾调用
"""
import json, time, os
from pathlib import Path
from datetime import datetime, timezone

SIGNAL_LOG = Path(__file__).parent.parent / 'data' / 'logs' / 'signal_log.jsonl'

def ensure_dir():
    SIGNAL_LOG.parent.mkdir(parents=True, exist_ok=True)

def write_signal(result: dict, symbol: str = None):
    """
    将一次 analyze() 结果写入 signal_log.jsonl
    result: brahma_engine.analyze() 的返回值
    """
    if not result:
        return
    ensure_dir()

    sym   = symbol or result.get('symbol', 'UNKNOWN')
    ts    = int(time.time() * 1000)
    price = result.get('price') or result.get('entry', 0)

    record = {
        'ts':           ts,
        'ts_str':       datetime.fromtimestamp(ts/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
        'sym':          sym,
        'price':        price,
        'regime':       result.get('regime', 'UNKNOWN'),
        'direction':    result.get('signal_dir') or result.get('direction', 'UNKNOWN'),
        'score_final':  result.get('score_final'),
        'grade':        result.get('grade'),
        'timing_status':result.get('timing_status', 'UNKNOWN'),
        'timing_badge': result.get('timing_badge', ''),
        'kronos_env':   result.get('kronos_env', 'NEUTRAL'),
        'kronos_p_up':  result.get('kronos_p_up'),
        's23_p_up':     result.get('s23_p_up'),
        'valid_signal': result.get('valid_signal', False),
        'globally_blocked': result.get('globally_blocked', False),
        'ssi_risk':     result.get('ssi_risk'),
        'mtf_alignment':result.get('mtf_alignment'),
        'result':       None,   # 待交易结果回填
        'exit_price':   None,
        'pnl_pct':      None,
    }

    with open(SIGNAL_LOG, 'a') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')

    return record


def update_result(ts: int, sym: str, exit_price: float, pnl_pct: float, result_str: str = None):
    """
    回填交易结果（实盘用）
    ts: 信号时间戳（毫秒）
    """
    ensure_dir()
    if not SIGNAL_LOG.exists():
        return False

    lines = SIGNAL_LOG.read_text().strip().split('\n')
    updated = False
    new_lines = []
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            if rec.get('ts') == ts and rec.get('sym') == sym and rec.get('result') is None:
                rec['exit_price'] = exit_price
                rec['pnl_pct']    = pnl_pct
                rec['result']     = result_str or ('WIN' if pnl_pct > 0 else 'LOSS')
                updated = True
            new_lines.append(json.dumps(rec, ensure_ascii=False))
        except:
            new_lines.append(line)

    SIGNAL_LOG.write_text('\n'.join(new_lines) + '\n')
    return updated


def read_recent(n: int = 50) -> list:
    """读取最近n条信号"""
    ensure_dir()
    if not SIGNAL_LOG.exists():
        return []
    lines = SIGNAL_LOG.read_text().strip().split('\n')
    records = []
    for line in reversed(lines):
        if not line.strip(): continue
        try:
            records.append(json.loads(line))
        except: pass
        if len(records) >= n:
            break
    return list(reversed(records))


def stats_summary() -> dict:
    """统计信号日志质量"""
    ensure_dir()
    if not SIGNAL_LOG.exists():
        return {'total': 0}

    from collections import defaultdict
    records = []
    for line in SIGNAL_LOG.read_text().strip().split('\n'):
        if line.strip():
            try: records.append(json.loads(line))
            except: pass

    if not records:
        return {'total': 0}

    regime_dist  = defaultdict(int)
    timing_dist  = defaultdict(int)
    result_dist  = defaultdict(int)
    with_result  = [r for r in records if r.get('result')]

    for r in records:
        regime_dist[r.get('regime','?')] += 1
        timing_dist[r.get('timing_status','?')] += 1
    for r in with_result:
        result_dist[r.get('result','?')] += 1

    wr = sum(1 for r in with_result if r.get('result')=='WIN') / len(with_result) * 100 if with_result else 0

    return {
        'total':        len(records),
        'with_result':  len(with_result),
        'win_rate':     round(wr, 1),
        'regime_dist':  dict(regime_dist),
        'timing_dist':  dict(timing_dist),
        'result_dist':  dict(result_dist),
    }


# ── 集成补丁：自动注入 brahma_analysis_runner ──────────────
def patch_runner():
    """
    在 brahma_analysis_runner.py 的 run_analysis() 里
    注入 write_signal() 调用
    """
    runner_path = Path(__file__).parent.parent / 'brahma_brain' / 'brahma_analysis_runner.py'
    if not runner_path.exists():
        print(f'  ⚠️  runner不存在: {runner_path}')
        return False

    src = runner_path.read_text()

    # 检查是否已注入
    if 'p1_signal_log' in src or 'write_signal' in src:
        print('  ✅ signal_log 已注入 brahma_analysis_runner.py')
        return True

    # 找注入点：run_analysis() 的 return result 前
    inject_code = '''
    # [P1 signal_log 自动注入 2026-07-24]
    try:
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
        from p1_signal_log import write_signal as _write_signal
        _write_signal(result, symbol=symbol)
    except Exception:
        pass
    '''

    # 在最后一个 return result 前注入
    old = '    return result\n'
    new = inject_code + '    return result\n'
    if old in src:
        src = src.replace(old, new, 1)
        runner_path.write_text(src)
        print('  ✅ signal_log 注入成功 → brahma_analysis_runner.py')
        return True
    else:
        print('  ⚠️  未找到注入点，需要手动集成')
        return False


if __name__ == '__main__':
    print('=== P1: signal_log 基础设施 ===\n')

    # 1. 确保目录存在
    ensure_dir()
    print(f'  日志路径: {SIGNAL_LOG}')

    # 2. 写入一条测试记录
    test_rec = write_signal({
        'symbol': 'BTCUSDT',
        'price': 63902.8,
        'regime': 'BEAR_TREND',
        'signal_dir': 'SHORT',
        'score_final': 174.8,
        'grade': 88.5,
        'timing_status': 'STANDBY',
        'kronos_env': 'NEUTRAL',
        'kronos_p_up': 0.37,
        'valid_signal': True,
        'globally_blocked': False,
    }, symbol='BTCUSDT')
    print(f'  测试记录写入: {test_rec}')

    # 3. 注入 runner
    print()
    patch_runner()

    # 4. 统计
    print()
    s = stats_summary()
    print(f'  当前日志统计: {s}')

    print('\n✅ P1 signal_log 基础设施就绪')
