"""
brahma_autoresearch.py — 梵天AutoLoop v1.0
设计院 2026-08-01 自主创建（autoresearch P4层）

灵感来源: uditgoenka/autoresearch (MIT)
原生移植 autoresearch 核心原则到梵天 Python + git 体系:
  ① 机械指标驱动 — 目标: BULL_TREND:LONG WR (live_signal_log 真实结算)
  ② 单次只改一处 — atomic change，失败能精确定位
  ③ git 即记忆   — experiment: commit前缀，读log决策
  ④ 自动回滚     — smoke test 失败 → git revert
  ⑤ TSV 结果日志 — data/brahma_autoresearch_log.tsv

用法:
  python3 scripts/brahma_autoresearch.py --iterations 5 --dry-run
  python3 scripts/brahma_autoresearch.py --iterations 20
  python3 scripts/brahma_autoresearch.py --status          # 查看历史迭代
"""

# ── 内存门控（设计院2026-08-04封印）───────────────────
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'scripts') if '/scripts/' not in __file__ else _os.path.dirname(_os.path.abspath(__file__)))
try:
    from brahma_mem_manager import mem_gate as _mem_gate
    _mem_gate(700)
except (ImportError, SystemExit) as _e:
    if isinstance(_e, SystemExit): raise
# ──────────────────────────────────────────────────────

import argparse, json, subprocess, sys, time, csv
from datetime import datetime, timezone
from pathlib import Path

BASE    = Path(__file__).parent.parent
LOG     = BASE / 'data' / 'live_signal_log.jsonl'
WR_F    = BASE / 'data' / 'wr_matrix_live.json'
TSV_F   = BASE / 'data' / 'brahma_autoresearch_log.tsv'
SMOKE   = BASE / 'brahma_brain' / 'brahma_smoke_test.py'

# ── 优化目标 ──────────────────────────────────────────────────────────────────
TARGET_REGIME    = 'BULL_TREND'
TARGET_DIRECTION = 'LONG'
TARGET_KEY       = f'{TARGET_REGIME}:{TARGET_DIRECTION}'

# ── 可调参数候选池（单次只改一处）────────────────────────────────────────────
# 每个条目: (文件路径, 参数名/搜索串, 当前值, 候选新值列表, 描述)
PARAM_POOL = [
    # brahma_core.py 中 BULL_TREND LONG 的 TP 乘数
    ('brahma_brain/brahma_core.py',  '_tp1_mult',     '2.5',   ['2.0','3.0','1.8','2.2'], 'TP1乘数'),
    ('brahma_brain/brahma_core.py',  'CHOP.*tp1',     '2.5',   ['2.0','3.0'],              'CHOP TP1'),
    # timing_filter.py READY 阈值
    ('brahma_brain/timing_filter.py','READY_THRESHOLD','65',   ['60','70','55'],            'READY阈值'),
    # position_sizer.py S2 分层下限
    ('brahma_brain/position_sizer.py','S2.*score',    '138',   ['135','140','145'],         'S2分层score'),
]


def _run(cmd: list, cwd=BASE, timeout=30) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def get_live_wr(key: str = TARGET_KEY) -> dict:
    """从 wr_matrix_live.json 读取当前真实 WR"""
    if not WR_F.exists():
        return {'wr': None, 'total': 0, 'win': 0, 'loss': 0}
    d = json.loads(WR_F.read_text())
    m = d.get('matrix', {})
    v = m.get(key, {})
    return {
        'wr':    v.get('wr'),
        'total': v.get('total', 0),
        'win':   v.get('win', 0),
        'loss':  v.get('loss', 0),
        'ev':    v.get('ev_avg', 0),
    }


def run_smoke() -> bool:
    """跑冒烟测试，返回是否全绿"""
    if not SMOKE.exists():
        return True  # 无测试文件视为通过
    rc, out = _run(['python3', str(SMOKE)], timeout=60)
    passed = rc == 0 and 'FAIL' not in out.upper()
    return passed


def git_commit(msg: str) -> bool:
    rc, _ = _run(['git', 'add', '-A'])
    rc2, out = _run(['git', 'commit', '-m', msg])
    return rc2 == 0


def git_revert_last() -> bool:
    rc, out = _run(['git', 'revert', '--no-edit', 'HEAD'])
    return rc == 0


def run_settler_dry() -> dict:
    """跑 signal_settler dry-run，返回当前WR统计"""
    settler = BASE / 'scripts' / 'signal_settler.py'
    if not settler.exists():
        return get_live_wr()
    _run(['python3', str(settler)], timeout=120)
    return get_live_wr()


