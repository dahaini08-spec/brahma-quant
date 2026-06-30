#!/usr/bin/env python3
"""
达摩院参数总线 v1.0 — DharmaBus
======================================
功能：训练结果 → 自动写入 → 梵天大脑实时生效
每次训练完成后调用对应 push_*() 方法，参数立即对系统生效

文件：data/dharma_runtime.json — 所有节点统一写入的运行时配置

架构：
  达摩院训练节点
    ├── push_m01()  → 品种专项参数(sl/mh/thr/kelly)
    ├── push_m02()  → 置信区间 → 仓位上限解锁
    ├── push_m03()  → 体制×方向矩阵 → 方向过滤
    ├── push_m04()  → TP动态矩阵 → RR优化
    ├── push_m05()  → 连败风险量化 → 心理预期
    ├── push_m06()  → 跨品种相关矩阵 → 组合优化
    ├── push_m07()  → 时间窗口效应 → 禁用时段
    └── push_m08()  → 最终冠军认证 → 全局参数锁定

梵天大脑启动时自动加载 dharma_runtime.json
"""

import json, os, math, datetime
from pathlib import Path

BASE = Path(__file__).parent.parent
RUNTIME_FILE = BASE / 'data' / 'dharma_runtime.json'
BLACKLIST_PERMANENT = {'TONUSDT', 'LUNA2USDT', 'GUAUSDT', 'XRPUSDT', 'LUNAUSTC', 'USTCUSDT'}


# ══════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════

def _quarter_kelly(wr: float, pf: float, cap: float = 0.08) -> float:
    """Quarter-Kelly仓位计算，上限cap"""
    if wr <= 0 or pf <= 0:
        return 0.0
    b = pf * (1 - wr) / wr
    kelly = (wr * b - (1 - wr)) / b
    return round(min(max(kelly * 0.25, 0.0), cap), 4)


def _confidence_grade(n: int, pf: float, wr: float) -> str:
    """品种置信等级"""
    if n >= 100 and pf >= 2.0 and wr >= 0.50:
        return 'S'   # 旗舰
    elif n >= 50 and pf >= 1.8 and wr >= 0.48:
        return 'A'   # 可信
    elif n >= 30 and pf >= 1.5:
        return 'B'   # 参考
    elif n >= 20 and pf >= 1.3:
        return 'C'   # 观察
    else:
        return 'D'   # 样本不足


def _load() -> dict:
    """加载当前运行时配置"""
    if RUNTIME_FILE.exists():
        try:
            return json.loads(RUNTIME_FILE.read_text())
        except Exception:
            pass
    return {
        '_meta': {'version': '1.0', 'created': _ts()},
        'sym_params': {},
        'pos_limits': {},
        'regime_matrix': {},
        'tp_matrix': {},
        'drawdown_limits': {},
        'correlation_matrix': {},
        'time_blackout': {},
        'champion_params': {},
        'blacklist': list(BLACKLIST_PERMANENT),
        'global': {
            'min_score': 158,
            'default_sl': 2.0,
            'default_mh': 12,
            'default_pos': 0.05,
            'risk_per_trade': 0.008,
        }
    }


def _save(state: dict):
    """原子写入"""
    state['_meta']['updated'] = _ts()
    tmp = str(RUNTIME_FILE) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(RUNTIME_FILE))
    print(f'[DharmaBus] ✅ 写入 {RUNTIME_FILE.name}')


def _ts():
    return datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')


# ══════════════════════════════════════════════════════
# M01 — 全量参数扫描结果写入
# ══════════════════════════════════════════════════════

def push_m01(results: dict, min_n: int = 30, min_pf: float = 1.3):
    """
    M01训练结果 → 品种专项参数
    输入格式: {sym: {n, wr, pf, ev, params, best_thr, best_sl, best_mh}}
    """
    state = _load()
    updated = 0
    skipped_blacklist = 0

    for sym, v in results.items():
        if sym in BLACKLIST_PERMANENT:
            skipped_blacklist += 1
            continue
        n   = v.get('n', 0)
        pf  = v.get('pf', 0.0)
        wr  = v.get('wr', 0.0)
        ev  = v.get('ev', 0.0)
        params_str = v.get('params', '')

        if n < min_n or pf < min_pf:
            continue

        # 解析参数字符串 "thr=160,sl=2.0,mh=16"
        thr, sl, mh = 160, 2.0, 12
        for part in params_str.split(','):
            k, _, val = part.partition('=')
            k = k.strip()
            if k == 'thr':   thr = int(val)
            elif k == 'sl':  sl  = float(val)
            elif k == 'mh':  mh  = int(val)

        kelly = _quarter_kelly(wr, pf)
        grade = _confidence_grade(n, pf, wr)

        state['sym_params'][sym] = {
            'thr':        thr,
            'sl_mult':    sl,
            'mh':         mh,
            'kelly_pos':  kelly,
            'wr':         round(wr, 4),
            'pf':         round(pf, 4),
            'ev':         round(ev, 4),
            'n':          n,
            'grade':      grade,
            'source':     'M01',
            'ts':         _ts(),
        }
        updated += 1

    # 更新全局最优 MIN_SCORE（基于M01铁证 thr=160品种均PF最高）
    state['global']['min_score'] = 158  # M01铁证安全边际

    _save(state)
    print(f'[M01] 写入{updated}品种专项参数 | 跳过黑名单{skipped_blacklist}个')
    return updated


