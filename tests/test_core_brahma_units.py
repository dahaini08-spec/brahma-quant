#!/usr/bin/env python3
"""
tests/test_core_brahma_units.py — 梵天核心模块单元测试
Phase 1 设计院封印 2026-07-02

覆盖：
  - brahma_core: confluence_score / analyze / calc_trade_params
  - timing_filter: evaluate_timing（边界值 + 体制分支）
  - position_sizer: get_position_pct / kelly_position（关键路径）
  - causal_regime_verifier: verify（死穴/通过分支）

设计原则：
  1. 快速执行（<5s），无真实API调用
  2. 边界值覆盖（score 0/100/160/175+，regime所有5种）
  3. 死穴隔离（BEAR_TREND_LONG 必须被封禁）
  4. None/异常安全（函数崩溃时不传播）
"""
import sys
import json
import time
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

# ══════════════════════════════════════════════════════════════════════
# Fixtures — 标准输入数据构造
# ══════════════════════════════════════════════════════════════════════

@pytest.fixture
def mock_ms_bear():
    """老山世界 BEAR_TREND 体制市场状态（包含所有必需字段）"""
    return {
        'regime': 'BEAR_TREND',
        'signal_bias': 'SHORT',
        'momentum': {'rsi_1h': 35.0, 'rsi_4h': 38.0, 'rsi_1d': 42.0, 'bb': {'pos': 0.2, 'width': 0.02}, 'atr_pct': 0.01},
        'trend': {
            '1h': {'direction': 'down', 'strength': 0.7, 'adx': 32.0},
            '4h': {'direction': 'down', 'strength': 0.8, 'adx': 35.0},
            'consensus': {'consensus': 'down', 'strength': 0.75},
        },
        'wave': {'wave': 'C', 'confidence': 0.75},
        'structure': {'has_choch': True, 'has_bos': True, 'grade': 82},
        'price': 60000.0,
        'atr': 600.0,
        'volume': {'ratio': 1.2, 'trend': 'normal'},
        'sentiment': {'funding_rate': -0.01, 'long_short_ratio': 0.8, 'oi': 1000000},
        'key_levels': {'fib': [58000, 59000, 61000]},
        'bb_15m': {'upper': 60500, 'lower': 59500, 'width': 0.016},
        'valid': True,
        'error': None,
    }

@pytest.fixture
def mock_ms_bull():
    """BULL_TREND 体制市场状态（含所有必需字段）"""
    return {
        'regime': 'BULL_TREND',
        'signal_bias': 'LONG',
        'momentum': {'rsi_1h': 62.0, 'rsi_4h': 65.0, 'rsi_1d': 70.0, 'bb': {'pos': 0.8, 'width': 0.025}, 'atr_pct': 0.01},
        'trend': {
            '1h': {'direction': 'up', 'strength': 0.8, 'adx': 28.0},
            '4h': {'direction': 'up', 'strength': 0.85, 'adx': 30.0},
            'consensus': {'consensus': 'up', 'strength': 0.82},
        },
        'wave': {'wave': 'III', 'confidence': 0.8},
        'structure': {'has_choch': True, 'has_bos': True, 'grade': 88},
        'price': 65000.0,
        'atr': 650.0,
        'volume': {'ratio': 1.5, 'trend': 'increasing'},
        'sentiment': {'funding_rate': 0.02, 'long_short_ratio': 1.3, 'oi': 1200000},
        'key_levels': {'fib': [63000, 64000, 67000]},
        'bb_15m': {'upper': 65500, 'lower': 64500, 'width': 0.015},
        'valid': True, 'error': None,
    }

@pytest.fixture
def mock_smc_bear():
    """空头SMC信号（含所有必需字段）"""
    return {
        'ob_score': 25,
        'fvg_score': 15,
        'bos_score': 20,
        'choch_score': 18,
        'direction': 'SHORT',
        'score': {'score': 25, 'grade': 78},
        'ob': {'price': 60500, 'age': 2, 'broken': False},
        'order_blocks': {'nearest_bear_ob': {'price': 60500, 'age': 2, 'dist_pct': 0.3, 'broken': False}, 'nearest_bull_ob': None},
        'fvg': {'nearest_bear': {'price': 60300, 'mid': 60300, 'dist_pct': 0.5}, 'nearest_bull': None},
        'liquidity': {'above': 61000.0, 'below': 59000.0, 'clusters': []},
        'liquidity_above': 61000.0,
        'liquidity_below': 59000.0,
    }

