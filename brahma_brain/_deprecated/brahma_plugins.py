#!/usr/bin/env python3
"""
brahma_plugins.py — 梵天v5.0 Plugin层
将smc_engine/regime_scorer/position_sizer封装为v5引擎可用的Plugin

W2封印：苏摩111 2026-09-14
"""

import sys, math, json, os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# 添加brahma_brain到路径
BRAIN = Path(__file__).parent
sys.path.insert(0, str(BRAIN))

# ============================================================
# RegimePlugin — 封装 regime_scorer.py
# ============================================================

class RegimePlugin:
    """
    体制分类Plugin — 接入regime_scorer.py的score()函数
    回测模式：用历史regime labels文件
    实盘模式：调用regime_scorer.score()实时分类
    """
    
    def __init__(self, mode: str = "backtest") -> None:
        self.mode = mode  # "backtest" | "live"
        self._scorer = None
        self._label_cache = {}  # 回测用历史labels缓存
        
        if mode == "live":
            try:
                from regime_scorer import score as regime_score
                self._scorer = regime_score
            except Exception as e:
                print(f"[RegimePlugin] WARN: regime_scorer不可用: {e}")
                self._scorer = None
    
    def load_labels(self, symbol: str, data_dir: str = "data/historical") -> Dict[int, str]:
        """加载历史regime labels（回测模式）"""
        if symbol in self._label_cache:
            return self._label_cache[symbol]
        import gzip
        fname = Path(data_dir) / f"{symbol}_regime_labels.jsonl.gz"
        if not fname.exists():
            return {}
        labels = {}
        with gzip.open(fname, 'rt') as f:
            for l in f:
                l = l.strip()
                if not l: continue
                obj = json.loads(l)
                if isinstance(obj, dict) and 'ts' in obj and 'regime' in obj:
                    labels[obj['ts']] = obj['regime']
        self._label_cache[symbol] = labels
        return labels
    
    def classify(self, symbol: str, ts: int, candles=None) -> str:
        """
        返回体制字符串: BULL_TREND / BULL_EARLY / BEAR_TREND / BEAR_EARLY / BEAR_RECOVERY / CHOP_MID
        """
        if self.mode == "live" and self._scorer:
            try:
                result = self._scorer(symbol)
                primary = result.get('primary', 'CHOP')
                # 映射到6体制
                if primary == 'BULL':
                    phase = result.get('phase', '')
                    return 'BULL_TREND' if 'trend' in str(phase).lower() else 'BULL_EARLY'
                elif primary == 'BEAR':
                    phase = result.get('phase', '')
                    if 'recovery' in str(phase).lower():
                        return 'BEAR_RECOVERY'
                    return 'BEAR_TREND'
                else:
                    return 'CHOP_MID'
            except Exception as _e:
                return 'CHOP_MID'
        else:
            # 回测模式：从历史labels查找
            labels = self.load_labels(symbol)
            return labels.get(ts, None)
    
    def get_direction(self, regime: str) -> int:
        """体制→方向映射"""
        if 'BULL' in regime: return 1
        if 'BEAR' in regime: return -1
        return 0  # CHOP
    
    def get_position_mult(self, regime: str, direction: str) -> float:
        """体制→仓位乘数"""
        mults = {
            'BULL_TREND':    {'long': 1.3, 'short': 0.5},
            'BULL_EARLY':     {'long': 1.0, 'short': 0.5},
            'BEAR_TREND':     {'long': 0.5, 'short': 1.6},
            'BEAR_EARLY':     {'long': 0.5, 'short': 1.2},
            'BEAR_RECOVERY':  {'long': 1.15, 'short': 0.5},
            'CHOP_MID':       {'long': 0.5, 'short': 0.88},
        }
        return mults.get(regime, {'long': 1.0, 'short': 1.0}).get(direction, 1.0)


# ============================================================
# SMCPlugin — 封装 smc_engine.py
# ============================================================