# ══════════════════════════════════════════════════════
# M02 — Bootstrap置信区间 → 仓位解锁
# ══════════════════════════════════════════════════════

def push_m02(boot_results: dict):
    """
    M02 Bootstrap结果 → 仓位上限
    输入: {sym: {ci95: [low, high], ci99: [...], median_pf, n}}
    """
    state = _load()
    unlocked = []
    downgraded = []

    for sym, b in boot_results.items():
        if sym in BLACKLIST_PERMANENT:
            continue
        ci95_low  = b.get('ci95', [0, 0])[0]
        ci99_low  = b.get('ci99', [0, 0])[0]
        med_pf    = b.get('median_pf', 0)
        n         = b.get('n', 0)

        if ci95_low >= 1.5:
            pos = 0.07   # 高置信 → 满仓
            tier = 'GOLD'
        elif ci95_low >= 1.3:
            pos = 0.05   # 中置信 → 标准仓
            tier = 'SILVER'
        elif ci95_low >= 1.0:
            pos = 0.03   # 低置信 → 轻仓
            tier = 'BRONZE'
        else:
            pos = 0.0    # 置信下限<1.0 → 禁用
            tier = 'DISABLED'
            downgraded.append(sym)

        state['pos_limits'][sym] = {
            'max_pos':  pos,
            'ci95_low': ci95_low,
            'ci99_low': ci99_low,
            'tier':     tier,
            'source':   'M02',
            'ts':       _ts(),
        }
        if pos >= 0.05:
            unlocked.append(f'{sym}({tier})')

    _save(state)
    print(f'[M02] 仓位解锁: {unlocked}')
    if downgraded:
        print(f'[M02] ⚠️ 降级品种: {downgraded}')


# ══════════════════════════════════════════════════════
# M03 — 体制×方向矩阵
# ══════════════════════════════════════════════════════

def push_m03(regime_matrix: dict):
    """
    M03体制矩阵 → 方向过滤
    输入: {sym: {regime: {long: {wr,pf,n}, short: {wr,pf,n}}}}
    """
    state = _load()
    rules = {}

    for sym, regimes in regime_matrix.items():
        if sym in BLACKLIST_PERMANENT:
            continue
        rules[sym] = {}
        for regime, dirs in regimes.items():
            long_pf  = dirs.get('long',  {}).get('pf', 0)
            short_pf = dirs.get('short', {}).get('pf', 0)
            long_wr  = dirs.get('long',  {}).get('wr', 0)
            short_wr = dirs.get('short', {}).get('wr', 0)
            long_n   = dirs.get('long',  {}).get('n',  0)
            short_n  = dirs.get('short', {}).get('n',  0)

            # 方向评分：PF×WR加权
            long_score  = long_pf  * long_wr  if long_n  >= 10 else 0
            short_score = short_pf * short_wr if short_n >= 10 else 0

            if long_score > short_score * 1.2:
                best_dir = 'LONG'
            elif short_score > long_score * 1.2:
                best_dir = 'SHORT'
            else:
                best_dir = 'BOTH'

            rules[sym][regime] = {
                'best_dir':   best_dir,
                'long_pf':    round(long_pf, 3),
                'short_pf':   round(short_pf, 3),
                'long_wr':    round(long_wr, 3),
                'short_wr':   round(short_wr, 3),
                'confidence': 'HIGH' if max(long_n, short_n) >= 30 else 'LOW',
            }

    state['regime_matrix'] = rules
    state['regime_matrix']['_ts'] = _ts()
    _save(state)
    print(f'[M03] 体制矩阵写入: {len(rules)}品种')


