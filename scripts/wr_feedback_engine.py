#!/usr/bin/env python3
"""
wr_feedback_engine.py — WR矩阵每日反哺信号矩阵权重
设计院封印 2026-09-03 苏摩111

职责：
  每日读取 data/wr_matrix_realtime.json
  对统计显著(n>=30)的组合，用实测WR计算新乘数
  写入 data/regime_mult_override.json
  brahma_brain/regime_config.py 的 get_regime_mult() 优先读取此override

核心算法（Wilson CI校准）：
  raw_mult = (WR - 0.5) * 4 + 1.0     # WR=75%→mult=2.0, WR=50%→mult=1.0, WR=25%→mult=0.0
  wilson_lower = Wilson CI下限（保守估计）
  final_mult = clip(wilson_based_mult, 0.10, 2.00)  # 硬上下限

保守原则：
  - n<30 不更新（样本不足）
  - WR偏差<10%（即mult变化<0.4）不更新（噪声区间）
  - 手写铁证乘数 ± 允许float范围=±0.3（防止过度修正）
  - 每次最大调整步长 = 0.15（防止突变）

接入位置：
  - scripts/wr_feedback_engine.py（本文件）
  - brahma_brain/regime_config.py get_regime_mult() 读取override
  - crontab: 0 2 * * * python3 scripts/wr_feedback_engine.py（每日凌晨2点）
"""
import json, math, sys
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

WR_MATRIX_FILE  = BASE / 'data' / 'wr_matrix_realtime.json'
OVERRIDE_FILE   = BASE / 'data' / 'regime_mult_override.json'
FEEDBACK_LOG    = BASE / 'logs' / 'wr_feedback.log'

# 保守参数
MIN_N           = 30       # 最低样本量
MIN_WR_DELTA    = 0.05     # WR偏离0.5至少5%才更新
MAX_STEP        = 0.15     # 单次最大调整步长
MULT_FLOOR      = 0.10     # 乘数下限
MULT_CEIL       = 2.00     # 乘数上限

# 手写铁证乘数基准（来自regime_config.py，用于限制过度偏移）
BASELINE_MULT = {
    'BULL_TREND:LONG':      1.10,
    'BULL_TREND:SHORT':     0.50,
    'BEAR_TREND:LONG':      0.35,
    'BEAR_TREND:SHORT':     1.60,
    'BEAR_RECOVERY:LONG':   1.20,
    'BEAR_RECOVERY:SHORT':  0.35,
    'BULL_EARLY:LONG':      1.20,
    'BULL_EARLY:SHORT':     0.35,
    'CHOP_MID:LONG':        0.50,
    'CHOP_MID:SHORT':       0.88,
}
MAX_DEVIATION = 0.40  # 允许偏离baseline最多±0.40


def wilson_ci_lower(n_win: int, n: int, z: float = 1.645) -> float:
    """Wilson置信区间下限（90% CI）——保守估计WR"""
    if n == 0:
        return 0.0
    p = n_win / n
    denom = 1 + z**2 / n
    center = p + z**2 / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))
    return max(0.0, (center - spread) / denom)