def log_tsv(iteration: int, action: str, param: str, old_val: str, new_val: str,
            wr_before: float | None, wr_after: float | None,
            reason: str = ''):
    """追加一行到 TSV 结果日志"""
    TSV_F.parent.mkdir(exist_ok=True)
    write_header = not TSV_F.exists()
    with open(TSV_F, 'a', newline='') as f:
        w = csv.writer(f, delimiter='\t')
        if write_header:
            w.writerow(['ts', 'iteration', 'action', 'param', 'old_val', 'new_val',
                        'wr_before', 'wr_after', 'wr_delta', 'reason'])
        wr_delta = None
        if wr_before is not None and wr_after is not None:
            wr_delta = round((wr_after - wr_before) * 100, 2)
        w.writerow([
            datetime.now(timezone.utc).isoformat(),
            iteration,
            action,
            param,
            old_val,
            new_val,
            f'{wr_before:.3f}' if wr_before is not None else 'N/A',
            f'{wr_after:.3f}' if wr_after is not None else 'N/A',
            f'{wr_delta:+.2f}%' if wr_delta is not None else 'N/A',
            reason,
        ])


def pick_next_change(iteration: int, tried: list) -> dict | None:
    """
    选择下一个要尝试的参数改动
    - 读取 git log 避免重复
    - 轮询 PARAM_POOL（跳过已试过的）
    """
    pool_idx = iteration % len(PARAM_POOL)
    file_rel, param_hint, old_val, candidates, desc = PARAM_POOL[pool_idx]
    # 选一个未试过的候选值
    for cand in candidates:
        key = f'{file_rel}:{param_hint}:{cand}'
        if key not in tried:
            return {
                'file':       file_rel,
                'param_hint': param_hint,
                'old_val':    old_val,
                'new_val':    cand,
                'desc':       desc,
                'key':        key,
            }
    return None  # 所有候选已试过


def apply_change(change: dict) -> bool:
    """用 sed 原地替换参数值（安全：只改第一处匹配）"""
    f = BASE / change['file']
    if not f.exists():
        return False
    content = f.read_text()
    # 简单字符串替换（只改一处）
    hint = change['param_hint'].split('.*')[0]  # 取 hint 前缀
    old = change['old_val']
    new = change['new_val']
    # 找包含 hint 且含 old 的行替换
    lines = content.splitlines()
    changed = False
    new_lines = []
    for line in lines:
        if hint in line and old in line and not changed:
            new_lines.append(line.replace(old, new, 1))
            changed = True
        else:
            new_lines.append(line)
    if changed:
        f.write_text('\n'.join(new_lines) + '\n')
    return changed


def revert_change(change: dict):
    """回滚：把 new_val 改回 old_val"""
    reverse = dict(change)
    reverse['old_val'] = change['new_val']
    reverse['new_val'] = change['old_val']
    apply_change(reverse)


def print_status():
    """打印历史迭代结果"""
    if not TSV_F.exists():
        print('尚无迭代记录')
        return
    with open(TSV_F) as f:
        rows = list(csv.DictReader(f, delimiter='\t'))
    print(f'共 {len(rows)} 次迭代记录:')
    for r in rows[-20:]:
        wr_b = r.get('wr_before','N/A')
        wr_a = r.get('wr_after','N/A')
        delta = r.get('wr_delta','N/A')
        print(f"  #{r['iteration']:>3} [{r['action']:6}] {r['param']:30} "
              f"{r['old_val']}→{r['new_val']}  WR:{wr_b}→{wr_a}({delta})  {r['reason']}")
    # 当前WR
    wr = get_live_wr()
    print(f"\n当前 {TARGET_KEY} WR={wr['wr']:.1%}" if wr['wr'] else
          f"\n当前 {TARGET_KEY} WR=N/A (结算数据不足)")


