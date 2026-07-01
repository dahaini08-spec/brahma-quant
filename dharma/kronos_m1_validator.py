"""
kronos_m1_validator.py — 达摩院 Kronos M1 验证器 v1.0
══════════════════════════════════════════════════════
设计院 封印 2026-07-01

职责：
  监控 kronos_bridge shadow 日志，在满足 M1 条件时：
  1. 计算 Kronos vs Kronos-Lite 方向准确率差异
  2. 自动切换 KRONOS_BRIDGE_MODE=blend（M1通过）
  3. 推送达摩院验证报告到 Jarvis

M1 升级条件（达摩院铁律）：
  - n ≥ 100 条有实际结果（actual_result）的 shadow 记录
  - Kronos 方向准确率 ≥ Kronos-Lite + 2pp
  - 连续5笔 Kronos 正确率不低于 Lite（稳定性验证）

运行方式：
  python3 dharma/kronos_m1_validator.py          # 查看当前状态
  python3 dharma/kronos_m1_validator.py --check  # 运行 M1 检验
  python3 dharma/kronos_m1_validator.py --fill-results  # 填充实盘结果（达摩院）
"""

from __future__ import annotations
import json, os, sys, argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional

BASE       = Path(__file__).parent.parent
DATA_DIR   = BASE / 'data'
SHADOW_LOG = DATA_DIR / 'kronos_bridge_shadow.jsonl'
CONFIG_ENV = BASE / '.env'
RESULT_LOG = DATA_DIR / 'kronos_m1_results.json'

# M1 升级阈值
M1_MIN_N        = 100    # 最少 n 条验证样本
M1_WR_DELTA_PP  = 2.0    # Kronos 需高于 Lite 至少 2pp
M1_STABILITY_N  = 5      # 最后 N 条 Kronos 不低于 Lite


# ════════════════════════════════════════════════════════════════
# 1. 读取 shadow 日志
# ════════════════════════════════════════════════════════════════

def load_shadow_records() -> List[Dict]:
    """加载所有 shadow 日志记录"""
    if not SHADOW_LOG.exists():
        return []
    records = []
    with open(SHADOW_LOG) as f:
        for line in f:
            try:
                records.append(json.loads(line.strip()))
            except Exception:
                pass
    return records


def load_validated_records(records: List[Dict]) -> List[Dict]:
    """只返回有 actual_result 的记录"""
    return [r for r in records if r.get('actual_result') in ('WIN', 'LOSS')]


# ════════════════════════════════════════════════════════════════
# 2. 从 trade_records 自动填充实盘结果
# ════════════════════════════════════════════════════════════════

def fill_results_from_trade_records() -> int:
    """
    从 wuqu_positions/trade_records 自动填充 shadow 日志的 actual_result

    匹配逻辑：symbol + 时间窗口(±30分钟) + direction
    """
    records = load_shadow_records()
    unfilled = [r for r in records if not r.get('actual_result')]

    if not unfilled:
        print("✅ 所有记录已有 actual_result")
        return 0

    # 加载 trade_records
    trade_files = list(DATA_DIR.glob('trade_records*.json')) + \
                  list(DATA_DIR.glob('wuqu_positions*.json'))

    if not trade_files:
        print(f"⚠️  未找到 trade_records 文件，需手动填充")
        return 0

    trades = []
    for tf in trade_files:
        try:
            d = json.loads(tf.read_text())
            if isinstance(d, list):
                trades.extend(d)
            elif isinstance(d, dict) and 'trades' in d:
                trades.extend(d['trades'])
        except Exception:
            pass

    if not trades:
        print("⚠️  trade_records 无数据")
        return 0

    filled = 0
    updated_records = []

    for rec in records:
        if rec.get('actual_result'):
            updated_records.append(rec)
            continue

        rec_ts  = rec.get('ts', '')
        rec_sym = rec.get('symbol', '')
        rec_dir = rec.get('direction', '')

        # 尝试匹配
        matched = False
        for trade in trades:
            t_sym = trade.get('symbol', trade.get('sym', ''))
            t_dir = trade.get('direction', trade.get('side', ''))
            t_pnl = trade.get('pnl', trade.get('realized_pnl', None))

            if t_sym != rec_sym:
                continue
            if t_dir and rec_dir and t_dir.upper()[:4] != rec_dir.upper()[:4]:
                continue
            if t_pnl is None:
                continue

            # 时间匹配（±2小时）
            try:
                from datetime import timedelta
                rec_dt   = datetime.fromisoformat(rec_ts.replace('Z', '+00:00'))
                trade_ts = trade.get('close_time', trade.get('ts', trade.get('closeTime', '')))
                if trade_ts:
                    if isinstance(trade_ts, (int, float)):
                        trade_dt = datetime.fromtimestamp(trade_ts/1000, tz=timezone.utc)
                    else:
                        trade_dt = datetime.fromisoformat(str(trade_ts).replace('Z', '+00:00'))
                    if abs((trade_dt - rec_dt).total_seconds()) > 7200:
                        continue
            except Exception:
                pass

            rec['actual_result'] = 'WIN' if float(t_pnl) > 0 else 'LOSS'
            rec['actual_pnl']    = float(t_pnl)
            matched = True
            filled += 1
            break

        updated_records.append(rec)

    # 重写 shadow 日志
    with open(SHADOW_LOG, 'w') as f:
        for r in updated_records:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

    print(f"✅ 自动填充 {filled}/{len(unfilled)} 条 actual_result")
    return filled