class SMCPlugin:
    """
    SMC结构分析Plugin — 接入smc_engine.py
    提供FVG/OB/清算地图/共振检测
    用于信号增强（不是独立信号源）
    """
    
    def __init__(self, mode: str = "backtest") -> None:
        self.mode = mode
        self._analyze = None
        self._find_fvg = None
        self._find_ob = None
        self._find_liq = None
        
        try:
            import os, sys
            # 从brahma_plugins.py所在目录的父目录加载
            brain_dir = str(Path(__file__).parent)
            project_dir = str(Path(__file__).parent.parent)
            if project_dir not in sys.path:
                sys.path.insert(0, project_dir)
            if brain_dir not in sys.path:
                sys.path.insert(0, brain_dir)
            from smc_engine import analyze_smc, find_fvg, find_order_blocks, find_liquidity_pools
            self._analyze = analyze_smc
            self._find_fvg = find_fvg
            self._find_ob = find_order_blocks
            self._find_liq = find_liquidity_pools
        except Exception as e:
            print(f"[SMCPlugin] WARN: smc_engine不可用: {e}")
    
    def analyze(self, symbol: str, signal_dir: str = 'LONG', interval: str = '1h') -> dict:
        """
        完整SMC分析
        返回: {fvg, order_blocks, liquidity, resonance, score, ...}
        """
        if self._analyze is None:
            return {'available': False}
        try:
            result = self._analyze(symbol, signal_dir=signal_dir, interval=interval)
            result['available'] = True
            return result
        except Exception as e:
            return {'available': False, 'error': str(e)}
    
    def check_resonance(self, symbol: str, signal_dir: str) -> Tuple[bool, float]:
        """
        SMC共振检测
        返回: (has_resonance, resonance_score)
        """
        if self._analyze is None:
            return False, 0.0
        try:
            result = self._analyze(symbol, signal_dir=signal_dir, interval='1h')
            # 检查FVG+OB+清算共振
            fvg = result.get('fvg', [])
            ob = result.get('order_blocks', [])
            liq = result.get('liquidity', [])
            
            score = 0
            if fvg: score += 1
            if ob: score += 1
            if liq: score += 1
            
            return score >= 2, score / 3.0
        except Exception as _e:
            return False, 0.0
    
    def get_fvg_zone(self, symbol: str, direction: str) -> Optional[Tuple[float, float]]:
        """获取最近FVG区间（用于入场精度优化）"""
        if self._find_fvg is None:
            return None
        try:
            result = self._find_fvg(symbol, interval='1h')
            if not result:
                return None
            # 返回最近的FVG区间
            if direction == 'LONG':
                # 找最近的Bull FVG（支撑）
                for fvg in reversed(result):
                    if fvg.get('type') == 'bull':
                        return (fvg.get('low'), fvg.get('high'))
            else:
                for fvg in reversed(result):
                    if fvg.get('type') == 'bear':
                        return (fvg.get('high'), fvg.get('low'))
            return None
        except Exception as _e:
            return None


# ============================================================
# RiskPlugin — 封装 position_sizer.py
# ============================================================

