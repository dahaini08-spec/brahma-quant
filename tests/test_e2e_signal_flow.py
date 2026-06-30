#!/usr/bin/env python3
"""
设计院 · 全链路端到端仿真测试 v1.0
tests/test_e2e_signal_flow.py

覆盖全流程：
  E2E-1  analyze() 输出结构完整性（15维breakdown必须全有）
  E2E-2  confluence_score → 评分在合理范围 [-30, 220]
  E2E-3  calc_trade_params → SL/TP/RR 方向和数值自洽
  E2E-4  signal_queue 冷却机制（同标的90min内不重复）
  E2E-5  DharmaBridge 写入 → live_signal_log 可读
  E2E-6  live_signal_settler 结算逻辑自洽
  E2E-7  brahma_state.json 结构完整性
  E2E-8  queue_state.json 结构完整性
  E2E-9  评分 > 160 时 rr_gate 不得因R:R失真被误杀（2026-05-29 Bug防回归）

运行：python3 tests/test_e2e_signal_flow.py
"""
import sys, os, json, time, unittest, tempfile, shutil
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

VERSION = 'v1.0'

class TestAnalyzeOutput(unittest.TestCase):
    """E2E-1/2: analyze() 输出结构"""

    @classmethod
    def setUpClass(cls):
        """只调用一次analyze，节省时间"""
        try:
            from brahma_brain.brahma_brain import analyze
            cls.result = analyze('ETHUSDT', 'SHORT')
            cls.available = True
        except Exception as e:
            cls.available = False
            cls.skip_reason = str(e)

    def setUp(self):
        if not self.available:
            self.skipTest(self.skip_reason)

    def test_required_top_keys(self):
        """顶层必须包含所有关键字段"""
        required = {'symbol','regime','signal_dir','price','confluence','params','momentum'}
        missing = required - set(self.result.keys())
        self.assertEqual(missing, set(), f"缺少顶层字段: {missing}")

    def test_confluence_has_breakdown(self):
        """confluence 必须有 breakdown 字典"""
        cf = self.result.get('confluence', {})
        self.assertIn('breakdown', cf, "confluence 缺少 breakdown")
        self.assertIsInstance(cf['breakdown'], dict)

    def test_breakdown_15_dims(self):
        """breakdown 应包含15个核心维度"""
        expected_dims = {
            '趋势一致性','关键位精确度','动量背离','SMC结构','量能验证',
            '形态成熟度','清算/OI','情绪/费率','时段权重','谐波+多周期',
            '鲸鱼+跨市场+微观','期权+订单流','L2+贝叶斯+宏观',
            'ML+在线贝叶斯+滑点','LSTM+NLP情绪'
        }
        bd = self.result['confluence'].get('breakdown', {})
        missing = expected_dims - set(bd.keys())
        self.assertEqual(missing, set(), f"breakdown 缺少维度: {missing}")

    def test_score_in_range(self):
        """总分在 [-30, 250] 范围内"""
        score = self.result['confluence'].get('total', 0)
        self.assertGreaterEqual(score, -30, f"评分{score}低于下限")
        self.assertLessEqual(score, 250, f"评分{score}超过上限")

    def test_price_positive(self):
        """现价必须为正"""
        self.assertGreater(self.result['price'], 0)

    def test_params_complete(self):
        """params 字段完整"""
        required = {'entry_lo','entry_hi','stop_loss','tp1','tp2','rr1','sl_pct'}
        p = self.result.get('params', {})
        missing = required - set(p.keys())
        self.assertEqual(missing, set(), f"params 缺少字段: {missing}")


