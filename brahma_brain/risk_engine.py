"""
risk_engine.py — 梵天独立风控引擎
设计院封印 2026-09-12 苏摩111

职责：
  1. Pre-trade risk gate：信号生成后、执行前的最终风控检查
  2. Portfolio risk：BTC+ETH同方向相关性调整
  3. Kill switch：单日-5%NAV全平
  4. 整合已有组件：circuit_breaker / drawdown_tracker / antifragile_guard / guardrails

设计原则（40年交易员视角）：
  - 风控独立于alpha/decision，有最终否决权
  - 只加2条新规则：①单日-5%NAV全平 ②同方向仓位×0.7
  - 不改现有风控逻辑，只做facade整合
  - 风控不复杂化——越复杂越容易在极端情况下失效

接入位置：
  - brahma_brain/risk_engine.py（本文件）
  - trader_brain.decide() 返回后、auto_executor执行前调用
  - auto_executor.py 在execute()中调用risk_engine.check()

调用链路：
  trader_brain.decide() → signal
  risk_engine.check(signal) → (approved, modified_signal, reasons)
  auto_executor.execute(modified_signal) → order
"""
import json, time, sys
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

# Kill switch配置
KILL_SWITCH_DAILY_LOSS_PCT = 5.0   # 单日亏损>5%NAV → 全平
CORRELATION_ADJUST_FACTOR = 0.7     # BTC+ETH同方向 → 仓位×0.7
MAX_PORTFOLIO_VAR_PCT = 15.0        # 组合VaR ≤ 15%NAV


def check(signal: dict, portfolio: dict = None) -> dict:
    """
    独立风控检查 — 信号生成后、执行前的最终gate
    
    参数：
      signal: trader_brain.decide()的返回（含direction/position_pct/leverage/sl等）
      portfolio: 当前持仓信息（可选，缺省时从positions_state.json读取）
    
    返回：
      {
        'approved': bool,         # 是否通过风控
        'modified': dict,         # 修改后的signal（仓位调整等）
        'reasons': list[str],     # 风控原因列表
        'kill_switch': bool,      # kill switch是否触发
        'warnings': list[str],    # 警告（不否决但需注意）
      }
    """
    reasons = []
    warnings = []
    kill_switch = False
    modified = dict(signal)
    
    # ── 1. Kill Switch：单日亏损检查 ──────────────────────
    try:
        nav_data = _get_daily_nav()
        if nav_data['daily_pnl_pct'] <= -KILL_SWITCH_DAILY_LOSS_PCT:
            kill_switch = True
            reasons.append(f'Kill switch: 单日亏损{nav_data["daily_pnl_pct"]:.1f}%≤{-KILL_SWITCH_DAILY_LOSS_PCT}%NAV → 全平')
    except Exception as e:
        warnings.append(f'Kill switch检查失败: {e}')
    
    # ── 2. 回撤追踪器检查（已有组件）──────────────────────
    try:
        from brahma_brain.drawdown_tracker import check_can_execute as _dd_check
        dd_ok, dd_reason = _dd_check(signal)
        if not dd_ok:
            reasons.append(f'回撤门控: {dd_reason}')
    except Exception as e:
        warnings.append(f'回撤检查失败: {e}')
    
    # ── 3. 熔断器检查（已有组件）──────────────────────────
    try:
        from brahma_brain.circuit_breaker import BrahmaCircuitRegistry
        registry = BrahmaCircuitRegistry.get()
        if registry.has_open_breakers():
            open_breakers = [k for k, v in registry.status_all().items() if v.get('state') == 'OPEN']
            reasons.append(f'熔断器OPEN: {open_breakers}')
    except Exception as e:
        warnings.append(f'熔断器检查失败: {e}')
    
    # ── 4. 反脆弱守卫检查（已有组件）──────────────────────
    try:
        from brahma_brain.antifragile_guard import full_guard_check
        ag = full_guard_check(signal.get('symbol', 'BTCUSDT'), signal.get('direction', ''))
        if ag.get('halt'):
            reasons.append(f'反脆弱halt: {ag.get("reason", "")}')
        # 反脆弱仓位乘数
        ag_mult = ag.get('size_multiplier', 1.0)
        if ag_mult < 1.0:
            _apply_position_mult(modified, ag_mult, '反脆弱降仓')
    except Exception as e:
        warnings.append(f'反脆弱检查失败: {e}')
    
    # ── 5. 组合相关性检查（新增）──────────────────────────
    try:
        corr_result = _check_correlation(signal, portfolio)
        if corr_result['adjust']:
            _apply_position_mult(modified, CORRELATION_ADJUST_FACTOR, corr_result['reason'])
            warnings.append(corr_result['reason'])
    except Exception as e:
        warnings.append(f'组合相关性检查失败: {e}')
    
    # ── 6. Guardrails Layer 9-12检查（已有组件）───────────
    try:
        from guardrails.layer_9_12 import check_layer9_12
        l912 = check_layer9_12(
            signal.get('symbol', 'BTCUSDT'),
            signal.get('price', 0),
            signal.get('direction', ''),
        )
        if l912.get('halt'):
            reasons.append(f'Guardrail halt: {l912.get("reason", "")}')
    except Exception as e:
        warnings.append(f'Guardrail检查失败: {e}')
    
    # ── 最终决策 ─────────────────────────────────────────
    approved = len(reasons) == 0 and not kill_switch
    
    return {
        'approved': approved,
        'modified': modified,
        'reasons': reasons,
        'kill_switch': kill_switch,
        'warnings': warnings,
    }