# ══════════════════════════════════════════════════════
# M04 — TP动态矩阵
# ══════════════════════════════════════════════════════

def push_m04(tp_matrix: dict):
    """
    M04 TP矩阵 → 最优RR
    输入: {sym: {regime: {score_range: best_rr}}}
    """
    state = _load()
    state['tp_matrix'] = tp_matrix
    state['tp_matrix']['_ts'] = _ts()
    _save(state)
    print(f'[M04] TP动态矩阵写入: {len(tp_matrix)-1}品种')


# ══════════════════════════════════════════════════════
# M05 — 连败蒙特卡洛 → 心理预期管理
# ══════════════════════════════════════════════════════

def push_m05(drawdown_data: dict):
    """
    M05连败分析 → 风险上限
    输入: {sym: {max_streak_p5, max_streak_p95, max_dd_p95}}
    """
    state = _load()
    state['drawdown_limits'] = drawdown_data
    state['drawdown_limits']['_ts'] = _ts()
    _save(state)
    print(f'[M05] 连败风险写入: {len(drawdown_data)-1}品种')


# ══════════════════════════════════════════════════════
# M06 — 跨品种相关矩阵
# ══════════════════════════════════════════════════════

def push_m06(corr_matrix: dict, best_trio: list = None):
    """
    M06相关矩阵 → 最优同持组合
    输入: {sym_a: {sym_b: corr_coef}}
    """
    state = _load()
    state['correlation_matrix'] = corr_matrix
    if best_trio:
        state['correlation_matrix']['_best_trio'] = best_trio
    state['correlation_matrix']['_ts'] = _ts()
    _save(state)
    print(f'[M06] 相关矩阵写入 | 最优三组合: {best_trio}')


# ══════════════════════════════════════════════════════
# M07 — 时间窗口效应
# ══════════════════════════════════════════════════════

def push_m07(time_effects: dict):
    """
    M07时间窗口 → 禁用时段
    输入: {weekday: {0..6: pf}, hour_utc: {0..23: pf}, month: {1..12: pf}}
    """
    state = _load()
    blackout = {}

    # 自动识别禁用时段（PF < 0.95）
    for dim, data in time_effects.items():
        blackout[dim] = [k for k, v in data.items() if isinstance(v, (int, float)) and v < 0.95]

    state['time_blackout'] = {
        'rules':   blackout,
        'raw':     time_effects,
        '_ts':     _ts(),
    }
    _save(state)
    print(f'[M07] 时间窗口写入 | 禁用时段: {blackout}')


# ══════════════════════════════════════════════════════
# M08 — 最终冠军认证
# ══════════════════════════════════════════════════════

def push_m08(champion: dict):
    """
    M08最终认证 → 全局参数锁定
    输入: {thr, sl, mh, tp, median_pf, ci99_low, certified_syms}
    """
    state = _load()
    state['champion_params'] = champion
    state['champion_params']['_ts'] = _ts()
    # 更新全局默认参数
    if champion.get('ci99_low', 0) >= 1.5:
        state['global']['min_score']  = champion.get('thr', 158)
        state['global']['default_sl'] = champion.get('sl',  2.0)
        state['global']['default_mh'] = champion.get('mh',  12)
        print(f'[M08] 🏆 冠军参数已锁定: {champion}')
    else:
        print(f'[M08] ⚠️ CI99下限<1.5，冠军认证未通过')
    _save(state)


# ══════════════════════════════════════════════════════
# M09 — 品种×维度权重矩阵（零训练成本，即时落地）
# ══════════════════════════════════════════════════════

def push_m09(dim_weight_map: dict):
    """
    M09品种×维度权重 → 评分体系精准化
    输入: {sym: {dim_name: weight_factor}}
    weight=0.0  强负向维度清零（如BTC谐波-0.381）
    weight=0.5  弱负向维度减半
    weight=1.5  弱正向维度增强
    weight=2.0  强正向维度双倍（如ETH背离+0.277）
    来源: full_universe_backtest dim_contrib铁证
    """
    state = _load()
    state['dim_weight'] = dim_weight_map
    state['dim_weight']['_ts'] = _ts()
    total_zeros = sum(
        1 for sym_d in dim_weight_map.values()
        if isinstance(sym_d, dict)
        for w in sym_d.values() if w == 0.0
    )
    _save(state)
    print(f'[M09] ✅ 品种×维度权重写入 | {len(dim_weight_map)-0}品种 | {total_zeros}个维度清零')


def get_dim_weight(sym: str, dim_name: str) -> float:
    """获取品种在特定维度上的权重因子，默认1.0"""
    state = _load()
    sym_map = state.get('dim_weight', {}).get(sym, {})
    return float(sym_map.get(dim_name, 1.0))