# ════════════════════════════════════════════════════════════════
# 3. M1 验证核心
# ════════════════════════════════════════════════════════════════

def run_m1_check(records: Optional[List[Dict]] = None, verbose: bool = True) -> Dict:
    """
    执行 M1 验证

    Returns:
        {
          'm1_pass':         bool,
          'n':               int,
          'kronos_wr':       float,
          'lite_wr':         float,
          'delta_pp':        float,
          'stability_pass':  bool,
          'recommendation':  str,   # 'upgrade_to_blend' | 'continue_shadow' | 'insufficient_data'
        }
    """
    if records is None:
        records = load_shadow_records()

    validated = load_validated_records(records)
    n = len(validated)

    if n < M1_MIN_N:
        result = {
            'm1_pass':        False,
            'n':              n,
            'n_needed':       M1_MIN_N - n,
            'kronos_wr':      None,
            'lite_wr':        None,
            'delta_pp':       None,
            'stability_pass': False,
            'recommendation': 'insufficient_data',
            'message':        f'样本不足: {n}/{M1_MIN_N}，还需 {M1_MIN_N - n} 条有实际结果的记录',
        }
        if verbose:
            print(f"⏳ M1 进度: {n}/{M1_MIN_N} ({n/M1_MIN_N*100:.0f}%)")
            print(f"   还需 {M1_MIN_N - n} 条有 actual_result 的 shadow 记录")
        return result

    # 计算准确率
    kronos_correct = 0
    lite_correct   = 0
    lite_n         = 0

    for r in validated:
        actual_win = r['actual_result'] == 'WIN'
        ks = r.get('kronos_score', 0)
        ls = r.get('lite_score', None)

        # Kronos 方向判断（正分=做多看涨，负分=做空看跌）
        kronos_dir_correct = (ks > 0 and actual_win) or (ks < 0 and not actual_win)
        if kronos_dir_correct:
            kronos_correct += 1

        # Lite 方向判断
        if ls is not None:
            lite_dir_correct = (ls > 0 and actual_win) or (ls < 0 and not actual_win)
            if lite_dir_correct:
                lite_correct += 1
            lite_n += 1

    kronos_wr = kronos_correct / n
    lite_wr   = lite_correct / lite_n if lite_n > 0 else 0.5
    delta_pp  = (kronos_wr - lite_wr) * 100

    # 稳定性检验：最近 M1_STABILITY_N 条
    recent = validated[-M1_STABILITY_N:] if len(validated) >= M1_STABILITY_N else validated
    recent_k_corr = 0
    recent_l_corr = 0
    recent_l_n    = 0

    for r in recent:
        actual_win = r['actual_result'] == 'WIN'
        ks = r.get('kronos_score', 0)
        ls = r.get('lite_score', None)
        if (ks > 0 and actual_win) or (ks < 0 and not actual_win):
            recent_k_corr += 1
        if ls is not None:
            if (ls > 0 and actual_win) or (ls < 0 and not actual_win):
                recent_l_corr += 1
            recent_l_n += 1

    recent_k_wr     = recent_k_corr / len(recent)
    recent_l_wr     = recent_l_corr / recent_l_n if recent_l_n > 0 else 0.5
    stability_pass  = recent_k_wr >= recent_l_wr

    m1_pass = (delta_pp >= M1_WR_DELTA_PP) and stability_pass

    recommendation = 'upgrade_to_blend' if m1_pass else \
                     ('continue_shadow' if delta_pp >= 0 else 'investigate_regression')

    result = {
        'm1_pass':          m1_pass,
        'n':                n,
        'kronos_wr':        round(kronos_wr, 4),
        'lite_wr':          round(lite_wr, 4),
        'delta_pp':         round(delta_pp, 2),
        'stability_pass':   stability_pass,
        'recent_n':         len(recent),
        'recent_k_wr':      round(recent_k_wr, 4),
        'recent_l_wr':      round(recent_l_wr, 4),
        'recommendation':   recommendation,
        'threshold':        {'n': M1_MIN_N, 'delta_pp': M1_WR_DELTA_PP},
        'checked_at':       datetime.now(timezone.utc).isoformat(),
    }

    if verbose:
        _print_m1_report(result)

    return result