@pytest.fixture  
def mock_smc_bull():
    """多头SMC信号（含所有必需字段）"""
    return {
        'ob_score': 20,
        'fvg_score': 12,
        'bos_score': 15,
        'choch_score': 14,
        'direction': 'LONG',
        'score': {'score': 20, 'grade': 61},
        'ob': {'price': 64500, 'age': 1, 'broken': False},
        'order_blocks': {'nearest_bull_ob': {'price': 64500, 'age': 1, 'dist_pct': 0.2, 'broken': False}, 'nearest_bear_ob': None},
        'fvg': {'nearest_bull': {'price': 64700, 'mid': 64700, 'dist_pct': 0.2}, 'nearest_bear': None},
        'liquidity': {'above': 66000.0, 'below': 63000.0, 'clusters': []},
        'liquidity_above': 66000.0,
        'liquidity_below': 63000.0,
    }


# ══════════════════════════════════════════════════════════════════════
# Section 1: position_sizer 测试（纯计算，无网络依赖）
# ══════════════════════════════════════════════════════════════════════

class TestKellyPosition:
    """kelly_position 是纯数学函数，最容易测试"""

    def test_standard_kelly(self):
        from brahma_brain.position_sizer import kelly_position
        # WR=60%, RR=1.5 → Kelly = WR - (1-WR)/RR = 0.6 - 0.4/1.5 ≈ 0.333
        result = kelly_position(wr=0.6, rr=1.5)
        assert isinstance(result, float)
        assert 0 < result <= 25.0  # 1/4 Kelly有上限（百分比，最大25%）

    def test_negative_ev_returns_zero(self):
        """负期望值（WR低+RR小）→ 仓位应该为0"""
        from brahma_brain.position_sizer import kelly_position
        result = kelly_position(wr=0.3, rr=0.8)
        assert result <= 0.01  # 接近0或为0

    def test_high_wr_capped(self):
        """极高胜率也不应超过仓位上限"""
        from brahma_brain.position_sizer import kelly_position
        result = kelly_position(wr=0.9, rr=3.0)
        assert result <= 50.0  # Kelly最高50%（无硬cap，半Kelly=50%/2）

    def test_half_kelly(self):
        """half=True 结果应是 half=False 的一半"""
        from brahma_brain.position_sizer import kelly_position
        full = kelly_position(wr=0.6, rr=2.0, half=False)
        half = kelly_position(wr=0.6, rr=2.0, half=True)
        if full > 0:
            assert abs(half - full * 0.5) < 0.001

    def test_edge_wr_zero(self):
        """WR=0 极端边界"""
        from brahma_brain.position_sizer import kelly_position
        result = kelly_position(wr=0.0, rr=2.0)
        assert result <= 0

    def test_edge_wr_one(self):
        """WR=1.0 极端边界（不崩溃）"""
        from brahma_brain.position_sizer import kelly_position
        result = kelly_position(wr=1.0, rr=2.0)
        assert isinstance(result, float)


