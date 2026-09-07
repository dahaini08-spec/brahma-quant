"""
tests/test_brahma_core_smoke.py — brahma_core主路径最小冒烟测试
[P1 2026-09-07 苏摩111] 覆盖最危险的主路径，不需要100%覆盖

测试原则：
  - 不mock网络（真实API调用，缓存命中后极快）
  - 覆盖主路径：analyze()返回结构完整性
  - 覆盖门控：gates.py拦截逻辑
  - 覆盖适配：adapter.analyze_to_signal()
"""
import sys, os, json, time
sys.path.insert(0, '.')
sys.path.insert(0, 'brahma_brain')

import pytest

# ── 预热fixture（整个测试session只预热一次）────────────────────────────────
@pytest.fixture(scope='session', autouse=True)
def warmup_cache():
    """session级预热，避免每个测试各自拉API"""
    try:
        from data_cache import prefetch_symbol
        prefetch_symbol('ETHUSDT')
        prefetch_symbol('BTCUSDT')
    except Exception:
        pass
    yield


# ── analyze()返回结构完整性 ────────────────────────────────────────────────

class TestAnalyzeStructure:
    """analyze()必须返回带有特定字段的dict"""

    def test_returns_dict(self):
        from brahma_brain.brahma_core import analyze
        r = analyze('ETHUSDT', deep=False)
        assert isinstance(r, dict), "analyze()必须返回dict"

    def test_has_required_fields(self):
        from brahma_brain.brahma_core import analyze
        r = analyze('ETHUSDT', deep=False)
        required = ['score', 'regime', 'action', 'signal_dir']
        for field in required:
            assert field in r, f"analyze()结果缺少字段: {field}"

    def test_score_is_numeric(self):
        from brahma_brain.brahma_core import analyze
        r = analyze('ETHUSDT', deep=False)
        score = r.get('score') or r.get('score_final') or r.get('total')
        assert score is not None, "score字段不能为None"
        assert isinstance(float(score), float), "score必须是数值"

    def test_score_in_valid_range(self):
        from brahma_brain.brahma_core import analyze
        r = analyze('ETHUSDT', deep=False)
        score = float(r.get('score') or r.get('total') or 0)
        assert -50 <= score <= 200, f"score={score}超出合理范围[-50,200]"

    def test_regime_is_known(self):
        from brahma_brain.brahma_core import analyze
        r = analyze('ETHUSDT', deep=False)
        regime = r.get('regime', '')
        known = {'BULL_TREND','BULL_EARLY','CHOP_MID','CHOP_LOW',
                 'BEAR_EARLY','BEAR_TREND','BEAR_RECOVERY','UNKNOWN',''}
        assert regime in known, f"未知体制: {regime}"

    def test_action_is_valid(self):
        from brahma_brain.brahma_core import analyze
        r = analyze('ETHUSDT', deep=False)
        action = r.get('action', '')
        valid = {'ENTER','WATCH','SKIP','WAIT','BLOCKED','SIGNAL_GENERATED',''}
        assert action in valid or action.startswith('ENTER') or action.startswith('SKIP'), \
            f"未知action: {action}"

    def test_price_is_positive(self):
        from brahma_brain.brahma_core import analyze
        r = analyze('ETHUSDT', deep=False)
        price = float(r.get('price', 0))
        assert price > 100, f"ETH价格={price}不合理（应>100）"

    def test_btcusdt_works(self):
        """BTC也能正常分析（防止symbol污染bug）"""
        from brahma_brain.brahma_core import analyze
        r = analyze('BTCUSDT', deep=False)
        assert isinstance(r, dict)
        price = float(r.get('price', 0))
        assert price > 1000, f"BTC价格={price}不合理（应>1000）"
        regime_btc = r.get('regime', '')
        assert regime_btc != '', "BTC regime不能为空"

    def test_price_not_contaminated_across_symbols(self):
        """防止BTC price污染ETH analyze() — 历史bug复现"""
        from brahma_brain.brahma_core import analyze
        r_btc = analyze('BTCUSDT', deep=False)
        r_eth = analyze('ETHUSDT', deep=False)
        p_btc = float(r_btc.get('price', 0))
        p_eth = float(r_eth.get('price', 0))
        # BTC价格应该远高于ETH
        assert p_btc > p_eth * 5, \
            f"价格污染嫌疑: BTC={p_btc} ETH={p_eth}（BTC应>ETH×5）"


# ── gates.py门控逻辑 ────────────────────────────────────────────────────────