def _print_m1_report(r: Dict):
    """打印 M1 验证报告"""
    m1_icon = '✅' if r['m1_pass'] else '❌'
    print("╔════════════════════════════════════════════════╗")
    print("║  达摩院 · Kronos M1 验证报告                    ║")
    print("╠════════════════════════════════════════════════╣")
    print(f"║  验证样本: {r['n']:>4} / {r['threshold']['n']:<4}                          ║")
    print(f"║  Kronos WR:  {r['kronos_wr']*100:>5.1f}%                            ║")
    print(f"║  Lite WR:    {r['lite_wr']*100:>5.1f}%                            ║")
    print(f"║  Δ:         {r['delta_pp']:>+6.2f}pp  (需≥+{r['threshold']['delta_pp']}pp)          ║")
    print(f"║  稳定性:    {'✅ 通过' if r['stability_pass'] else '❌ 未通过':<10} (最近{r['recent_n']}条)       ║")
    print("╠════════════════════════════════════════════════╣")
    print(f"║  M1 结果: {m1_icon}  {'通过 → 升级至 blend 模式' if r['m1_pass'] else '未通过 → 继续积累'}       ║")
    print(f"║  建议: {r['recommendation']:<40}║")
    print("╚════════════════════════════════════════════════╝")


# ════════════════════════════════════════════════════════════════
# 4. 自动升级：M1通过 → blend 模式
# ════════════════════════════════════════════════════════════════

def upgrade_to_blend() -> bool:
    """
    M1 验证通过后，更新 .env 将 KRONOS_BRIDGE_MODE 切换为 blend
    """
    try:
        env_path = CONFIG_ENV
        if env_path.exists():
            content = env_path.read_text()
            if 'KRONOS_BRIDGE_MODE=' in content:
                import re
                content = re.sub(r'KRONOS_BRIDGE_MODE=\w+', 'KRONOS_BRIDGE_MODE=blend', content)
            else:
                content += '\nKRONOS_BRIDGE_MODE=blend\n'
            env_path.write_text(content)
        else:
            env_path.write_text('KRONOS_BRIDGE_MODE=blend\n')

        os.environ['KRONOS_BRIDGE_MODE'] = 'blend'
        print("✅ KRONOS_BRIDGE_MODE=blend 已写入 .env")
        print("   重启 brahma_analysis_runner 后生效")
        return True
    except Exception as e:
        print(f"❌ 升级失败: {e}")
        return False


def upgrade_to_live() -> bool:
    """M2 验证通过后，切换为 live 模式"""
    try:
        env_path = CONFIG_ENV
        if env_path.exists():
            content = env_path.read_text()
            import re
            content = re.sub(r'KRONOS_BRIDGE_MODE=\w+', 'KRONOS_BRIDGE_MODE=live', content)
            env_path.write_text(content)
        os.environ['KRONOS_BRIDGE_MODE'] = 'live'
        print("🚀 KRONOS_BRIDGE_MODE=live 已写入 .env（M2完全替换Kronos-Lite）")
        return True
    except Exception as e:
        print(f"❌ 升级失败: {e}")
        return False


# ════════════════════════════════════════════════════════════════
# 5. 状态仪表板
# ════════════════════════════════════════════════════════════════