class RiskPlugin:
    """
    仓位风控Plugin — 接入position_sizer.py
    S/B/B+分级 + Kelly + Headroom回撤保护 + 战场对齐
    """
    
    # S/B/B+分级（与v5默认一致）
    TIERS = [
        {'name': 'S',  'sl_max': 0.01, 'pct': 0.05},   # SL<1% → 5%NAV
        {'name': 'B-', 'sl_max': 0.015, 'pct': 0.02},  # SL 1-1.5% → 2%NAV
        {'name': 'B+', 'sl_max': 0.02, 'pct': 0.03},   # SL 1.5-2% → 3%NAV
    ]
    BASE_PCT = 0.10  # SL>2%时基础仓位
    
    # Headroom回撤保护
    HEADROOM = [
        {'dd': 0.05, 'mult': 0.7},   # 回撤5%→×0.7
        {'dd': 0.10, 'mult': 0.5},   # 回撤10%→×0.5
        {'dd': 0.15, 'mult': 0.0},    # 回撤15%→停止
    ]
    
    def __init__(self, mode: str = "backtest") -> None:
        self.mode = mode
        self._get_position = None
        self._compute_sl = None
        self._kelly = None
        self._headroom = None
        self._war_field = None
        
        try:
            from position_sizer import (
                get_position_pct, compute as compute_sl,
                kelly_position, get_headroom_factor, apply_headroom,
                calc_war_field_alignment, get_war_field_position
            )
            self._get_position = get_position_pct
            self._compute_sl = compute_sl
            self._kelly = kelly_position
            self._headroom = get_headroom_factor
            self._apply_headroom = apply_headroom
            self._war_field = calc_war_field_alignment
            self._war_pos = get_war_field_position
        except Exception as e:
            print(f"[RiskPlugin] WARN: position_sizer部分不可用: {e}")
    
    def size(self, entry: float, sl: float, regime: str, direction: str,
             nav: float, peak_nav: float, score: float = 100,
             symbol: str = 'BTCUSDT') -> dict:
        """
        完整仓位计算
        返回: {pct, tier, regime_mult, headroom_mult, final_pct, kelly, reason}
        """
        sl_pct = abs(entry - sl) / entry if entry > 0 else 1.0
        
        # 1. S/B/B+分级
        tier = 'B+'
        base_pct = self.BASE_PCT * (0.02 / max(sl_pct, 0.001))  # SL>2%按比例缩小
        for t in self.TIERS:
            if sl_pct < t['sl_max']:
                tier = t['name']
                base_pct = t['pct']
                break
        
        # 2. 体制乘数
        regime_mults = {
            'BULL_TREND':    {'LONG': 1.3, 'SHORT': 0.5},
            'BULL_EARLY':     {'LONG': 1.0, 'SHORT': 0.5},
            'BEAR_TREND':     {'LONG': 0.5, 'SHORT': 1.6},
            'BEAR_EARLY':     {'LONG': 0.5, 'SHORT': 1.2},
            'BEAR_RECOVERY':  {'LONG': 1.15, 'SHORT': 0.5},
            'CHOP_MID':       {'LONG': 0.5, 'SHORT': 0.88},
        }
        regime_mult = regime_mults.get(regime, {'LONG': 1.0, 'SHORT': 1.0}).get(direction, 1.0)
        
        # 3. Headroom回撤保护
        dd = (peak_nav - nav) / peak_nav if peak_nav > 0 else 0
        headroom_mult = 1.0
        for h in self.HEADROOM:
            if dd >= h['dd']:
                headroom_mult = h['mult']
        
        # 4. Kelly仓位（如果有WR数据）
        kelly = 0.0
        if self._kelly:
            try:
                # 假设WR=0.52, RR=2.9
                kelly = self._kelly(0.52, 2.9, half=True)
            except Exception as _e:
                kelly = 0.0
        
        # 5. 最终仓位
        final_pct = base_pct * regime_mult * headroom_mult
        final_pct = min(final_pct, 0.10)  # 上限10%NAV
        
        # 6. 尝试调用position_sizer的get_position_pct（实盘模式）
        if self.mode == "live" and self._get_position:
            try:
                ps_result = self._get_position(
                    symbol=symbol, score=score, direction=direction,
                    nav=nav, regime=regime, sl_pct=sl_pct
                )
                if ps_result.get('allowed'):
                    # 用position_sizer的结果覆盖（更精确）
                    final_pct = ps_result.get('pct', final_pct) / 100.0
            except Exception as _e:
                print(f"[WARN] brahma_plugins: _e", file=sys.stderr)
        
        reason = f"{tier}档 SL={sl_pct*100:.1f}% × {regime_mult:.1f} × {headroom_mult:.1f}"
        if headroom_mult == 0:
            reason = "回撤>15% 停止交易"
        
        return {
            'pct': final_pct,
            'tier': tier,
            'sl_pct': sl_pct,
            'regime_mult': regime_mult,
            'headroom_mult': headroom_mult,
            'kelly': kelly,
            'base_pct': base_pct,
            'reason': reason,
        }
    
    def compute_sl(self, symbol: str, entry: float, direction: str,
                   regime: str = 'CHOP_MID', score: float = 100,
                   key_levels: list = None) -> dict:
        """
        计算动态止损位（接入position_sizer.compute）
        返回: {sl_price, sl_pct, atr14, atr_mult, reasoning}
        """
        if self._compute_sl:
            try:
                result = self._compute_sl(
                    symbol=symbol, entry_price=entry,
                    signal_dir=direction, regime=regime,
                    score=score, key_levels=key_levels
                )
                return result
            except Exception as _e:
                print(f"[WARN] brahma_plugins: _e", file=sys.stderr)
        
        # 回退：用ATR×2.3
        return {
            'sl_price': None,
            'sl_pct': 0.023,
            'atr_mult': 2.3,
            'reasoning': 'fallback ATR×2.3',
        }


# ============================================================
# 插件管理器
# ============================================================

@dataclass
class PluginManager:
    """管理v5.0的三个核心Plugin"""
    regime: RegimePlugin = None
    smc: SMCPlugin = None
    risk: RiskPlugin = None
    
    @classmethod
    def create(cls, mode: str = "backtest") -> Any:
        """create"""
        return cls(
            regime=RegimePlugin(mode=mode),
            smc=SMCPlugin(mode=mode),
            risk=RiskPlugin(mode=mode),
        )
    
    def is_ready(self) -> bool:
        """判断ready"""
        return all([self.regime, self.smc, self.risk])
    
    def status(self) -> dict:
        """status"""
        return {
            'regime': self.regime is not None,
            'smc': self.smc is not None and self.smc._analyze is not None,
            'risk': self.risk is not None and self.risk._get_position is not None,
            'smc_live': self.smc._analyze is not None if self.smc else False,
            'risk_live': self.risk._get_position is not None if self.risk else False,
        }


if __name__ == '__main__':
    print("=== Plugin状态检查 ===")
    pm = PluginManager.create(mode="backtest")
    print(f"Status: {pm.status()}")
    
    # 测试RegimePlugin
    labels = pm.regime.load_labels("BTCUSDT")
    print(f"RegimePlugin: {len(labels)}个labels")
    
    # 测试SMCPlugin
    smc_status = pm.smc._analyze is not None
    print(f"SMCPlugin: analyze_smc={'✅' if smc_status else '❌'}")
    
    # 测试RiskPlugin
    risk_status = pm.risk._get_position is not None
    risk_local = pm.risk.size(50000, 49000, 'BULL_TREND', 'LONG', 10000, 10000)
    print(f"RiskPlugin: position_sizer={'✅' if risk_status else '❌'} | 本地计算: {risk_local}")
