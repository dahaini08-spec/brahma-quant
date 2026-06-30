"""
hunter_main.py — 猎手拉娜v2 主入口（接口存根 v1.0）
设计院 2026-05-24

架构说明：
  猎手扫描由 brahma_matrix.py 和 alpha_hunter 负责
  本文件作为 brahma_core.py 的调用接口，防止 ModuleNotFoundError
  未来可在此实装 run_once() 调用真实扫描逻辑
"""
import json, time, os
from pathlib import Path

BASE = Path(__file__).parent.parent.parent  # trading-system/

def run_once(state: dict, dry_run: bool = False) -> dict:
    """
    猎手扫描接口
    brahma_core 通过此函数触发信号扫描
    当前版本：读取 signal_queue.jsonl 最新信号，同步到 state
    """
    signal_queue_file = BASE / 'data' / 'signal_queue.jsonl'

    try:
        signals = []
        if signal_queue_file.exists():
            with open(signal_queue_file) as f:
                for line in f:
                    if line.strip():
                        try:
                            s = json.loads(line)
                            # 只取最近1小时的有效信号
                            ts_str = s.get('ts', '')
                            if ts_str:
                                from datetime import datetime, timezone, timedelta
                                ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                                if datetime.now(timezone.utc) - ts < timedelta(hours=1):
                                    signals.append(s)
                        except:
                            pass

        # 更新 state
        state['signal_queue'] = signals
        state['scan_count'] = state.get('scan_count', 0) + 1
        state['last_scan_ts'] = time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime())
        state['scanned'] = len(signals)
        state['candidates'] = sum(1 for s in signals if float(s.get('score', 0)) >= 145)
        state['regime'] = _get_regime()

        track = state.get('track', 'BRAHMA')
        print(f"[HunterMain] {track} 扫描完成: queue={len(signals)} candidates={state['candidates']} dry={dry_run}")

    except Exception as e:
        print(f"[HunterMain] ⚠️ 扫描异常: {e}")

    return state


def _get_regime() -> str:
    try:
        state_file = BASE / 'data' / 'brahma_state.json'
        d = json.load(open(state_file))
        return d.get('regime', 'UNKNOWN')
    except:
        return 'UNKNOWN'