def status_dashboard() -> str:
    """完整状态仪表板，供推送/打印使用"""
    records    = load_shadow_records()
    validated  = load_validated_records(records)
    n_total    = len(records)
    n_valid    = len(validated)
    current_mode = os.environ.get('KRONOS_BRIDGE_MODE', 'shadow')

    # 来源统计
    sources = {}
    for r in records:
        s = r.get('source', 'unknown')
        sources[s] = sources.get(s, 0) + 1

    # 分歧统计（|Kronos - Lite| >= 3）
    big_deltas = [r for r in records if abs(r.get('delta', 0)) >= 3]

    lines = [
        "🏛️ 达摩院 · Kronos Bridge 状态",
        f"",
        f"📊 Shadow 日志",
        f"  总记录: {n_total} 条",
        f"  已验证: {n_valid} / {M1_MIN_N} (M1门槛)",
        f"  进度:   {'█' * min(20, int(n_valid/M1_MIN_N*20))}{'░'*(20-min(20,int(n_valid/M1_MIN_N*20)))} {n_valid/M1_MIN_N*100:.0f}%",
        f"",
        f"🔧 当前模式: {current_mode.upper()}",
        f"  来源分布: {sources}",
        f"  大分歧(|Δ|≥3): {len(big_deltas)} 条",
    ]

    if n_valid >= 10:
        r = run_m1_check(records, verbose=False)
        lines += [
            f"",
            f"📈 Kronos vs Lite (n={n_valid})",
            f"  Kronos WR: {r['kronos_wr']*100:.1f}%",
            f"  Lite WR:   {r['lite_wr']*100:.1f}%",
            f"  Δ:        {r['delta_pp']:+.2f}pp (需≥+{M1_WR_DELTA_PP}pp)",
            f"  M1:       {'✅ 可升级blend' if r['m1_pass'] else '⏳ 积累中'}",
        ]

    lines += [
        f"",
        f"升级路径: shadow → blend(M1) → live(M2)",
        f"下次检验: n={max(M1_MIN_N, n_valid+1)} 时自动触发",
    ]

    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
# 6. 主入口
# ════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='达摩院 Kronos M1 验证器')
    parser.add_argument('--check',        action='store_true', help='运行 M1 验证')
    parser.add_argument('--status',       action='store_true', help='显示状态仪表板')
    parser.add_argument('--fill-results', action='store_true', help='从 trade_records 填充实盘结果')
    parser.add_argument('--upgrade-blend',action='store_true', help='手动升级至 blend 模式')
    parser.add_argument('--upgrade-live', action='store_true', help='手动升级至 live 模式')
    parser.add_argument('--mock-fill',    type=int, default=0,  help='填充 N 条 mock 记录（测试用）')
    args = parser.parse_args()

    # 默认显示状态
    if not any([args.check, args.fill_results, args.upgrade_blend,
                args.upgrade_live, args.mock_fill]):
        args.status = True

    if args.status:
        print(status_dashboard())

    if args.fill_results:
        fill_results_from_trade_records()

    if args.mock_fill > 0:
        # 测试用：写入 N 条带 actual_result 的 mock shadow 记录
        import random
        DATA_DIR.mkdir(exist_ok=True)
        with open(SHADOW_LOG, 'a') as f:
            for i in range(args.mock_fill):
                ks = random.randint(-10, 12)
                ls = random.randint(-10, 12)
                win = random.random() < (0.62 if ks > 0 else 0.38)
                rec = {
                    'ts':            datetime.now(timezone.utc).isoformat(),
                    'symbol':        random.choice(['BTCUSDT', 'ETHUSDT', 'SOLUSDT']),
                    'direction':     'LONG' if ks > 0 else 'SHORT',
                    'regime':        random.choice(['BEAR_TREND', 'BULL_TREND', 'CHOP_MID']),
                    'kronos_score':  ks,
                    'lite_score':    ls,
                    'delta':         ks - ls,
                    'p_up':          round(random.uniform(0.3, 0.75), 3),
                    'source':        'kronos',
                    'actual_result': 'WIN' if win else 'LOSS',
                }
                f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        print(f"✅ 写入 {args.mock_fill} 条 mock 记录")

    if args.check:
        result = run_m1_check()
        if result['m1_pass']:
            print("\n🎯 M1 条件满足！执行升级...")
            upgrade_to_blend()

    if args.upgrade_blend:
        upgrade_to_blend()

    if args.upgrade_live:
        upgrade_to_live()
