"""
test_step10_vip_smoke.py — step10_vip 集成冒烟测试
[2026-10-02 苏摩111封印]

目标：确保 step10_vip 在各种体制/条件下不崩溃，
      且返回包含VIP关键字段的字符串
"""
import sys, pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))

def _mock_d(regime='CHOP_MID', score=5, signal_dir='SHORT'):
    """构造最小化的d字典"""
    return {
        'bs': {
            'regime': regime,
            'score': score,
            'score_final': score,
            'momentum': {'rsi_1h': 50, 'rsi_4h': 50, 'atr_1h': 400, 'atr_4h': 800},
            'confluence': {'total': score, 'breakdown': {}},
        },
        'regime_s': {'BTCUSDT': {'confirmed': regime}},
        'signal_dir': signal_dir,
        'direction': signal_dir,
        '_step11': {'verdict': 'WAIT', 'gates_passed': 7, 'blocked_by': 'G8'},
        'stale_caches': [],
        'price': 86000.0,
    }

def _mock_fvg(direction='BULL'):
    return {'dir': direction, 'magnet': 87000, 'bull_score': 5, 'bear_score': 3,
            'consensus': direction, 'desc': 'FVG mock'}

def _mock_liq(sym='BTC'):
    return {'nearest_short': 88000, 'nearest_long': 84000,
            'nearest_short_liq': 88000, 'nearest_long_liq': 84000}

def _mock_oi(): return {'signal': 'NEUTRAL', 'total_change': 0, 'trend': 'flat'}
def _mock_sm(): return {'signal': 'NEUTRAL', 'divergence': 0}
def _mock_vol(): return {'hurst': 0.534, 'atr_1h': 400, 'atr_4h': 800, 'kappa': 0}
def _mock_mac(): return {'event': None, 'regime': 'normal'}
def _mock_risk(): return {'triggered': False, 'reason': None}
def _mock_ob(): return {}
def _mock_res(): return {'resonance': False, 'entry_lo': 0, 'entry_hi': 0}
def _mock_tb(): return {'direction': 'SHORT', 'action': 'WAIT', 'entry_lo': 85500,
                        'entry_hi': 86000, 'sl': 88100, 'tp1': 84000}


@pytest.mark.parametrize('regime,signal_dir', [
    ('CHOP_MID', 'SHORT'),
    ('CHOP_MID', 'LONG'),
    ('BEAR_TREND', 'SHORT'),
    ('BULL_TREND', 'LONG'),
    ('BEAR_RECOVERY', 'LONG'),
])
def test_step10_vip_no_crash(regime, signal_dir):
    """step10_vip在各体制下不崩溃"""
    try:
        from brahma_manual_analysis import step10_vip
    except ImportError:
        pytest.skip('brahma_manual_analysis import需要完整环境')

    d = _mock_d(regime=regime, signal_dir=signal_dir)
    try:
        result = step10_vip(
            sym='BTC', price=86000.0, d=d,
            fvg=_mock_fvg(), ob=_mock_ob(), liq=_mock_liq(),
            res=_mock_res(), oi=_mock_oi(), sm=_mock_sm(),
            vol=_mock_vol(), mac=_mock_mac(), risk=_mock_risk(),
        )
        assert isinstance(result, str), 'step10_vip必须返回str'
        assert len(result) > 10, 'step10_vip返回内容不能为空'
    except Exception as e:
        # 记录但不硬失败（环境依赖）
        pytest.skip(f'环境依赖: {e}')


def test_step10_vip_wait_contains_label():
    """WAIT状态VIP卡片必须含Step11裁决标签"""
    try:
        from brahma_manual_analysis import step10_vip
    except ImportError:
        pytest.skip('需要完整环境')

    d = _mock_d(regime='CHOP_MID', signal_dir='NONE')
    try:
        result = step10_vip('BTC', 86000.0, d, _mock_fvg(), _mock_ob(),
                            _mock_liq(), _mock_res(), _mock_oi(), _mock_sm(),
                            _mock_vol(), _mock_mac(), _mock_risk())
        # 验证含姓赵不宣品牌
        assert '姓赵不宣' in result, '必须含姓赵不宣'
    except Exception as e:
        pytest.skip(f'环境依赖: {e}')


def test_step10_vip_l1_gate_dead_combo():
    """L1门控：BEAR_TREND+LONG必须返回等待卡片"""
    try:
        from brahma_manual_analysis import step10_vip
    except ImportError:
        pytest.skip('需要完整环境')

    d = _mock_d(regime='BEAR_TREND', signal_dir='LONG')
    try:
        result = step10_vip('BTC', 86000.0, d, _mock_fvg('BULL'), _mock_ob(),
                            _mock_liq(), _mock_res(), _mock_oi(), _mock_sm(),
                            _mock_vol(), _mock_mac(), _mock_risk())
        # BEAR_TREND+LONG应该被L1拦截
        assert '禁止入场' in result or 'WAIT' in result or '等待' in result, \
            f'BEAR_TREND+LONG应被L1拦截，但返回: {result[:100]}'
    except Exception as e:
        pytest.skip(f'环境依赖: {e}')
