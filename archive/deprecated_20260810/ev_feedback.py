"""
ev_feedback.py — EV实时反馈模块
设计院·达摩院 封印 2026-06-30

职责：每笔交易结算后，自动更新EV矩阵并触发参数微调
     settler → ev_feedback → wr_matrix_update → brahma_core参数收敛

架构原则：
  - 非阻断：任何异常不影响主结算流程
  - 增量式：不改历史，只追加更新
  - 轻量级：每次结算只操作单条记录，O(1)复杂度
"""

import json, os, time
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
EV_LOG_PATH = BASE / 'data' / 'ev_feedback_log.jsonl'
WR_MATRIX_PATH = BASE / 'data' / 'wr_matrix_realtime.json'

# ══════════════════════════════════════════════════════════════
# 核心：EV矩阵实时更新（每笔结算触发）
# ══════════════════════════════════════════════════════════════

def update_ev(record: dict, outcome: str) -> dict:
    """
    单笔结算后更新EV矩阵
    
    参数:
        record:  live_signal_settler 的信号记录（含regime/direction/score/pnl_pct）
        outcome: TP1/TP2/SL/TIMEOUT
    
    返回: {updated: bool, ev_delta: float, matrix_key: str}
    """
    try:
        sym       = record.get('symbol', '?')
        regime    = record.get('regime', 'UNKNOWN')
        direction = record.get('signal_dir') or record.get('direction', '')
        score     = float(record.get('score', 0) or 0)
        pnl_pct   = float(record.get('pnl_pct', 0) or 0)
        grade     = int(float(record.get('structure_grade') or record.get('grade', 0) or 0))
        signal_id = record.get('signal_id', '')
        ts_now    = datetime.now(timezone.utc).isoformat()

        # 矩阵 key: regime×direction×score分段
        score_bin = _score_bin(score)
        matrix_key = f'{regime}:{direction}:{score_bin}'

        # 加载或初始化矩阵
        matrix = _load_matrix()

        # 初始化该key
        if matrix_key not in matrix:
            matrix[matrix_key] = {
                'regime': regime, 'direction': direction, 'score_bin': score_bin,
                'n': 0, 'n_win': 0, 'n_loss': 0, 'n_timeout': 0,
                'sum_pnl': 0.0, 'sum_pnl_win': 0.0, 'sum_pnl_loss': 0.0,
                'wr': 0.0, 'ev': 0.0, 'avg_pnl_win': 0.0, 'avg_pnl_loss': 0.0,
                'last_updated': ts_now,
            }

        m = matrix[matrix_key]
        m['n'] += 1

        is_win = outcome in ('TP1', 'TP2')
        is_loss = outcome == 'SL'
        is_timeout = outcome == 'TIMEOUT'

        if is_win:
            m['n_win'] += 1
            m['sum_pnl_win'] += pnl_pct
        elif is_loss:
            m['n_loss'] += 1
            m['sum_pnl_loss'] += abs(pnl_pct)
        else:
            m['n_timeout'] += 1

        m['sum_pnl'] += pnl_pct

        # 重算统计
        n_decided = m['n_win'] + m['n_loss']
        m['wr'] = round(m['n_win'] / n_decided, 4) if n_decided > 0 else 0.0
        m['avg_pnl_win']  = round(m['sum_pnl_win'] / max(m['n_win'], 1), 4)
        m['avg_pnl_loss'] = round(m['sum_pnl_loss'] / max(m['n_loss'], 1), 4)
        # EV = WR×avgWin - (1-WR)×avgLoss
        m['ev'] = round(
            m['wr'] * m['avg_pnl_win'] - (1 - m['wr']) * m['avg_pnl_loss'], 4
        )
        m['last_updated'] = ts_now

        _save_matrix(matrix)

        # 写入EV反馈日志
        log_entry = {
            'ts': ts_now, 'signal_id': signal_id, 'symbol': sym,
            'matrix_key': matrix_key, 'outcome': outcome,
            'pnl_pct': pnl_pct, 'ev_after': m['ev'],
            'wr_after': m['wr'], 'n': m['n'],
        }
        with open(EV_LOG_PATH, 'a') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')

        pass  # [静默]

        # 触发参数微调（每10笔触发一次）
        if m['n'] % 10 == 0:
            _trigger_param_nudge(matrix_key, m)

        return {'updated': True, 'ev_delta': m['ev'], 'matrix_key': matrix_key, 'wr': m['wr']}

    except Exception as e:
        pass  # [静默]
        return {'updated': False, 'error': str(e)}