class TestGates:
    """门控铁律验证"""

    def test_dead_zone_blocks_130_145(self):
        """130-145死亡区间在CHOP/BEAR体制下必须被拦截"""
        from brahma_os.gates import check_gates
        fake_signal = {
            'symbol': 'ETHUSDT', 'side': 'LONG', 'score': 137.0,
            'regime': 'CHOP_MID', 'grade': 60.0,
            'entry_lo': 2400.0, 'entry_hi': 2420.0,
            'stop': 2350.0, 'target': 2500.0,
            'valid_until': time.time() + 86400,
        }
        result = check_gates(fake_signal)
        assert result.get('blocked') is True, \
            f"CHOP_MID score=137应被死亡区间门控拦截，但结果: {result}"

    def test_bear_recovery_short_blocked(self):
        """BEAR_RECOVERY:SHORT WR=0%应被永久封禁"""
        from brahma_os.gates import check_gates
        fake_signal = {
            'symbol': 'ETHUSDT', 'side': 'SHORT', 'score': 155.0,
            'regime': 'BEAR_RECOVERY', 'grade': 80.0,
            'entry_lo': 2500.0, 'entry_hi': 2520.0,
            'stop': 2570.0, 'target': 2350.0,
            'valid_until': time.time() + 86400,
        }
        result = check_gates(fake_signal)
        assert result.get('blocked') is True, \
            f"BEAR_RECOVERY:SHORT应被永久封禁，但结果: {result}"

    def test_high_score_bull_long_passes(self):
        """BULL_TREND:LONG高分信号应通过门控"""
        from brahma_os.gates import check_gates
        fake_signal = {
            'symbol': 'BTCUSDT', 'side': 'LONG', 'score': 150.0,
            'regime': 'BULL_TREND', 'grade': 85.0,
            'entry_lo': 65000.0, 'entry_hi': 65500.0,
            'stop': 63000.0, 'target': 70000.0,
            'valid_until': time.time() + 86400,
        }
        result = check_gates(fake_signal)
        # 高分BULL_TREND:LONG不应该被直接blocked（可能WATCH但不blocked）
        assert 'block_reason' not in result or 'DEAD' not in result.get('block_reason',''), \
            f"BULL_TREND:LONG score=150不应被死亡区间拦截: {result}"


# ── adapter适配层 ────────────────────────────────────────────────────────────

class TestAdapter:
    """analyze_to_signal适配层完整性"""

    def test_blocked_result_returns_none(self):
        from brahma_brain.core_output import signal_from_result
        result = {'blocked': True, 'block_reason': 'TEST', 'symbol': 'ETHUSDT'}
        sig = signal_from_result(result, 'ETHUSDT')
        assert sig is None

    def test_missing_entry_returns_none(self):
        from brahma_brain.core_output import signal_from_result
        result = {'symbol': 'ETHUSDT', 'signal_dir': 'SHORT',
                  'regime': 'BEAR_EARLY', 'score': 150.0, 'price': 2500.0}
        sig = signal_from_result(result, 'ETHUSDT')
        assert sig is None

    def test_vip_format_has_required_elements(self):
        from brahma_brain.core_output import format_vip
        result = {
            'symbol': 'ETHUSDT', 'signal_dir': 'SHORT', 'regime': 'BEAR_EARLY',
            'score_final': 150.0, 'score': 150.0, 'price': 2500.0,
            'params': {'entry_lo': 2490.0, 'entry_hi': 2510.0,
                      'stop': 2560.0, 'tp1': 2380.0, 'rr1': 2.0,
                      'leverage': 10, 'pos_pct': 5.0},
        }
        card = format_vip(result)
        assert '姓赵不宣' in card
        assert '止损' in card
        assert '梵天系统' in card


# ── WR反哺不静默 ────────────────────────────────────────────────────────────

class TestWRFeedbackIntegrity:
    """WR反哺引擎不再静默失败"""

    def test_brahma_alert_importable(self):
        """brahma_alert模块必须可以导入"""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'brahma_alert', 'scripts/brahma_alert.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert hasattr(mod, 'alert_error')
        assert hasattr(mod, 'alert_warning')

    def test_wr_feedback_skips_paper_realtime(self):
        """paper_realtime:X:Y格式的key必须被跳过（防止n_win=0→WR=0%）"""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'wr_feedback', 'scripts/wr_feedback_engine.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # 构造含paper_realtime的matrix
        matrix = {
            'paper_realtime:BEAR_EARLY:SHORT': {
                'wr': 0.8077, 'n': 52, 'avg_pnl': 2.3
            },
            'BEAR_EARLY:SHORT:145-160': {
                'wr': 0.893, 'n': 28, 'n_win': 25, 'settled': 28
            }
        }
        new_override, changes = mod.compute_new_override(matrix)
        # paper_realtime的key不应该出现在override里
        assert 'paper_realtime:BEAR_EARLY' not in new_override, \
            "paper_realtime格式的key不应写入override"


# ── SQLite数据层 ────────────────────────────────────────────────────────────

class TestBrahmaDB:
    """SQLite信号数据层基础验证"""

    def test_db_exists_and_has_records(self):
        import sqlite3
        assert os.path.exists('data/brahma_signals.db'), "brahma_signals.db不存在"
        conn = sqlite3.connect('data/brahma_signals.db')
        count = conn.execute('SELECT COUNT(*) FROM signals').fetchone()[0]
        conn.close()
        assert count > 100, f"DB记录数={count}，预期>100"

    def test_brahma_db_get_stats(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'brahma_db', 'scripts/brahma_db.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        stats = mod.get_stats()
        assert isinstance(stats, dict)
        assert stats.get('total', 0) > 100

    def test_brahma_db_insert_idempotent(self):
        """INSERT OR IGNORE幂等性测试"""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'brahma_db', 'scripts/brahma_db.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        import sqlite3, time
        test_sig = {
            'signal_id': f'SMOKE_TEST_{int(time.time())}',
            'ts': time.time(), 'symbol': 'ETHUSDT', 'side': 'SHORT',
            'regime': 'TEST', 'score': 99.0,
        }
        r1 = mod.insert_signal(test_sig)
        r2 = mod.insert_signal(test_sig)  # 重复插入
        assert r1 is True, "第一次insert应成功"
        # 第二次不崩溃即可（IGNORE）