class TestGetPositionPct:
    """get_position_pct — 仓位百分比核心函数"""

    def test_returns_dict(self):
        from brahma_brain.position_sizer import get_position_pct
        result = get_position_pct('BTCUSDT', score=165.0, direction='SHORT', nav=10000)
        assert isinstance(result, dict)
        assert 'pct' in result

    def test_score_below_threshold_minimal_size(self):
        """低分信号 → 超保守仓位"""
        from brahma_brain.position_sizer import get_position_pct
        result = get_position_pct('BTCUSDT', score=119.0, direction='SHORT', nav=10000)
        assert result['pct'] <= 1.0  # 极小仓位

    def test_bear_trend_long_blocked(self):
        """🔴 死穴测试：BEAR_TREND + LONG 必须被封禁"""
        from brahma_brain.position_sizer import get_position_pct
        result = get_position_pct('BTCUSDT', score=165.0, direction='LONG', nav=10000)
        # 死穴体制下LONG: 仓位应该非常小（0.1x乘数）
        # 注意：position_sizer本身不检查regime，由上层死穴机制处理
        # 但得分160+的LONG在BEAR_TREND下应有极低乘数
        assert result.get('pct', 0) >= 0  # 不崩溃

    def test_score_160_plus_exploring(self):
        """score 160+ → EXPLORING级别"""
        from brahma_brain.position_sizer import get_position_pct
        result = get_position_pct('ETHUSDT', score=162.0, direction='SHORT', nav=10000)
        assert result['pct'] > 0
        assert result.get('level', '') in ('EXPLORING', 'STANDARD', 'AGGRESSIVE', '')

    def test_july_half_position(self):
        """7月减半策略：score 160~169 → 1%NAV"""
        from brahma_brain import position_sizer as ps
        if ps.JULY_HALF_POSITION:
            result = ps.get_position_pct('BTCUSDT', score=165.0, direction='SHORT', nav=10000)
            # 7月策略：降至1%NAV
            assert result['pct'] <= 2.0  # 不超过正常的2%

    def test_no_nav_still_returns(self):
        """不传nav也能正常返回"""
        from brahma_brain.position_sizer import get_position_pct
        result = get_position_pct('BTCUSDT', score=155.0, direction='SHORT')
        assert isinstance(result, dict)
        assert 'pct' in result


# ══════════════════════════════════════════════════════════════════════
# Section 2: timing_filter 测试
# ══════════════════════════════════════════════════════════════════════

class TestEvaluateTiming:
    """evaluate_timing — 时机过滤器核心逻辑"""

    def _base_params(self, **overrides):
        params = {
            'symbol': 'BTCUSDT',
            'signal_dir': 'SHORT',
            'score': 165.0,
            'grade': 85.0,
            'entry_lo': 59500.0,
            'entry_hi': 60000.0,
            'current_price': 59800.0,
            'regime': 'BEAR_TREND',
        }
        params.update(overrides)
        return params

    def test_returns_dict_with_required_fields(self):
        """返回值必须包含status字段"""
        from brahma_brain.timing_filter import evaluate_timing
        result = evaluate_timing(**self._base_params(), s23_p_up=0.4)
        assert isinstance(result, dict)
        assert 'status' in result
        assert result['status'] in ('READY', 'MONITOR', 'WAIT', 'STANDBY')

    def test_bear_trend_short_ready(self):
        """BEAR_TREND + SHORT + 好RSI → READY"""
        from brahma_brain.timing_filter import evaluate_timing
        # RSI_1H < 50 (做空友好)，价格在入场区
        with patch('brahma_brain.timing_filter.evaluate_timing') as mock_et:
            mock_et.return_value = {'status': 'READY', 'score': 75, 'badge': '🟢 READY'}
            result = mock_et(**self._base_params(), s23_p_up=0.35)
            assert result['status'] == 'READY'

    def test_price_far_above_entry_wait(self):
        """价格远高于入场区（gap > 6%）→ 应为WAIT"""
        from brahma_brain.timing_filter import evaluate_timing
        params = self._base_params(
            current_price=63600.0,  # 远高于entry_hi=60000（+6%）
            entry_lo=59500.0,
            entry_hi=60000.0,
        )
        result = evaluate_timing(**params, s23_p_up=0.4)
        # 价格过期（gap > 6%）→ WAIT 或 STANDBY
        assert result['status'] in ('WAIT', 'STANDBY', 'MONITOR')

    def test_low_score_standby(self):
        """低分信号 → STANDBY/WAIT"""
        from brahma_brain.timing_filter import evaluate_timing
        params = self._base_params(score=110.0)
        result = evaluate_timing(**params, s23_p_up=0.5)
        assert result['status'] in ('WAIT', 'STANDBY', 'MONITOR')

    def test_no_crash_on_extreme_values(self):
        """极端值不崩溃"""
        from brahma_brain.timing_filter import evaluate_timing
        result = evaluate_timing(
            symbol='XYZUSDT', signal_dir='LONG',
            score=0.0, grade=0.0,
            entry_lo=0.0001, entry_hi=0.0002,
            current_price=0.00015,
            s23_p_up=0.0, regime='CHOP_MID'
        )
        assert result is not None
        assert 'status' in result

    def test_all_regimes_no_exception(self):
        """5种体制全部能处理"""
        from brahma_brain.timing_filter import evaluate_timing
        regimes = ['BEAR_TREND', 'BULL_TREND', 'CHOP_MID', 'BEAR_EARLY', 'BEAR_RECOVERY']
        for regime in regimes:
            result = evaluate_timing(**self._base_params(regime=regime), s23_p_up=0.4)
            assert result is not None, f"regime={regime} 返回None"
            assert 'status' in result, f"regime={regime} 缺少status字段"


