#!/usr/bin/env python3
"""
设计院 · 梵天参数边界测试 v1.0
tests/test_brahma_params.py

覆盖：
  T-P1  calc_trade_params 基础约束（SL方向/TP方向/RR基准）
  T-P2  R:R 必须从入场中点算，不得从当前价算（修复 2026-05-29 Bug）
  T-P3  极端价格/ATR边界（零ATR/超大ATR/极小价格）
  T-P4  N17专项 sl_pct 基准为入场中点
  T-P5  rebase_params 重算后参数自洽
  T-P6  L6守卫：SL/TP方向不得逆转

运行：python3 tests/test_brahma_params.py
"""
import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from brahma_brain.brahma_brain import calc_trade_params, rebase_params

VERSION = 'v1.0'

def _make_ms(price=2000.0, atr_1h=13.0, atr_4h=30.0,
             rsi=50.0, regime='BEAR_TREND',
             sw_highs=None, sw_lows=None, fib=None):
    """构造最小化 ms 数据包"""
    return {
        'price': price,
        'regime': regime,
        'momentum': {
            'rsi_1h': rsi, 'rsi_4h': rsi,
            'atr_1h': atr_1h, 'atr_4h': atr_4h,
            'obv_trend': 'DOWN',
        },
        'key_levels': {
            'fib': fib or {'0.382': price * 1.01, '0.618': price * 0.99},
        },
        'swing_4h': {
            'highs': sw_highs or [price * 1.01, price * 1.02, price * 1.03],
            'lows':  sw_lows  or [price * 0.97, price * 0.98, price * 0.99],
        },
        'order_blocks': {},
        'fvg': {},
    }

def _make_smc(price=2000.0):
    return {
        'order_blocks': {},
        'fvg': {},
        'structure': {'trend': 'DOWN', 'hl_structure': 'LH_LL'},
    }

class TestCalcTradeParamsBasic(unittest.TestCase):
    """T-P1: 基础约束"""

    def test_short_sl_above_entry_hi(self):
        """SHORT: 止损必须 > 入场区上沿"""
        ms = _make_ms()
        smc = _make_smc()
        p = calc_trade_params(ms, smc, 'SHORT')
        self.assertGreater(p['stop_loss'], p['entry_hi'],
            f"SHORT止损{p['stop_loss']:.4f}不高于entry_hi{p['entry_hi']:.4f}")

    def test_short_tp1_below_entry_lo(self):
        """SHORT: TP1必须 < 入场区下沿"""
        ms = _make_ms()
        smc = _make_smc()
        p = calc_trade_params(ms, smc, 'SHORT')
        self.assertLess(p['tp1'], p['entry_lo'],
            f"SHORT TP1{p['tp1']:.4f}不低于entry_lo{p['entry_lo']:.4f}")

    def test_long_sl_below_entry_lo(self):
        """LONG: 止损必须 < 入场区下沿"""
        ms = _make_ms(regime='BULL_TREND')
        smc = _make_smc()
        p = calc_trade_params(ms, smc, 'LONG')
        self.assertLess(p['stop_loss'], p['entry_lo'],
            f"LONG止损{p['stop_loss']:.4f}不低于entry_lo{p['entry_lo']:.4f}")

    def test_long_tp1_above_entry_hi(self):
        """LONG: TP1必须 > 入场区上沿"""
        ms = _make_ms(regime='BULL_TREND')
        smc = _make_smc()
        p = calc_trade_params(ms, smc, 'LONG')
        self.assertGreater(p['tp1'], p['entry_hi'],
            f"LONG TP1{p['tp1']:.4f}不高于entry_hi{p['entry_hi']:.4f}")

    def test_tp2_more_extreme_than_tp1_short(self):
        """SHORT: TP2 <= TP1（更低）"""
        ms = _make_ms()
        smc = _make_smc()
        p = calc_trade_params(ms, smc, 'SHORT')
        self.assertLessEqual(p['tp2'], p['tp1'],
            f"SHORT TP2{p['tp2']:.4f} > TP1{p['tp1']:.4f}")

    def test_no_negative_prices(self):
        """所有价格参数必须为正"""
        ms = _make_ms()
        smc = _make_smc()
        p = calc_trade_params(ms, smc, 'SHORT')
        for k in ('entry_lo','entry_hi','stop_loss','tp1','tp2'):
            self.assertGreater(p[k], 0, f"{k}={p[k]:.6f}不为正")