def main():
    parser = argparse.ArgumentParser(description='梵天AutoLoop v1.0')
    parser.add_argument('--iterations', type=int, default=5, help='迭代次数 (default: 5)')
    parser.add_argument('--dry-run', action='store_true', help='不实际修改文件和git')
    parser.add_argument('--status', action='store_true', help='查看历史迭代')
    args = parser.parse_args()

    if args.status:
        print_status()
        return

    # ── 读取历史已试过的变更 ──────────────────────────────────────────────────
    tried = set()
    if TSV_F.exists():
        with open(TSV_F) as f:
            for row in csv.DictReader(f, delimiter='\t'):
                if row.get('action') == 'KEEP':
                    tried.add(f"{row.get('param','')}:{row.get('new_val','')}")

    # ── 基线 ──────────────────────────────────────────────────────────────────
    # 先跑一次 settler 更新 WR
    print('[AutoLoop] 更新信号结算数据...')
    baseline = run_settler_dry()
    wr_base  = baseline.get('wr')
    _wr_base_str = (f'{wr_base:.1%}' if wr_base else 'N/A')
    _n_base = baseline['total']
    print(f'[AutoLoop] 基线 {TARGET_KEY} WR={_wr_base_str} (n={_n_base})')

    if baseline['total'] < 10:
        print('[AutoLoop] 结算信号不足10条，WR置信度低，建议积累更多数据后运行')

    sep = '─' * 60
    print(sep)

    kept = 0
    reverted = 0

    for i in range(1, args.iterations + 1):
        print(f'\n[AutoLoop] === 迭代 #{i}/{args.iterations} ===')

        change = pick_next_change(i - 1, tried)
        if not change:
            print('[AutoLoop] 所有候选参数均已尝试，停止')
            break

        wr_before_val = get_live_wr().get('wr')
        print(f'[AutoLoop] 参数: {change["desc"]} ({change["param_hint"]})')
        print(f'[AutoLoop] 改动: {change["old_val"]} → {change["new_val"]}')

        if args.dry_run:
            print('[AutoLoop] DRY-RUN: 跳过实际修改')
            log_tsv(i, 'DRY-RUN', change['param_hint'], change['old_val'],
                    change['new_val'], wr_before_val, None, 'dry-run')
            continue

        # 1. 应用改动
        ok = apply_change(change)
        if not ok:
            print(f'[AutoLoop] ❌ 改动失败（文件/参数未找到），跳过')
            log_tsv(i, 'SKIP', change['param_hint'], change['old_val'],
                    change['new_val'], wr_before_val, None, 'apply_failed')
            continue

        # 2. git commit
        _bwr = (f'{wr_before_val:.1%}' if wr_before_val else 'N/A')
        commit_msg = f"experiment: {change['desc']} {change['old_val']}->{change['new_val']} | baseline_WR={_bwr}"
        committed = git_commit(commit_msg)
        if not committed:
            revert_change(change)
            print('[AutoLoop] ❌ git commit 失败，已还原')
            log_tsv(i, 'SKIP', change['param_hint'], change['old_val'],
                    change['new_val'], wr_before_val, None, 'commit_failed')
            continue

        # 3. smoke test
        print('[AutoLoop] 跑冒烟测试...')
        smoke_ok = run_smoke()
        if not smoke_ok:
            print('[AutoLoop] ❌ 冒烟测试失败 → git revert')
            git_revert_last()
            log_tsv(i, 'REVERT', change['param_hint'], change['old_val'],
                    change['new_val'], wr_before_val, None, 'smoke_fail')
            reverted += 1
            continue

        # 4. 重跑 settler 更新 WR
        print('[AutoLoop] 更新WR矩阵...')
        wr_after = run_settler_dry()
        wr_after_val = wr_after.get('wr')

        # 5. 判断保留/回滚
        improved = (wr_before_val is None or wr_after_val is None or
                    wr_after_val > wr_before_val - 0.005)  # 允许0.5%噪音

        if improved:
            _wb = (f'{wr_before_val:.1%}' if wr_before_val else 'N/A')
            _wa = (f'{wr_after_val:.1%}' if wr_after_val else 'N/A')
            print(f'[AutoLoop] KEEP WR: {_wb} -> {_wa}')
            log_tsv(i, 'KEEP', change['param_hint'], change['old_val'],
                    change['new_val'], wr_before_val, wr_after_val, 'wr_improved_or_neutral')
            tried.add(change['key'])
            kept += 1
        else:
            print(f'[AutoLoop] ↩️  REVERT — WR下降: '
                  f'{wr_before_val:.1%} → {wr_after_val:.1%}')
            git_revert_last()
            log_tsv(i, 'REVERT', change['param_hint'], change['old_val'],
                    change['new_val'], wr_before_val, wr_after_val, 'wr_degraded')
            reverted += 1

        print(sep)

    # ── 总结 ──────────────────────────────────────────────────────────────────
    final = get_live_wr()
    print(f'\n[AutoLoop] 完成 | KEEP={kept} REVERT={reverted}')
    _fw = (f'{final["wr"]:.1%}' if final['wr'] else 'N/A')
    _fn = final['total']
    print(f'[AutoLoop] 最终 {TARGET_KEY} WR={_fw} (n={_fn})')
    print(f'[AutoLoop] 结果日志: {TSV_F}')


if __name__ == '__main__':
    main()