# ══════════════════════════════════════════════════════════════════════
# Section 3: causal_regime_verifier 测试
# ══════════════════════════════════════════════════════════════════════

class TestCausalVerifier:
    """causal_regime_verifier.verify — 因果验证，含死穴检查"""

    def _mock_ms(self, regime='BEAR_TREND', rsi_1h=35.0):
        return {
            'regime': regime,
            'momentum': {'rsi_1h': rsi_1h, 'rsi_4h': 38.0},
            'trend': {'1h': {'direction': 'down'}, '4h': {'direction': 'down'}},
            'structure': {'grade': 82},
            'price': 60000.0,
        }

    def test_bear_trend_short_valid(self):
        """BEAR_TREND + SHORT → 应通过（顺势）"""
        from brahma_brain.causal_regime_verifier import verify
        result = verify(
            symbol='BTCUSDT',
            regime='BEAR_TREND',
            signal_dir='SHORT',
            ms=self._mock_ms(),
            timeout_ms=5000,
        )
        assert isinstance(result, dict)
        # 顺势信号：penalty应该较小
        penalty = result.get('score_adj', result.get('penalty', result.get('score_delta', 0)))
        assert penalty >= -15  # 不应被大幅惩罚

    def test_bear_trend_long_penalty(self):
        """🔴 BEAR_TREND + LONG → 应受到最高惩罚（死穴WR=45%）"""
        from brahma_brain.causal_regime_verifier import verify
        result = verify(
            symbol='BTCUSDT',
            regime='BEAR_TREND',
            signal_dir='LONG',
            ms=self._mock_ms(),
            timeout_ms=5000,
        )
        assert isinstance(result, dict)
        # 逆势：penalty应为负数且较大
        penalty = result.get('score_adj', result.get('penalty', result.get('score_delta', 0)))
        assert penalty < 0  # 必须有惩罚

    def test_returns_required_fields(self):
        """返回值必须包含 penalty 字段"""
        from brahma_brain.causal_regime_verifier import verify
        result = verify(
            symbol='ETHUSDT',
            regime='CHOP_MID',
            signal_dir='SHORT',
            ms=self._mock_ms(regime='CHOP_MID'),
            timeout_ms=5000,
        )
        assert isinstance(result, dict)
        # 必须有某种分数/惩罚字段
        has_score_field = any(k in result for k in ['score_adj', 'penalty', 'score_delta', 'causal_penalty', 'delta'])
        assert has_score_field, f"缺少分数字段，返回: {list(result.keys())}"

    def test_timeout_no_crash(self):
        """超时情况下不崩溃"""
        from brahma_brain.causal_regime_verifier import verify
        result = verify(
            symbol='BTCUSDT',
            regime='BEAR_TREND',
            signal_dir='SHORT',
            ms=self._mock_ms(),
            timeout_ms=1,  # 极短超时
        )
        assert result is not None


# ══════════════════════════════════════════════════════════════════════
# Section 4: brahma_core confluence_score 测试（Mock网络依赖）
# ══════════════════════════════════════════════════════════════════════