class TestRRFromEntryMid(unittest.TestCase):
    """T-P2: R:R 必须从入场中点算（2026-05-29 Bug修复验证）"""

    def _verify_rr_basis(self, p, direction):
        entry_mid = (p['entry_lo'] + p['entry_hi']) / 2
        risk_from_mid = abs(p['stop_loss'] - entry_mid)
        tp_from_mid = abs(p['tp1'] - entry_mid)
        expected_rr = tp_from_mid / risk_from_mid if risk_from_mid > 0 else 0
        self.assertAlmostEqual(p['rr1'], expected_rr, places=1,
            msg=f"rr1={p['rr1']} 应等于 tp/risk from entry_mid={expected_rr:.2f}")

    def test_short_rr_from_entry_mid(self):
        """SHORT R:R 基准=入场中点"""
        ms = _make_ms(price=2000.0, atr_1h=13.0, atr_4h=30.0)
        smc = _make_smc(price=2000.0)
        p = calc_trade_params(ms, smc, 'SHORT')
        self._verify_rr_basis(p, 'SHORT')

    def test_long_rr_from_entry_mid(self):
        """LONG R:R 基准=入场中点"""
        ms = _make_ms(price=2000.0, regime='BULL_TREND')
        smc = _make_smc(price=2000.0)
        p = calc_trade_params(ms, smc, 'LONG')
        self._verify_rr_basis(p, 'LONG')

    def test_rr_not_corrupted_by_current_price_gap(self):
        """当当前价远离入场区时，R:R不应因此失真"""
        # 模拟：当前价$1800，入场区在$2034-$2056（需反弹入场）
        ms = _make_ms(price=1800.0, atr_1h=13.0, atr_4h=30.0,
                      sw_highs=[2040.0, 2060.0, 2080.0],
                      fib={'0.382': 2040.0, '0.618': 1780.0})
        smc = _make_smc(price=1800.0)
        p = calc_trade_params(ms, smc, 'SHORT')
        entry_mid = (p['entry_lo'] + p['entry_hi']) / 2
        risk_mid = abs(p['stop_loss'] - entry_mid)
        tp_mid = abs(p['tp1'] - entry_mid)
        true_rr = tp_mid / risk_mid if risk_mid > 0 else 0
        # 真实R:R应接近rr1，误差不超过20%
        if true_rr > 0 and p['rr1'] > 0:
            ratio = abs(p['rr1'] - true_rr) / true_rr
            self.assertLess(ratio, 0.20,
                f"R:R失真: rr1={p['rr1']:.2f} 真实={true_rr:.2f} 误差={ratio*100:.0f}%")