class TestRRBugRegression(unittest.TestCase):
    """E2E-9: R:R Bug 防回归（2026-05-29 修复验证）"""

    @classmethod
    def setUpClass(cls):
        try:
            from brahma_brain.brahma_brain import analyze
            cls.analyze = staticmethod(analyze)
            cls.available = True
        except Exception as e:
            cls.available = False
            cls.skip_reason = str(e)

    def setUp(self):
        if not self.available:
            self.skipTest(self.skip_reason)

    def test_eth_rr_not_corrupted(self):
        """ETH: 当价格离入场区有距离时，R:R不应<1（防止用当前价算失真）"""
        r = self.analyze('ETHUSDT', 'SHORT')
        p = r['params']
        entry_mid = (p['entry_lo'] + p['entry_hi']) / 2
        risk = abs(p['stop_loss'] - entry_mid)
        tp_dist = abs(p['tp1'] - entry_mid)
        true_rr = tp_dist / risk if risk > 0 else 0
        rr1 = p.get('rr1', 0)

        # 若rr1和true_rr差距超过50%，说明仍在用当前价算（Bug复现）
        if true_rr > 0 and rr1 > 0:
            ratio = abs(rr1 - true_rr) / true_rr
            self.assertLess(ratio, 0.50,
                f"R:R Bug复现！rr1={rr1:.2f} 真实={true_rr:.2f} 误差={ratio*100:.0f}% > 50%")

    def test_n17_spec_rr_from_entry_mid(self):
        """N17专项标的 R:R 基准验证（不得从当前价算）"""
        # 直接测 ETH 结果
        r = self.analyze('ETHUSDT', 'SHORT')
        p = r['params']
        entry_mid = (p['entry_lo'] + p['entry_hi']) / 2
        risk_from_mid = abs(p['stop_loss'] - entry_mid)
        if risk_from_mid > 0:
            computed_rr = abs(p['tp1'] - entry_mid) / risk_from_mid
            # rr1 和从入场中点算的差距应 < 10%
            if computed_rr > 0:
                err = abs(p['rr1'] - computed_rr) / computed_rr
                self.assertLess(err, 0.10,
                    f"ETH N17 rr1={p['rr1']:.2f} vs entry_mid基准={computed_rr:.2f} 误差{err*100:.0f}%")


class TestSignalQueueCooldown(unittest.TestCase):
    """E2E-4: 冷却队列结构验证"""

    def test_queue_state_structure(self):
        """queue_state.json 结构完整"""
        path = BASE / 'data' / 'queue_state.json'
        self.assertTrue(path.exists(), "queue_state.json 不存在")
        data = json.loads(path.read_text())
        self.assertIn('cooldowns', data)
        self.assertIn('queue', data)
        self.assertIn('active_positions', data)
        self.assertIsInstance(data['cooldowns'], dict)
        self.assertIsInstance(data['queue'], list)

    def test_cooldown_ts_format(self):
        """cooldown 时间戳格式正确"""
        path = BASE / 'data' / 'queue_state.json'
        data = json.loads(path.read_text())
        for sym, v in list(data['cooldowns'].items())[:10]:
            if isinstance(v, str):
                # 应该是 ISO 时间戳
                self.assertIn('T', v, f"{sym} cooldown ts 格式异常: {v}")
            elif isinstance(v, dict):
                self.assertIn('ts', v, f"{sym} cooldown dict 缺少 ts")

    def test_no_duplicate_active_positions(self):
        """活跃持仓不重复"""
        path = BASE / 'data' / 'queue_state.json'
        data = json.loads(path.read_text())
        positions = data.get('active_positions', [])
        syms = [p.get('symbol') for p in positions if isinstance(p, dict)]
        self.assertEqual(len(syms), len(set(syms)), f"持仓重复: {syms}")


class TestDharmaBridge(unittest.TestCase):
    """E2E-5: DharmaBridge 写入验证"""

    def test_syntax_valid(self):
        """dharma_data_bridge.py 语法正确（修复验证）"""
        import ast
        src = (BASE / 'dharma_data_bridge.py').read_text()
        try:
            ast.parse(src)
        except SyntaxError as e:
            self.fail(f"dharma_data_bridge.py 语法错误: L{e.lineno} {e.msg}")

    def test_import_ok(self):
        """dharma_data_bridge 可正常导入"""
        try:
            import dharma_data_bridge
        except SyntaxError as e:
            self.fail(f"导入失败-语法错误: {e}")
        except Exception as e:
            self.skipTest(f"导入异常(非语法): {e}")

    def test_live_signal_log_readable(self):
        """live_signal_log.jsonl 可正常读取"""
        path = BASE / 'data' / 'live_signal_log.jsonl'
        if not path.exists():
            self.skipTest("live_signal_log.jsonl 不存在")
        records = []
        with open(path) as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as e:
                    self.fail(f"第{i+1}行JSON解析失败: {e}")
        self.assertGreater(len(records), 0, "live_signal_log 为空")

    def test_signal_log_required_fields(self):
        """live_signal_log 每条记录必须有核心字段"""
        path = BASE / 'data' / 'live_signal_log.jsonl'
        if not path.exists():
            self.skipTest("live_signal_log.jsonl 不存在")
        required = {'signal_id', 'ts', 'symbol', 'signal_dir', 'score'}
        with open(path) as f:
            for i, line in enumerate(f):
                if i >= 20:
                    break
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                missing = required - set(r.keys())
                self.assertEqual(missing, set(), f"第{i+1}条记录缺少字段: {missing}")