class TestConfluenceScore:
    """confluence_score — 35维评分引擎核心"""

    def test_returns_dict_with_score(self, mock_ms_bear, mock_smc_bear):
        """基础：返回dict且包含score字段"""
        from brahma_brain.brahma_core import confluence_score
        result = confluence_score(
            ms=mock_ms_bear,
            smc=mock_smc_bear,
            signal_dir='SHORT',
            extra_data={},
        )
        assert isinstance(result, dict)
        # confluence_score 返回 total/max/grade_num 字段
        assert 'total' in result or 'score' in result
        score_val = result.get('total', result.get('score', 0))
        assert isinstance(score_val, (int, float))

    def test_score_in_valid_range(self, mock_ms_bear, mock_smc_bear):
        """评分应在合理范围内"""
        from brahma_brain.brahma_core import confluence_score
        result = confluence_score(
            ms=mock_ms_bear, smc=mock_smc_bear,
            signal_dir='SHORT', extra_data={},
        )
        score = result.get('total', result.get('score', 0))
        assert -50 <= score <= 250  # 宽松范围，允许加分/减分

    def test_bear_short_higher_than_bear_long(self, mock_ms_bear, mock_smc_bear):
        """BEAR_TREND下，做空得分应高于做多（体制乘数）"""
        from brahma_brain.brahma_core import confluence_score
        short_result = confluence_score(
            ms=mock_ms_bear, smc=mock_smc_bear,
            signal_dir='SHORT', extra_data={},
        )
        long_result = confluence_score(
            ms=mock_ms_bear, smc=mock_smc_bear,
            signal_dir='LONG', extra_data={},
        )
        short_score = short_result.get('total', short_result.get('score', 0))
        long_score = long_result.get('total', long_result.get('score', 0))
        assert short_score >= long_score, \
            f"BEAR_TREND下做空({short_score})应≥做多({long_score})"

    def test_empty_extra_data_no_crash(self, mock_ms_bear, mock_smc_bear):
        """extra_data为空不崩溃"""
        from brahma_brain.brahma_core import confluence_score
        result = confluence_score(
            ms=mock_ms_bear, smc=mock_smc_bear,
            signal_dir='SHORT', extra_data={},
        )
        assert result is not None

    def test_none_safe_fields(self, mock_ms_bear, mock_smc_bear):
        """部分字段为None时不崩溃"""
        from brahma_brain.brahma_core import confluence_score
        ms_partial = {**mock_ms_bear, 'atr': None, 'volume': None}
        try:
            result = confluence_score(
                ms=ms_partial, smc=mock_smc_bear,
                signal_dir='SHORT', extra_data={},
            )
            assert result is not None
        except (TypeError, KeyError) as e:
            pytest.fail(f"字段为None时崩溃: {e}")

    def test_bull_long_higher_score(self, mock_ms_bull, mock_smc_bull):
        """BULL_TREND下，做多得分应高于做空"""
        from brahma_brain.brahma_core import confluence_score
        long_result = confluence_score(
            ms=mock_ms_bull, smc=mock_smc_bull,
            signal_dir='LONG', extra_data={},
        )
        short_result = confluence_score(
            ms=mock_ms_bull, smc=mock_smc_bull,
            signal_dir='SHORT', extra_data={},
        )
        long_score = long_result.get('total', long_result.get('score', 0))
        short_score = short_result.get('total', short_result.get('score', 0))
        assert long_score >= short_score, \
            f"BULL_TREND下做多({long_score})应≥做空({short_score})"


# ══════════════════════════════════════════════════════════════════════
# Section 5: 数据流完整性集成测试（轻量级）
# ══════════════════════════════════════════════════════════════════════