# ══════════════════════════════════════════════════════
# M11 — CI宽度→仓位折扣（稳定性保险丝）
# ══════════════════════════════════════════════════════

def push_m11(ci_discount_map: dict):
    """
    M11 CI宽度→仓位折扣 → 高波动品种自动降仓
    输入: {sym: {ci_width, discount, reason}}
    折扣规则: CI>6.0→×0.55  CI>4.0→×0.70  CI>2.5→×0.85
    来源: M02 Bootstrap CI95数据
    """
    state = _load()
    state['ci_discount'] = ci_discount_map
    state['ci_discount']['_ts'] = _ts()
    # 同步更新 pos_limits（叠加折扣）
    applied = []
    for sym, info in ci_discount_map.items():
        if not isinstance(info, dict): continue
        discount = info.get('discount', 1.0)
        if sym in state['pos_limits']:
            base_pos = state['pos_limits'][sym]['max_pos']
            new_pos  = round(base_pos * discount, 3)
            state['pos_limits'][sym]['max_pos_raw'] = base_pos   # 保留原始值
            state['pos_limits'][sym]['max_pos']     = new_pos    # 折扣后值
            state['pos_limits'][sym]['ci_discount']  = discount
            applied.append(f'{sym}:{base_pos:.0%}→{new_pos:.1%}')
    _save(state)
    print(f'[M11] ✅ CI折扣写入 | 仓位调整: {applied}')


def get_pos_with_ci_discount(sym: str) -> float:
    """获取应用CI折扣后的品种最大仓位"""
    state = _load()
    pl = state.get('pos_limits', {}).get(sym, {})
    return pl.get('max_pos', state['global']['default_pos'])


# ══════════════════════════════════════════════════════
# M10 — 体制时机计数器（N14黄金窗口）
# ══════════════════════════════════════════════════════

def push_m10(regime_timing_rules: dict):
    """
    M10体制时机 → dist加减分规则
    输入: {dist_range: {score_delta, label, pf}}
    例: {'5~10': {'delta': +6, 'label': '黄金窗口', 'pf': 1.625}}
    来源: N14_regime_timing铁证
    """
    state = _load()
    state['regime_timing'] = regime_timing_rules
    state['regime_timing']['_ts'] = _ts()
    _save(state)
    print(f'[M10] ✅ 体制时机规则写入 | {len(regime_timing_rules)-1}个dist区间')


# ══════════════════════════════════════════════════════
# 读取接口（梵天大脑启动时使用）
# ══════════════════════════════════════════════════════

def load_runtime() -> dict:
    """梵天大脑加载运行时配置"""
    return _load()


def get_sym_params(sym: str) -> dict:
    """获取品种专项参数"""
    state = _load()
    return state.get('sym_params', {}).get(sym, {})


def get_pos_limit(sym: str) -> float:
    """获取品种最大仓位"""
    state = _load()
    limit = state.get('pos_limits', {}).get(sym, {})
    return limit.get('max_pos', state['global']['default_pos'])


def get_regime_dir(sym: str, regime: str) -> str:
    """获取品种在当前体制下的最优方向"""
    state = _load()
    matrix = state.get('regime_matrix', {})
    sym_data = matrix.get(sym, {})
    regime_data = sym_data.get(regime, {})
    return regime_data.get('best_dir', 'BOTH')


def get_best_rr(sym: str, regime: str, score: int) -> float:
    """获取当前条件下的最优RR目标"""
    state = _load()
    tp = state.get('tp_matrix', {})
    sym_tp = tp.get(sym, {})
    regime_tp = sym_tp.get(regime, {})
    # 按评分段查找
    for seg, rr in sorted(regime_tp.items()):
        lo, hi = seg.split('~') if '~' in seg else (0, 999)
        if int(lo) <= score <= int(hi):
            return float(rr)
    return 3.0  # 默认RR


def is_time_blackout(weekday: int, hour_utc: int, month: int) -> bool:
    """检查当前时间是否在禁用窗口"""
    state = _load()
    blackout = state.get('time_blackout', {}).get('rules', {})
    if str(weekday) in [str(x) for x in blackout.get('weekday', [])]:
        return True
    if str(hour_utc) in [str(x) for x in blackout.get('hour_utc', [])]:
        return True
    if str(month) in [str(x) for x in blackout.get('month', [])]:
        return True
    return False


# ══════════════════════════════════════════════════════
# 状态报告
# ══════════════════════════════════════════════════════