def _apply_position_mult(signal: dict, mult: float, reason: str):
    """应用仓位乘数"""
    orig_pct = signal.get('position_pct', 0)
    new_pct = orig_pct * mult
    signal['position_pct'] = round(new_pct, 4)
    if 'risk_adjustments' not in signal:
        signal['risk_adjustments'] = []
    signal['risk_adjustments'].append(f'{reason}: {orig_pct:.2f}%→{new_pct:.2f}% (×{mult})')


def _get_daily_nav() -> dict:
    """获取当日NAV和PnL"""
    try:
        nav_path = BASE / 'data' / 'nav_history.jsonl'
        if not nav_path.exists():
            return {'daily_pnl_pct': 0, 'nav': 0}
        
        lines = nav_path.read_text().strip().split('\n')
        if len(lines) < 2:
            return {'daily_pnl_pct': 0, 'nav': 0}
        
        # 今天的NAV
        today = lines[-1]
        today_nav = json.loads(today).get('nav', 0)
        
        # 找今天最早的一条作为基准
        today_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        base_nav = today_nav
        for line in reversed(lines):
            r = json.loads(line)
            if r.get('time', '').startswith(today_date):
                base_nav = r.get('nav', base_nav)
                break
        
        daily_pnl_pct = ((today_nav - base_nav) / base_nav * 100) if base_nav > 0 else 0
        return {
            'daily_pnl_pct': round(daily_pnl_pct, 2),
            'nav': today_nav,
            'base_nav': base_nav,
        }
    except Exception:
        return {'daily_pnl_pct': 0, 'nav': 0}


def _check_correlation(signal: dict, portfolio: dict = None) -> dict:
    """
    BTC+ETH同方向相关性检查
    如果当前已持有BTC多单，新信号是ETH多单 → 仓位×0.7
    """
    symbol = signal.get('symbol', '').upper()
    direction = signal.get('direction', '').upper()
    
    if not symbol or not direction:
        return {'adjust': False, 'reason': ''}
    
    # 读取当前持仓
    if portfolio is None:
        portfolio = _load_portfolio()
    
    # 检查相关性对
    corr_pairs = {
        'BTCUSDT': 'ETHUSDT',
        'ETHUSDT': 'BTCUSDT',
    }
    counterpart = corr_pairs.get(symbol, '')
    if not counterpart:
        return {'adjust': False, 'reason': ''}
    
    # 检查对手标的是否有同方向持仓
    counter_pos = portfolio.get(counterpart, {})
    counter_dir = str(counter_pos.get('direction', '')).upper()
    counter_size = float(counter_pos.get('position_pct', 0) or 0)
    
    if counter_dir == direction and counter_size > 0:
        return {
            'adjust': True,
            'reason': f'组合相关性: {counterpart}已有{direction}仓{counter_size:.1f}% → 仓位×{CORRELATION_ADJUST_FACTOR}',
        }
    
    return {'adjust': False, 'reason': ''}


def _load_portfolio() -> dict:
    """加载当前持仓状态"""
    try:
        p_path = BASE / 'data' / 'positions_state.json'
        if p_path.exists():
            return json.loads(p_path.read_text())
    except Exception:
        pass
    return {}


def execute_kill_switch(reason: str) -> dict:
    """
    Kill switch触发时执行全平
    返回执行结果
    """
    result = {
        'triggered': True,
        'reason': reason,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'actions': [],
    }
    
    try:
        # 调用emergency_close.py全平
        import subprocess
        proc = subprocess.run(
            ['python3', str(BASE / 'emergency_close.py'), '--all', '--reason', f'kill_switch:{reason}'],
            capture_output=True, text=True, timeout=30,
        )
        result['actions'].append(f'emergency_close: {proc.stdout[:200]}')
        if proc.returncode != 0:
            result['actions'].append(f'error: {proc.stderr[:200]}')
    except Exception as e:
        result['actions'].append(f'emergency_close failed: {e}')
    
    # 保存kill switch记录
    try:
        ks_path = BASE / 'data' / 'kill_switch_log.jsonl'
        with open(ks_path, 'a') as f:
            f.write(json.dumps(result, ensure_ascii=False) + '\n')
    except Exception:
        pass
    
    return result


def get_status() -> dict:
    """风控引擎状态报告"""
    status = {
        'kill_switch_active': False,
        'daily_pnl': 0,
        'open_breakers': [],
        'dd_status': 'OK',
        'antifragile_status': 'OK',
        'portfolio_correlation': 'N/A',
    }
    
    try:
        nav = _get_daily_nav()
        status['daily_pnl'] = nav['daily_pnl_pct']
        status['kill_switch_active'] = nav['daily_pnl_pct'] <= -KILL_SWITCH_DAILY_LOSS_PCT
    except Exception:
        pass
    
    try:
        from brahma_brain.circuit_breaker import BrahmaCircuitRegistry
        registry = BrahmaCircuitRegistry.get()
        status['open_breakers'] = [k for k, v in registry.status_all().items() if v.get('state') == 'OPEN']
    except Exception:
        pass
    
    try:
        from brahma_brain.drawdown_tracker import get_status_report
        status['dd_status'] = get_status_report()[:200]
    except Exception:
        pass
    
    return status


if __name__ == '__main__':
    import sys
    print("=== Risk Engine Status ===")
    s = get_status()
    for k, v in s.items():
        print(f"  {k}: {v}")
    print()
    
    # Test with a sample signal
    test_signal = {
        'symbol': 'BTCUSDT',
        'direction': 'SHORT',
        'position_pct': 1.0,
        'leverage': 3,
        'price': 77000,
        'sl': 82000,
        'action': 'ENTER',
    }
    print("=== Test Signal ===")
    print(json.dumps(test_signal, indent=2))
    print()
    result = check(test_signal)
    print("=== Risk Check Result ===")
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