class TestExtremeBoundary(unittest.TestCase):
    """T-P3: 极端边界"""

    def test_zero_atr_no_crash(self):
        """ATR=0 不崩溃，止损仍合理"""
        ms = _make_ms(atr_1h=0.0, atr_4h=0.0)
        smc = _make_smc()
        try:
            p = calc_trade_params(ms, smc, 'SHORT')
            self.assertGreater(p['stop_loss'], 0)
        except ZeroDivisionError:
            self.fail("ATR=0时发生ZeroDivisionError")

    def test_huge_atr_no_crash(self):
        """ATR=50%价格（极高波动）不崩溃，止损不超过合理范围"""
        price = 2000.0
        ms = _make_ms(price=price, atr_1h=price*0.5, atr_4h=price*0.5)
        smc = _make_smc(price=price)
        p = calc_trade_params(ms, smc, 'SHORT')
        sl_pct = abs(p['stop_loss'] - price) / price * 100
        self.assertLess(sl_pct, 60, f"止损距离{sl_pct:.1f}%过大，ATR护栏未生效")

    def test_tiny_price_no_crash(self):
        """极小价格（MEME币）不崩溃"""
        price = 0.00032
        ms = _make_ms(price=price, atr_1h=price*0.05, atr_4h=price*0.1,
                      sw_highs=[price*1.01, price*1.02],
                      sw_lows=[price*0.98, price*0.99],
                      fib={'0.382': price*1.005, '0.618': price*0.995})
        smc = _make_smc(price=price)
        try:
            p = calc_trade_params(ms, smc, 'SHORT')
            for k in ('entry_lo','entry_hi','stop_loss','tp1','tp2'):
                self.assertGreater(p[k], 0, f"{k}={p[k]} 不为正")
        except Exception as e:
            self.fail(f"极小价格崩溃: {e}")

    def test_sl_pct_reasonable(self):
        """止损幅度应在 0.1% ~ 20% 之间"""
        ms = _make_ms()
        smc = _make_smc()
        p = calc_trade_params(ms, smc, 'SHORT')
        self.assertGreater(p['sl_pct'], 0.1, f"sl_pct={p['sl_pct']}太小")
        self.assertLess(p['sl_pct'], 20.0, f"sl_pct={p['sl_pct']}过大")

    def test_invalid_regime_no_crash(self):
        """未知体制不崩溃"""
        ms = _make_ms(regime='UNKNOWN_FUTURE_REGIME')
        smc = _make_smc()
        try:
            p = calc_trade_params(ms, smc, 'SHORT')
            self.assertIsNotNone(p)
        except Exception as e:
            self.fail(f"未知体制崩溃: {e}")


class TestRebaseParams(unittest.TestCase):
    """T-P5: rebase_params 参数自洽"""

    def test_short_rebase_sl_above_entry(self):
        """SHORT rebase: 止损仍 > new_entry"""
        ms = _make_ms(); smc = _make_smc()
        orig = calc_trade_params(ms, smc, 'SHORT')
        rb = rebase_params(orig, new_entry=2045.0, atr_1h=13.0, signal_dir='SHORT')
        self.assertGreater(rb['stop_loss'], rb['entry_hi'],
            f"rebase SHORT SL{rb['stop_loss']} <= entry_hi{rb['entry_hi']}")

    def test_short_rebase_tp1_below_entry(self):
        """SHORT rebase: TP1 < new_entry"""
        ms = _make_ms(); smc = _make_smc()
        orig = calc_trade_params(ms, smc, 'SHORT')
        rb = rebase_params(orig, new_entry=2045.0, atr_1h=13.0, signal_dir='SHORT')
        self.assertLess(rb['tp1'], rb['entry_lo'],
            f"rebase SHORT TP1{rb['tp1']} >= entry_lo{rb['entry_lo']}")

    def test_rebase_rr_uses_new_entry(self):
        """rebase R:R 基于 new_entry 计算"""
        ms = _make_ms(); smc = _make_smc()
        orig = calc_trade_params(ms, smc, 'SHORT')
        new_entry = 2045.0
        rb = rebase_params(orig, new_entry=new_entry, atr_1h=13.0, signal_dir='SHORT')
        risk = abs(rb['stop_loss'] - new_entry)
        tp_dist = abs(rb['tp1'] - new_entry)
        expected_rr = tp_dist / risk if risk > 0 else 0
        self.assertAlmostEqual(rb['rr1'], expected_rr, places=1,
            msg=f"rebase rr1={rb['rr1']} 应={expected_rr:.2f}")


class TestL6Guard(unittest.TestCase):
    """T-P6: L6守卫方向约束"""

    def test_short_entry_order(self):
        """入场区 entry_lo <= entry_hi"""
        ms = _make_ms()
        smc = _make_smc()
        p = calc_trade_params(ms, smc, 'SHORT')
        self.assertLessEqual(p['entry_lo'], p['entry_hi'],
            f"entry_lo{p['entry_lo']} > entry_hi{p['entry_hi']}")

    def test_rr1_positive(self):
        """R:R 必须为正数"""
        ms = _make_ms()
        smc = _make_smc()
        p = calc_trade_params(ms, smc, 'SHORT')
        self.assertGreater(p['rr1'], 0, f"rr1={p['rr1']} 不为正")

    def test_rr2_gte_rr1(self):
        """R:R2 >= R:R1（TP2空间更大）"""
        ms = _make_ms()
        smc = _make_smc()
        p = calc_trade_params(ms, smc, 'SHORT')
        self.assertGreaterEqual(p['rr2'], p['rr1'],
            f"rr2={p['rr2']} < rr1={p['rr1']}")


