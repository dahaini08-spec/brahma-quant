"""
loss_memory_recorder.py — 亏损记忆自动记录器
设计院 2026-09-20 苏摩111

从live_signal_log中提取已结算信号，记录到亏损记忆库。
每次运行时只处理新的已结算信号。
"""
import json, sys, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from brahma_brain.loss_memory_engine import record_trade, extract_fingerprint, get_stats

BASE = Path(__file__).parent.parent
SIGNAL_LOG = BASE / 'data' / 'live_signal_log.jsonl'
PROGRESS_FILE = BASE / 'data' / 'loss_memory_progress.json'


def load_progress():
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {'last_processed_idx': 0}


def save_progress(idx):
    PROGRESS_FILE.write_text(json.dumps({'last_processed_idx': idx}))


def main():
    if not SIGNAL_LOG.exists():
        print('live_signal_log不存在')
        return
    
    with open(SIGNAL_LOG) as f:
        lines = f.readlines()
    
    progress = load_progress()
    start_idx = progress['last_processed_idx']
    
    # 只处理已结算的信号
    settled = []
    for i, line in enumerate(lines[start_idx:], start=start_idx):
        if not line.strip():
            continue
        d = json.loads(line)
        result = d.get('result', '')
        if result in ('WIN', 'LOSS'):
            settled.append((i, d))
    
    if not settled:
        print(f'无新已结算信号（从{start_idx}开始扫描）')
        return
    
    recorded = 0
    for idx, sig in settled:
        fingerprint = {
            'symbol': sig.get('symbol', ''),
            'regime': sig.get('regime', ''),
            'signal_dir': sig.get('signal_dir', ''),
            'score_final': float(sig.get('score_final', sig.get('score', 0)) or 0),
            'rsi_1h': float(sig.get('rsi_1h', 50) or 50),
            'rsi_4h': float(sig.get('rsi_4h', 50) or 50),
            'bb_width': float(sig.get('bb_width_4h', 20) or 20),
            'hurst': float(sig.get('hurst', 0.5) or 0.5),
            'hurst_1d': float(sig.get('hurst_1d', 0.5) or 0.5),
            'hurst_4h': float(sig.get('hurst_4h', 0.5) or 0.5),
            'fvg_consensus': sig.get('fvg_consensus', ''),
            'oi_signal': sig.get('oi_signal', ''),
        }
        outcome = {
            'result': sig.get('result', ''),
            'pnl_pct': float(sig.get('pnl_pct', 0) or 0),
            'entry_price': float(sig.get('price', 0) or 0),
            'exit_price': float(sig.get('exit_price', 0) or 0),
            'stop_loss': float(sig.get('stop_loss', 0) or 0),
            'tp_target': float(sig.get('tp1', 0) or 0),
            'hold_hours': 0,  # TODO: 从settled_at计算
            'exit_reason': sig.get('outcome', ''),
            'narrative': sig.get('output_tag', ''),
        }
        if record_trade(fingerprint, outcome):
            recorded += 1
    
    # 更新进度
    last_idx = settled[-1][0] + 1 if settled else start_idx
    save_progress(last_idx)
    
    stats = get_stats()
    print(f'记录{recorded}条交易记忆，总计{stats["total"]}条 (W={stats.get("wins",0)} L={stats.get("losses",0)})')


if __name__ == '__main__':
    main()