def wr_to_mult(wr: float) -> float:
    """
    WR → 体制乘数映射
    WR=0.75 → mult=2.0（最强）
    WR=0.50 → mult=1.0（中性）
    WR=0.25 → mult=0.0（最弱）
    """
    return max(MULT_FLOOR, min(MULT_CEIL, (wr - 0.5) * 4 + 1.0))


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    line = f'[{ts}] {msg}'
    print(line)
    try:
        with open(FEEDBACK_LOG, 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass


def load_wr_matrix() -> dict:
    if not WR_MATRIX_FILE.exists():
        return {}
    try:
        d = json.loads(WR_MATRIX_FILE.read_text())
        return d.get('matrix', d) if isinstance(d, dict) else {}
    except Exception as e:
        log(f'WR矩阵读取失败: {e}')
        return {}


def load_override() -> dict:
    if OVERRIDE_FILE.exists():
        try:
            return json.loads(OVERRIDE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_override(data: dict):
    data['_updated_at'] = datetime.now(timezone.utc).isoformat()
    data['_updated_date'] = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    OVERRIDE_FILE.write_text(json.dumps(data, indent=2))


def compute_new_override(matrix: dict) -> tuple[dict, list]:
    """
    从WR矩阵计算新的override乘数
    返回: (override_dict, change_log)
    """
    current_override = load_override()
    new_override = {k: v for k, v in current_override.items() if not k.startswith('_')}
    changes = []

    for key, entry in matrix.items():
        # 只处理 regime:direction:score_bin 格式（不处理symbol格式）
        parts = key.split(':')
        if len(parts) != 3 or 'USDT' in key:
            continue

        regime, direction, score_bin = parts
        n       = int(entry.get('n', 0))
        n_win   = int(entry.get('n_win', 0))
        settled = int(entry.get('settled', n))  # settled优先，否则用n

        # 样本门槛
        if settled < MIN_N:
            continue

        # 计算Wilson保守WR
        wilson_wr = wilson_ci_lower(n_win, settled)

        # WR偏差不足，跳过
        if abs(wilson_wr - 0.5) < MIN_WR_DELTA:
            continue

        # 计算新乘数
        new_mult = wr_to_mult(wilson_wr)

        # 对比baseline，限制偏离
        regime_dir_key = f'{regime}:{direction}'
        baseline = BASELINE_MULT.get(regime_dir_key, 1.0)
        new_mult = max(baseline - MAX_DEVIATION,
                      min(baseline + MAX_DEVIATION, new_mult))

        # 限制单次调整步长
        old_mult = current_override.get(regime_dir_key, baseline)
        if abs(new_mult - old_mult) > MAX_STEP:
            new_mult = old_mult + MAX_STEP * (1 if new_mult > old_mult else -1)

        new_mult = round(new_mult, 4)

        # 只在有实质变化时更新
        if abs(new_mult - old_mult) > 0.01:
            new_override[regime_dir_key] = new_mult
            changes.append({
                'key':       regime_dir_key,
                'score_bin': score_bin,
                'n':         settled,
                'wilson_wr': round(wilson_wr, 4),
                'old_mult':  round(old_mult, 4),
                'new_mult':  new_mult,
                'baseline':  baseline,
            })

    return new_override, changes


def main():
    log('=== WR矩阵每日反哺启动 ===')

    # 防重复：同一天已运行过则跳过（防止每次手动调用累计漂移）
    current_override = load_override()
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if current_override.get('_updated_date') == today:
        log(f'今日({today})已运行过，跳过重复执行')
        print('HEARTBEAT_OK')
        return

    matrix = load_wr_matrix()
    if not matrix:
        log('WR矩阵为空，跳过')
        print('HEARTBEAT_OK')
        return

    valid_entries = {k: v for k, v in matrix.items()
                     if int(v.get('settled', v.get('n', 0))) >= MIN_N}
    log(f'WR矩阵总条目: {len(matrix)} | 统计显著(n>={MIN_N}): {len(valid_entries)}')

    new_override, changes = compute_new_override(matrix)

    if not changes:
        log('无需更新（所有组合偏差在噪声区间内）')
        print('HEARTBEAT_OK')
        return

    save_override(new_override)

    log(f'Override更新 {len(changes)} 条:')
    for c in changes:
        arrow = '↑' if c['new_mult'] > c['old_mult'] else '↓'
        log(f"  {c['key']} [{c['score_bin']}] "
            f"Wilson_WR={c['wilson_wr']:.0%} n={c['n']} "
            f"mult: {c['old_mult']:.3f} {arrow} {c['new_mult']:.3f} "
            f"(baseline={c['baseline']:.3f})")

    # P2-1: 权重变化>0.15时 LLM审核，防止异常漂移 (2026-09-04 苏摩111封印)
    # 接入位置: save_override后，推送前
    _llm_flags = []
    _big_changes = [c for c in changes if abs(c['new_mult'] - c['old_mult']) >= 0.15]
    if _big_changes:
        try:
            _llm_review_path = str(BASE / 'scripts')
            if _llm_review_path not in sys.path:
                sys.path.insert(0, _llm_review_path)
            from free_llm_client import _call_openrouter as _llm_wr
            for _ch in _big_changes[:3]:  # 每次最多审查3个
                _prompt = (
                    f"梵天WR自学习审核：{_ch['key']}\n"
                    f"Wilson_WR={_ch['wilson_wr']:.0%} n={_ch['n']}\n"
                    f"乘数变化: {_ch['old_mult']:.3f}→{_ch['new_mult']:.3f} "
                    f"(差{_ch['new_mult']-_ch['old_mult']:+.3f} baseline={_ch['baseline']:.3f})\n"
                    f"请判断：这个乘数调整合理吗？回答格式: 合理 或 异常，附简短原因(15字内)"
                )
                _resp = _llm_wr(_prompt, max_tokens=30)
                if _resp and '异常' in _resp:
                    _llm_flags.append(f"{_ch['key']}: LLM告警→{_resp.strip()[:40]}")
                    log(f'[P2-1] LLM异常告警: {_ch["key"]} {_resp.strip()[:50]}')
                else:
                    log(f'[P2-1] LLM审核通过: {_ch["key"]} {(_resp or "").strip()[:30]}')
        except Exception as _p21_e:
            log(f'[P2-1] LLM审核跳过: {_p21_e}')
    # ── end P2-1 ─────────────────────────────────────────────────────

    # 推送摘要到Jarvis
    try:
        sys.path.insert(0, str(BASE / 'scripts'))
        from push_hub import _jarvis as _pj
        summary = (f'📊 WR矩阵反哺完成\n'
                   f'更新 {len(changes)} 个体制乘数\n' +
                   '\n'.join(f"  {c['key']}: {c['old_mult']:.2f}→{c['new_mult']:.2f} "
                             f"(WR={c['wilson_wr']:.0%} n={c['n']})"
                             for c in changes[:5]))
        if _llm_flags:
            summary += '\n⚠️ LLM异常告警:\n' + '\n'.join(f'  {f}' for f in _llm_flags)
        _pj(summary, level='P2')
    except Exception:
        pass

    log('=== 反哺完成 ===')


if __name__ == '__main__':
    main()