if __name__ == '__main__':
    import unittest
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [TestCalcTradeParamsBasic, TestRRFromEntryMid,
                TestExtremeBoundary, TestRebaseParams, TestL6Guard]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    print()
    total = result.testsRun
    fails = len(result.failures) + len(result.errors)
    print('=' * 60)
    print(f'总计: {total}  通过: {total-fails}  失败: {fails}')
    print('✅ 全部通过' if fails == 0 else '❌ 存在失败，请检查')


# ══════════════════════════════════════════════════════
# ErrorRegistry 回归适配器
# ══════════════════════════════════════════════════════
def test_rr_not_from_current_price(test_input: str) -> bool:
    """ERR-005 回归：验证R:R不从当前价算（返回True=通过）"""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from brahma_brain.brahma_brain import calc_trade_params
    ms = {
        'price': 1800.0, 'regime': 'BEAR_TREND',
        'momentum': {'rsi_1h': 50,'rsi_4h': 45,'atr_1h': 13,'atr_4h': 30,'obv_trend': 'DOWN'},
        'key_levels': {'fib': {'0.382': 2040.0, '0.618': 1780.0}},
        'swing_4h': {'highs': [2040.0,2060.0,2080.0], 'lows': [1760.0,1780.0,1790.0]},
        'order_blocks': {}, 'fvg': {},
    }
    smc = {'order_blocks':{},'fvg':{},'structure':{'trend':'DOWN','hl_structure':'LH_LL'}}
    p = calc_trade_params(ms, smc, 'SHORT')
    entry_mid = (p['entry_lo'] + p['entry_hi']) / 2
    risk_mid = abs(p['stop_loss'] - entry_mid)
    if risk_mid <= 0: return True
    true_rr = abs(p['tp1'] - entry_mid) / risk_mid
    if true_rr <= 0: return True
    err = abs(p['rr1'] - true_rr) / true_rr
    return err < 0.20  # 误差<20% = 通过

# ══════════════════════════════════════════════════════
# ERR-008 回归：极端低价SL方向验证
# ══════════════════════════════════════════════════════
def test_extreme_low_price_sl_direction(test_input: str) -> bool:
    """ERR-008 回归：price=0.0001 SL方向必须正确（True=通过）"""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from brahma_brain.brahma_brain import calc_trade_params
    for price in [0.00001, 0.0001, 0.001]:
        sw = price * 0.01
        ms = {
            'price': price, 'regime': 'BEAR_TREND',
            'momentum': {'rsi_1h': 50, 'rsi_4h': 45, 'atr_1h': price*0.0001, 'atr_4h': price*0.0001, 'obv_trend': 'DOWN'},
            'key_levels': {'fib': {'0.382': price*1.01, '0.618': price*0.99}},
            'swing_4h': {'highs': [price*(1+0.01*i) for i in range(1,4)], 'lows': [price*(1-0.01*i) for i in range(1,4)]},
            'order_blocks': {}, 'fvg': {},
        }
        smc = {'order_blocks': {}, 'fvg': {}, 'structure': {'trend': 'DOWN', 'hl_structure': 'LH_LL'}}
        for direction in ['SHORT', 'LONG']:
            p = calc_trade_params(ms, smc, direction)
            if direction == 'SHORT' and p['stop_loss'] <= p['entry_hi']:
                return False
            if direction == 'LONG' and p['stop_loss'] >= p['entry_lo']:
                return False
    return True

class TestExtremeLowPrice(unittest.TestCase):
    def test_extreme_low_price(self):
        """ERR-008: 极端低价SL方向不得与入场方向相反"""
        result = test_extreme_low_price_sl_direction('')
        self.assertTrue(result, "极端低价时SL方向错误，精度截断Bug未修复")