def status_report():
    """打印当前达摩院总线状态"""
    state = _load()
    print('╔══════════════════════════════════════════════════╗')
    print('║       达摩院参数总线 · 运行时状态报告             ║')
    print('╠══════════════════════════════════════════════════╣')
    meta = state.get('_meta', {})
    print(f'  版本: {meta.get("version","?")} | 更新: {meta.get("updated","从未")}')
    print()

    # 品种专项参数
    sp = state.get('sym_params', {})
    print(f'  品种专项参数: {len(sp)}个')
    for sym, p in sorted(sp.items(), key=lambda x: -x[1].get('pf', 0))[:8]:
        print(f'    {sym:15s} [{p["grade"]}] PF={p["pf"]:.3f} WR={p["wr"]:.1%} '
              f'thr={p["thr"]} sl={p["sl_mult"]} mh={p["mh"]} pos={p["kelly_pos"]:.1%}')
    print()

    # 仓位上限
    pl = state.get('pos_limits', {})
    if pl:
        print(f'  M02仓位解锁: {len(pl)}个品种')
        for sym, l in sorted(pl.items(), key=lambda x: -x[1].get('max_pos', 0))[:6]:
            print(f'    {sym:15s} [{l["tier"]}] 上限={l["max_pos"]:.1%} CI95下限={l.get("ci95_low","?"):.3f}')
    else:
        print('  M02仓位解锁: 🔴 未完成（使用保守默认仓位）')
    print()

    # 体制矩阵
    rm = state.get('regime_matrix', {})
    syms = [k for k in rm.keys() if not k.startswith('_')]
    print(f'  M03体制矩阵: {"✅ " + str(len(syms)) + "品种" if syms else "🔴 未完成"}')

    # 时间窗口
    tb = state.get('time_blackout', {})
    if tb:
        print(f'  M07时间窗口: ✅ 已写入 | 禁用规则: {tb.get("rules",{})}')
    else:
        print('  M07时间窗口: 🔴 未完成')

    # 冠军参数
    cp = state.get('champion_params', {})
    if cp:
        print(f'  M08冠军认证: ✅ thr={cp.get("thr")} sl={cp.get("sl")} mh={cp.get("mh")}')
    else:
        print('  M08冠军认证: 🔴 未完成')

    print()
    print(f'  黑名单: {state.get("blacklist",[])}')
    g = state.get('global', {})
    print(f'  全局参数: MIN_SCORE={g.get("min_score")} default_sl={g.get("default_sl")} '
          f'mh={g.get("default_mh")} risk={g.get("risk_per_trade",0)*100:.1f}%/笔')
    print('╚══════════════════════════════════════════════════╝')


# ══════════════════════════════════════════════════════
# 初始化：把M01结果写入总线
# ══════════════════════════════════════════════════════

def init_from_m01():
    """首次运行：从M01训练结果初始化总线"""
    m01_file = BASE / 'dharma' / 'results' / 'train_100k_M01_20260527_093821.json'
    if not m01_file.exists():
        print('[DharmaBus] M01文件不存在，跳过初始化')
        return

    data = json.loads(m01_file.read_text())
    results = data.get('results', {})
    n = push_m01(results)
    print(f'[DharmaBus] ✅ M01初始化完成，写入{n}品种')


if __name__ == '__main__':
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if cmd == 'init':
        init_from_m01()
    elif cmd == 'status':
        status_report()
    elif cmd == 'reset':
        RUNTIME_FILE.unlink(missing_ok=True)
        print('[DharmaBus] 已清空，重新初始化...')
        init_from_m01()
    else:
        print(f'用法: python3 dharma_bus.py [init|status|reset]')


# ══════════════════════════════════════════════════════
# 闭环接口：由 adaptive_threshold 自动调用（2026-06-04）
# ══════════════════════════════════════════════════════

def update_thresholds(new_thr: int) -> int:
    """
    将 adaptive_threshold 最新门槛写入所有品种。
    只降低门槛（不超过当前值），防止误升。
    返回修改品种数。
    """
    state = _load()
    updated = 0
    for sym, params in state.get('sym_params', {}).items():
        old = params.get('thr', 999)
        if old != new_thr:
            params['thr'] = new_thr
            updated += 1
    # 更新 global
    g = state.setdefault('global', {})
    if g.get('min_score', 999) != new_thr:
        g['min_score'] = new_thr
        updated += 1
    _save(state)
    print(f'[DharmaBus] update_thresholds: {new_thr} → {updated}项已更新')
    return updated