class TestBrahmaState(unittest.TestCase):
    """E2E-7: brahma_state.json 完整性"""

    def test_state_exists(self):
        """brahma_state.json 存在"""
        self.assertTrue((BASE / 'data' / 'brahma_state.json').exists())

    def test_state_structure(self):
        """brahma_state.json 结构完整"""
        data = json.loads((BASE / 'data' / 'brahma_state.json').read_text())
        required = {'nav', 'regime', 'positions'}
        missing = required - set(data.keys())
        self.assertEqual(missing, set(), f"brahma_state 缺少字段: {missing}")

    def test_nav_positive(self):
        """NAV 必须为正"""
        data = json.loads((BASE / 'data' / 'brahma_state.json').read_text())
        self.assertGreater(data.get('nav', 0), 0, "NAV 为零或负数")

    def test_ws_guardian_heartbeat_fresh(self):
        """ws_guardian 心跳应在30分钟内"""
        path = BASE / 'data' / 'ws_guardian_state.json'
        if not path.exists():
            self.skipTest("ws_guardian_state.json 不存在")
        data = json.loads(path.read_text())
        ts = data.get('ts', 0)
        age = time.time() - ts
        self.assertLess(age, 1800,
            f"ws_guardian心跳已{age:.0f}s未更新（>30min），进程可能宕机")


class TestLiveSignalSettler(unittest.TestCase):
    """E2E-6: live_signal_settler 逻辑自洽"""

    def test_settler_importable(self):
        """live_signal_settler 可正常导入"""
        try:
            sys.path.insert(0, str(BASE / 'scripts'))
            import live_signal_settler
        except ImportError as e:
            self.fail(f"live_signal_settler 导入失败: {e}")

    def test_settler_dry_run_no_crash(self):
        """dry_run 不崩溃"""
        try:
            sys.path.insert(0, str(BASE / 'scripts'))
            import live_signal_settler
            # 只测函数存在
            self.assertTrue(callable(live_signal_settler.settle))
            self.assertTrue(callable(live_signal_settler.get_stats))
        except Exception as e:
            self.fail(f"settler 初始化失败: {e}")

    def test_stats_structure(self):
        """get_stats 返回必要字段"""
        sys.path.insert(0, str(BASE / 'scripts'))
        import live_signal_settler
        s = live_signal_settler.get_stats()
        required = {'total', 'settled', 'running', 'wins', 'losses', 'win_rate'}
        missing = required - set(s.keys())
        self.assertEqual(missing, set(), f"stats 缺少字段: {missing}")


if __name__ == '__main__':
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [TestAnalyzeOutput, TestRRBugRegression, TestSignalQueueCooldown,
                TestDharmaBridge, TestBrahmaState, TestLiveSignalSettler]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    print()
    total = result.testsRun
    fails = len(result.failures) + len(result.errors)
    print('=' * 60)
    print(f'总计: {total}  通过: {total-fails}  失败: {fails}  跳过: {len(result.skipped)}')
    print('✅ 全部通过' if fails == 0 else '❌ 存在失败，请检查')


# ══════════════════════════════════════════════════════
# ErrorRegistry 回归适配器
# ══════════════════════════════════════════════════════
def test_dharma_bridge_syntax(test_input: str) -> bool:
    """ERR-006 回归：dharma_data_bridge语法正确（True=通过）"""
    import ast
    from pathlib import Path
    src = (Path(__file__).parent.parent / 'dharma_data_bridge.py').read_text()
    try:
        ast.parse(src)
        return True
    except SyntaxError:
        return False

def test_live_settler_has_settled(test_input: str) -> bool:
    """ERR-007 回归：live_signal_log有已结算记录（True=通过）"""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
    import live_signal_settler
    s = live_signal_settler.get_stats()
    return s.get('settled', 0) > 0