def _score_bin(score: float) -> str:
    """评分分档：<120 / 120-139 / 140-159 / 160+"""
    if score >= 160: return '160+'
    if score >= 140: return '140-159'
    if score >= 120: return '120-139'
    return '<120'


def _load_matrix() -> dict:
    try:
        if WR_MATRIX_PATH.exists():
            return json.loads(WR_MATRIX_PATH.read_text())
    except Exception:
        pass
    return {}


def _save_matrix(matrix: dict):
    WR_MATRIX_PATH.parent.mkdir(parents=True, exist_ok=True)
    WR_MATRIX_PATH.write_text(json.dumps(matrix, ensure_ascii=False, indent=2))


def _trigger_param_nudge(matrix_key: str, m: dict):
    """
    每10笔触发参数微调建议
    仅写入建议文件，不直接修改brahma_core（设计院安全原则）
    """
    try:
        nudge_path = BASE / 'data' / 'param_nudge_suggestions.jsonl'
        suggestion = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'matrix_key': matrix_key,
            'n': m['n'], 'wr': m['wr'], 'ev': m['ev'],
            'suggestion': _generate_nudge(m),
        }
        with open(nudge_path, 'a') as f:
            f.write(json.dumps(suggestion, ensure_ascii=False) + '\n')
        pass  # [静默]
        # 同步触发达摩院在线学习
        try:
            import sys as _dl_sys
            _dl_sys.path.insert(0, str(BASE / 'brahma_brain'))
            from dharma_online_learner import run_online_learning as _dl_run
            _dl_run()
        except Exception:
            pass
        # ── 断点B修复：同步更新 CONFIDENCE_TABLE（2026-07-03）──
        try:
            from position_sizer import sync_confidence_table_from_wr as _sync_ct
            _sync_ct(min_n=10)
        except Exception as _ct_e:
            pass  # [静默]
    except Exception:
        pass


def _generate_nudge(m: dict) -> str:
    """基于EV趋势生成参数微调建议"""
    wr = m['wr']
    ev = m['ev']
    n  = m['n']
    
    if n < 10:
        return f'样本不足({n}<10)，暂不建议调整'
    
    nudges = []
    if wr < 0.45:
        nudges.append(f'WR={wr:.1%}<45% → 建议提高score阈值+5分或收紧SL')
    elif wr > 0.75:
        nudges.append(f'WR={wr:.1%}>75% → 可适当放大仓位乘数+0.1x')
    
    if ev < -0.1:
        nudges.append(f'EV={ev:+.3f}%<-0.1% → 建议回撤该体制×方向组合配置')
    elif ev > 0.5:
        nudges.append(f'EV={ev:+.3f}%>+0.5% → 铁证组合，可提升信号优先级')
    
    return ' | '.join(nudges) if nudges else f'EV={ev:+.3f}% WR={wr:.1%} 正常范围，维持当前参数'


# ══════════════════════════════════════════════════════════════
# 对外接口：settler调用点
# ══════════════════════════════════════════════════════════════

def on_settlement(record: dict, outcome: str) -> dict:
    """
    settler结算完成后调用此接口
    封装所有EV反馈逻辑，对settler零侵入
    """
    return update_ev(record, outcome)


def get_ev_summary() -> dict:
    """获取当前EV矩阵摘要"""
    matrix = _load_matrix()
    if not matrix:
        return {'total_keys': 0, 'matrix': {}}
    
    # 排序：EV从高到低
    sorted_keys = sorted(matrix.keys(), key=lambda k: -matrix[k].get('ev', 0))
    top5 = {k: matrix[k] for k in sorted_keys[:5]}
    bottom5 = {k: matrix[k] for k in sorted_keys[-5:] if matrix[k].get('n', 0) >= 5}
    
    return {
        'total_keys': len(matrix),
        'top5_ev': top5,
        'bottom5_ev': bottom5,
        'last_updated': max((v.get('last_updated', '') for v in matrix.values()), default=''),
    }


if __name__ == '__main__':
    summary = get_ev_summary()
    print(f'EV矩阵: {summary["total_keys"]}个组合')
    for k, v in summary.get('top5_ev', {}).items():
        print(f'  TOP: {k} EV={v["ev"]:+.3f}% WR={v["wr"]:.1%} n={v["n"]}')
