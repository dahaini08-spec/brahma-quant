"""
dharma_online_learner.py — 达摩院在线学习模块
设计院·达摩院 封印 2026-06-30

职责：
  每N笔结算后，基于最新EV矩阵触发小批量参数收敛
  产出：param_recommendations.json（供苏摩审核后手动应用）
  原则：只产出建议，不自动修改brahma_core（苏摩最高批准权）

触发方式：
  - live_signal_settler 每结算10笔自动调用
  - 手动: python dharma_online_learner.py --review
"""

import json, os, time
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
EV_MATRIX_PATH  = BASE / 'data' / 'wr_matrix_realtime.json'
NUDGE_LOG_PATH  = BASE / 'data' / 'param_nudge_suggestions.jsonl'
RECOMMEND_PATH  = BASE / 'data' / 'param_recommendations.json'
TRIGGER_COUNT   = 10   # 每10笔触发一次


def run_online_learning(force: bool = False) -> dict:
    """
    小批量在线学习主入口
    1. 读取EV矩阵
    2. 识别需要调整的组合
    3. 生成参数建议（不直接修改brahma_core）
    """
    matrix = _load_matrix()
    if not matrix:
        return {'status': 'skip', 'reason': 'EV矩阵为空'}

    ts = datetime.now(timezone.utc).isoformat()
    recommendations = []
    alerts = []

    for key, m in matrix.items():
        n = m.get('n', 0)
        if n < TRIGGER_COUNT and not force:
            continue

        wr  = m.get('wr', 0)
        ev  = m.get('ev', 0)
        regime, direction, score_bin = key.split(':') if key.count(':') == 2 else (key, '', '')

        # ── 规则一：持续亏损组合 → 提高score阈值 ──
        if ev < -0.15 and n >= 10:
            recommendations.append({
                'type': 'RAISE_THRESHOLD',
                'key': key, 'regime': regime, 'direction': direction,
                'current_ev': ev, 'current_wr': wr, 'n': n,
                'action': f'建议 {regime}×{direction}×{score_bin} score阈值 +5分',
                'reason': f'EV={ev:+.3f}% < -0.15%，持续亏损，提高入场门槛',
                'priority': 'HIGH' if ev < -0.3 else 'MEDIUM',
            })

        # ── 规则二：高EV铁证组合 → 放大仓位乘数 ──
        elif ev > 0.4 and wr > 0.65 and n >= 15:
            recommendations.append({
                'type': 'INCREASE_SIZE',
                'key': key, 'regime': regime, 'direction': direction,
                'current_ev': ev, 'current_wr': wr, 'n': n,
                'action': f'建议 {regime}×{direction} 仓位乘数 +0.1x（当前铁证）',
                'reason': f'EV={ev:+.3f}% > 0.4% WR={wr:.1%} > 65%，铁证组合，可加码',
                'priority': 'HIGH',
            })

        # ── 规则三：死亡螺旋警报（WR<40%且样本>20） ──
        elif wr < 0.40 and n >= 20:
            alerts.append({
                'type': 'DEATH_SPIRAL_ALERT',
                'key': key, 'regime': regime, 'direction': direction,
                'wr': wr, 'ev': ev, 'n': n,
                'action': f'🚨 {regime}×{direction} WR={wr:.1%}<40% n={n}，建议暂停此组合',
                'reason': '死亡螺旋风险，连续亏损，建议提交设计院审核',
                'priority': 'CRITICAL',
            })

        # ── 规则四：SL过紧（高TIMEOUT率） ──
        n_timeout = m.get('n_timeout', 0)
        timeout_rate = n_timeout / n if n > 0 else 0
        if timeout_rate > 0.5 and n >= 10:
            recommendations.append({
                'type': 'WIDEN_SL',
                'key': key,
                'action': f'建议 {regime}×{direction} SL宽度 +0.3%（超时率={timeout_rate:.0%}）',
                'reason': f'TIMEOUT率={timeout_rate:.0%}>50%，SL可能过紧，信号方向正确但被提前止损',
                'priority': 'MEDIUM',
            })

    result = {
        'ts': ts,
        'total_keys': len(matrix),
        'evaluated': sum(1 for m in matrix.values() if m.get('n', 0) >= TRIGGER_COUNT),
        'recommendations': recommendations,
        'alerts': alerts,
        'summary': f'{len(recommendations)}条建议 / {len(alerts)}条警报',
    }

    # 写入建议文件
    RECOMMEND_PATH.parent.mkdir(parents=True, exist_ok=True)
    RECOMMEND_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f'[达摩院在线学习] {result["summary"]} → {RECOMMEND_PATH}')

    # 高优先级警报推送
    if alerts:
        _push_alerts(alerts)

    return result


def _load_matrix() -> dict:
    try:
        if EV_MATRIX_PATH.exists():
            return json.loads(EV_MATRIX_PATH.read_text())
    except Exception:
        pass
    return {}


def _push_alerts(alerts: list):
    """推送CRITICAL级别警报到苏摩"""
    try:
        from pathlib import Path
        import sys
        sys.path.insert(0, str(BASE / 'scripts'))
        from system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
        import subprocess

        msg_lines = ['🚨 达摩院在线学习 · 死亡螺旋警报']
        for a in alerts[:3]:
            msg_lines.append(f"  {a['action']}")
        msg = '\n'.join(msg_lines)

        subprocess.run([
            'openclaw', 'message', 'send',
            '--channel', 'jarvis',
            '--to', f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}',
            '--message', msg,
        ], capture_output=True, timeout=10)
    except Exception:
        pass


def get_latest_recommendations() -> dict:
    """获取最新参数建议（供苏摩审核）"""
    try:
        if RECOMMEND_PATH.exists():
            return json.loads(RECOMMEND_PATH.read_text())
    except Exception:
        pass
    return {'recommendations': [], 'alerts': [], 'summary': '暂无数据'}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--review', action='store_true', help='查看最新建议')
    parser.add_argument('--force', action='store_true', help='强制执行（忽略样本数限制）')
    args = parser.parse_args()

    if args.review:
        rec = get_latest_recommendations()
        print(f'📊 参数建议 ({rec.get("ts","?")[:16]}):')
        for r in rec.get('recommendations', []):
            print(f'  [{r["priority"]}] {r["action"]}')
        for a in rec.get('alerts', []):
            print(f'  🚨 [{a["priority"]}] {a["action"]}')
    else:
        result = run_online_learning(force=args.force)
        print(f'✅ 完成: {result["summary"]}')