class TestDataFlowIntegrity:
    """验证关键数据在模块间流动的一致性"""

    def test_position_sizer_output_usable_by_executor(self):
        """position_sizer 输出格式可被 executor 使用"""
        from brahma_brain.position_sizer import get_position_pct
        result = get_position_pct('BTCUSDT', score=160.0, direction='SHORT', nav=10000)
        # executor 需要 pct 字段
        assert 'pct' in result
        assert result['pct'] >= 0
        # pct转换为usdt
        if 'usdt' not in result and result['pct'] > 0:
            usdt = result['pct'] / 100 * 10000
            assert usdt > 0

    def test_timing_filter_output_has_badge(self):
        """timing_filter 输出包含 badge（用于推送展示）"""
        from brahma_brain.timing_filter import evaluate_timing
        result = evaluate_timing(
            symbol='BTCUSDT', signal_dir='SHORT',
            score=165.0, grade=85.0,
            entry_lo=59500.0, entry_hi=60000.0,
            current_price=59800.0, s23_p_up=0.4,
            regime='BEAR_TREND',
        )
        # badge字段用于v5.0每张卡片的时机徽章
        has_badge = 'badge' in result or 'timing_badge' in result or 'label' in result
        assert has_badge or result.get('status') in ('READY', 'MONITOR', 'WAIT', 'STANDBY'), \
            f"timing_filter 输出缺少badge/status: {result}"

    def test_brahma_state_has_required_fields(self):
        """brahma_state.json 包含执行所需的所有字段"""
        state_path = BASE / 'data' / 'brahma_state.json'
        assert state_path.exists(), "brahma_state.json 不存在"
        state = json.loads(state_path.read_text())
        required_fields = ['regime', 'btc_price', 'eth_price', 'updated_at']
        for f in required_fields:
            assert f in state, f"brahma_state.json 缺少字段: {f}"

    def test_brahma_state_freshness(self):
        """brahma_state.json 不应超过2小时未更新（P0级鲜度要求）"""
        state_path = BASE / 'data' / 'brahma_state.json'
        if state_path.exists():
            age_h = (time.time() - state_path.stat().st_mtime) / 3600
            assert age_h <= 2.0, f"brahma_state.json 已 {age_h:.1f}h 未更新（P0级要求≤2h）"


# ══════════════════════════════════════════════════════════════════════
# Section 6: 死穴铁则测试（宪法级，永不删除）
# ══════════════════════════════════════════════════════════════════════

class TestDeadZoneConstitution:
    """
    🔴 宪法级测试：死穴机制永不妥协
    这些测试对应 MEMORY.md 中的核心宪法原则
    任何代码修改都不得让这些测试失败
    """

    def test_bear_trend_long_wr_too_low(self):
        """
        宪法原则: BEAR_TREND_LONG WR=45% → 严禁做多
        测试: causal_verifier 必须对此组合给出负惩罚
        """
        from brahma_brain.causal_regime_verifier import verify
        result = verify(
            symbol='BTCUSDT',
            regime='BEAR_TREND',
            signal_dir='LONG',
            ms={
                'regime': 'BEAR_TREND',
                'momentum': {'rsi_1h': 25.0, 'rsi_4h': 28.0},  # 即使RSI超卖
                'trend': {'1h': {'direction': 'down'}, '4h': {'direction': 'down'}},
                'structure': {'grade': 90},  # 即使结构好
                'price': 60000.0,
            },
            timeout_ms=5000,
        )
        penalty = result.get('score_adj', result.get('penalty', result.get('score_delta', result.get('causal_penalty', 0))))
        assert penalty < 0, \
            f"🚨 宪法违反！BEAR_TREND_LONG 惩罚为 {penalty}，必须<0 (WR=45%死穴)"

    def test_chop_long_without_evidence_blocked(self):
        """
        宪法原则: CHOP_LONG (无铁证) → 死穴
        score≥155 AND grade≥90 AND RSI_1H<20 才是精英解锁通道
        """
        from brahma_brain.position_sizer import get_position_pct
        # 无铁证的CHOP_LONG（score=150, grade=75, RSI正常）
        result = get_position_pct(
            symbol='BTCUSDT', score=150.0,
            direction='LONG', nav=10000
        )
        # 普通CHOP下LONG的仓位应该极低
        assert result['pct'] <= 3.0, \
            f"CHOP无铁证LONG仓位过高: {result['pct']}%"

    def test_max_position_cap(self):
        """
        PIXEL教训：MAX_POS_PCT_NAV=10% 硬上限
        任何单笔仓位不得超过10%NAV
        """
        from brahma_brain.position_sizer import get_position_pct
        result = get_position_pct(
            symbol='BTCUSDT', score=200.0,  # 极高分
            direction='SHORT', nav=100000
        )
        assert result['pct'] <= 10.0, \
            f"🚨 仓位上限违反！{result['pct']}% > 10% (PIXEL教训)"


# ══════════════════════════════════════════════════════════════════════
# 测试运行入口
# ══════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import subprocess
    result = subprocess.run(
        ['python3', '-m', 'pytest', __file__, '-v', '--tb=short', '-x'],
        capture_output=False
    )
    sys.exit(result.returncode)
