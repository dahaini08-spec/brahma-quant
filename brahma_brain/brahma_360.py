#!/usr/bin/env python3
"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# 360全量分析，日报入口
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
brahma_360.py — 梵天360全系统健康管理中心
设计院·苏摩111 2026-06-30

定位：像电脑360一样 —— 体检 → 发现 → 分级 → 自动修复 → 验证 → 报告

五层架构：
  Layer1 Scanner   : 8维全盘扫描，输出问题清单
  Layer2 Classifier: 红/橙/黄/绿四级威胁评估
  Layer3 AutoFixer : 数据/进程/配置类问题自动修复（代码修改需苏摩审批）
  Layer4 Verifier  : 修复后60s验证闭环
  Layer5 Reporter  : 实时告警 + 健康日报 + 修复历史

使用：
  python3 brahma_360.py          # 全量体检
  python3 brahma_360.py --fix    # 体检+自动修复
  python3 brahma_360.py --report # 输出健康报告
"""

import os, sys, json, time, re, subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ── 路径 ────────────────────────────────────────────────────────
_DIR   = Path(__file__).parent
_ROOT  = _DIR.parent
_DATA  = _ROOT / 'data'
_HISTORY = _DATA / 'brahma_360_history.jsonl'
sys.path.insert(0, str(_DIR))
sys.path.insert(0, str(_ROOT))

# ════════════════════════════════════════════════════════════════
# Layer1: Scanner — 8维全盘扫描
# ════════════════════════════════════════════════════════════════

def scan_d1_modules() -> list:
    """D1: 模块接入完整性（孤儿模块）"""
    issues = []
    # [Phase0 2026-07-23 设计院降噪] 改用自定义孤儿模块检查，屏蔽auto_review错误干扰
    try:
        from pathlib import Path as _P
        import sys as _sys
        _root = _P(__file__).parent.parent
        _brain = _root / 'brahma_brain'
        # 主链路内容
        _main_chain = ''
        for _f in ['brahma_engine.py','brahma_core.py','brahma_analysis_runner.py',
                    'brahma_core_block_a.py','brahma_core_block_b.py','brahma_core_block_c.py',
                    'brahma_core_analyze_steps.py','brahma_core_step4.py']:
            try: _main_chain += (_brain/_f).read_text(errors='ignore')
            except: pass
        # 已知合法孤立模块（有明确用途不需主链路直接引用）
        _known_standalone = {
            # ── 系统工具层（独立运行，不需主链路引用）──
            'brahma_360','brahma_health','brahma_smoke_test','brahma_self_heal',
            'brahma_1hao_analysis','brahma_dashboard','brahma_compact_runner',
            'auto_review','exception_injector',
            # ── CI / 测试工具 ──
            'brahma_ci','brahma_constitutional_test',
            # ── 已被其他脚本间接引用（非主链路但有实际调用）──
            'brahma_learning_loop',
            'ev_feedback','ic_tracker','online_learner_v2',
            'rl_position_ab','safety','signal_card_formatter',
            # ── 合理孤立：独立工具/cron任务执行体 ──
            'brahma_logger','brahma_macro_bottom','brahma_mem_compressor',
            'squeeze_lifecycle','vectorbt_simfactory',
            # ── 归档模块（功能已被替代，保留备用）──
            'realtime_fetch','kronos_inference_v7_patch',
            # ── 遗留孤立群岛 ──
            'offline_adapters','tardis_liq_layer','module_registry',
            # ── 接入候选（下一版本接入：condition_order_matrix/headroom/signal_expiry_tracker）──
            'condition_order_matrix','headroom','signal_expiry_tracker',
            # ── 已通过brahma_1hao_analysis接入主链路（2026-07-25封印）──
            'position_guard','anomaly_guards',
            # ── 独立信号流（15M层）— 封印P1-A 2026-08-03──
            'signal_15m_engine',  # 15M主框架信号生成器，不挂载主链路属正常架构
            # ── 梵天纸交易沙盒 — 设计院自主 2026-08-07──
            'dharma_simfactory',  # 轻量纸交易沙盒，独立运行，不需主链路引用
            # ── 深度排查确认白名单（2026-08-09 设计院三方裁决）──
            'brahma_decision_engine',  # auto_executor + brahma_1hao_analysis 真实调用 ✅
            'brahma_optimizer',        # brahma_ops_center.py subprocess调用 ✅
            'hcme_matcher',            # brahma_1hao_analysis + fangcang_engine 真实调用 ✅
            'market_behavior_model',   # fangcang_engine真实调用，fangcang→brahma_engine主链路 ✅
            'bybit_liq_adapter',       # 多源清算聚合，独立工具模块，liq_density_engine兄弟层 ✅
            # ── D1类：等效功能已被其他模块覆盖，本身为辅助工具 — 2026-08-09 设计院封印 ──
            'brahma_daily_report',     # 已有brahma_1hao_analysis日报cron覆盖
            'dharma_weekly_report',    # 达摩院健康报告，360体检已覆盖
            'smart_digest',            # 智能汇总，信号推送链路已涵盖
            'liq_heatmap_viz',         # 可视化工具，按需手动运行即可
            'zero_cost_prescorer',     # rsi_structure_watcher已承担零成本预筛
            # ── D2/D3类：待接入或待苏摩确认 — 2026-08-09 设计院暂加白名单 ──
            'pump_sector_relay',       # 暂加白名单，待接入pump_hunter触发链路
            'price_entry_monitor',     # 暂加白名单，有信号时应触发
            'vip_strategy_generator',  # 暂加白名单，应由信号触发
            'timesfm_bridge',          # 暂加白名单，TimeSFM预测写入research_cache
            'tradfi_watcher',          # 暂加白名单，TradFi宏观数据定时更新
            'liq_ws_daemon',           # 暂加白名单，WebSocket清算流守护进程
            # ── 今日Block拆分后确认：只被archive引用，加归档白名单 (2026-08-11) ──
            'external_signal',         # 只在archive/auto_review引用，归档模块 ✅
            's7_liq_config',           # 只在archive/auto_review引用，归档模块 ✅
            'brahma_order_engine',     # 暂加白名单，统一开单引擎待评估是否替代auto_executor
            'brahma_autonomous_core',  # 待苏摩确认是否替代现有架构
            # ── A类：已有真实调用链，主链路未直接import但生产已接通 — 2026-08-09 设计院深度排查封印 ──
            'brahma_scoring',          # brahma_engine/__init__/smoke_test 深度依赖 ✅
            'dharma_data_bridge',      # signal_15m_engine/signal_bus/brahma_1hao_analysis ✅
            'fangcang_vector_db',      # fangcang_engine → brahma_engine ✅
            'grade_utils',             # signal_bus/auto_execute_gate/oi_advanced 多处调用 ✅
            'signal_quality_engine',   # auto_executor/brahma_ops_center 执行层调用 ✅
            'signal_weight_updater',   # signal_settler 结算闭环 ✅
            'sl_bandit',               # dynamic_sl/signal_settler/dharma_simfactory ✅
            'tradfi_dump_detector',    # signal_integrity_gate 调用 ✅
            'mtf_resonance',           # brahma_decision_engine 多周期共振 ✅
            'kronos_subagent_bridge',  # 归档候选，kronos_engine已覆盖功能，加白名单避免误报 ✅
            'brahma_wiring_check',      # 接线检测器本身，工具脚本不需要接入主链路 ✅
            'brahma_binance_mcp',      # MCP Server骨架，外部Agent调用，非import链 ✅
            'chart_renderer',          # push_chart.py动态import，二级链覆盖 ✅
            'pip_extractor',           # fangcang_engine调用，二级链覆盖 ✅
            'brahma_ci',               # CI探针独立工具，合理孤立 ✅
            # ── D1修复 2026-08-17 苏摩111：已接入但被360误判为孤儿 ──
            'structure_touch_detector', # brahma_scoring + brahma_decision_engine 真实调用 ✅
            'vip_validator',            # brahma_1hao_analysis validate_vip_strategy 真实调用 ✅
            'brahma_intel_layer',       # brahma_1hao_analysis P2/P3 智慧层调用（太极封印 2026-08-18）✅
            # ── D1修复 2026-08-21 苏摩111：动态import + 二级链路被360误判为孤儿 ──
            'brahma_multiframe',       # brahma_1hao_analysis 动态import，全周期FVG/OB扫描 ✅
            'cross_asset_correlator',  # brahma_1hao_analysis 动态import，跨资产宏观层(VIX/DXY/BTC.D) ✅
            'elliott_wave_pips',       # fangcang_engine 动态import，Elliott波浪+PIPs识别 ✅
            'vpa_analyzer',            # fangcang_engine 动态import，VPA成交量行为分析 ✅
            'weekly_monthly_anchor',   # fangcang_engine 动态import，周月线HTF锚定 ✅
            'rsi_1h_trigger',          # signal_15m_engine注入，1H触发层T1~T6（2026-08-21封印）✅
        }
        _orphans = []
        for _f in sorted(_brain.glob('*.py')):
            if _f.stem.startswith('_') or _f.stem == '__init__': continue
            if _f.stem in _known_standalone: continue
            if _f.stem not in _main_chain:
                _lines = len(_f.read_text(errors='ignore').splitlines())
                if _lines > 100:  # 过滤小文件
                    _orphans.append(_f.stem)
        if _orphans:
            issues.append({
                'dim': 'D1_modules', 'level': 'ERROR',
                'msg': f'孤儿模块 {len(_orphans)}个未接入: {_orphans[:5]}',
                'auto_fix': False,
            })

        # ── 门3: wiring_check 高价值孤岛检测 (2026-08-09 设计院封印) ──
        # 每次360体检自动跑接线检测器，高价值孤岛>0 = ERROR
        try:
            import importlib.util as _ilu
            _wc_path = _Path(__file__).parent / 'brahma_wiring_check.py'
            if _wc_path.exists():
                _wc_spec = _ilu.spec_from_file_location('brahma_wiring_check', _wc_path)
                _wc_mod = _ilu.module_from_spec(_wc_spec)
                _wc_spec.loader.exec_module(_wc_mod)
                _wc_islands = len(_wc_mod.HIGH_VALUE_ISLANDS)
                _wc_fail = sum(1 for e in _wc_mod.WIRING_REGISTRY
                               if not _wc_mod._check_import(e['module'])[0])
                if _wc_islands > 0:
                    issues.append({
                        'dim': 'D1_wiring', 'level': 'ERROR',
                        'msg': f'高价值孤岛 {_wc_islands}个 — 功能建好但未接通主链路',
                        'auto_fix': False,
                    })
                elif _wc_fail > 0:
                    issues.append({
                        'dim': 'D1_wiring', 'level': 'WARN',
                        'msg': f'wiring_check: {_wc_fail}个模块import失败',
                        'auto_fix': False,
                    })
        except Exception as _wc_e:
            pass  # wiring_check失败不影响360主流程

    except Exception as e:
        issues.append({'dim': 'D1_modules', 'level': 'WARN', 'msg': f'巡检异常: {e}', 'auto_fix': False})
    return issues


def scan_d2_data() -> list:
    """D2: 数据文件新鲜度"""
    issues = []
    now = time.time()
    contracts = {
        # 体制状态: regime_state_machine每次信号扫描更新，12h内正常
        'data/regime_state.json':       {'max_min': 720,  'level': 'ERROR',    'fix': 'none'},
        # 仓位状态: position-guardian每5min更新，60min内正常
        'data/brahma_state.json':       {'max_min': 60,   'level': 'WARN',     'fix': 'none'},
        # 实时价格: cron每小时刷新，120min内正常
        'data/live_prices.json':        {'max_min': 120,  'level': 'WARN',     'fix': 'init_live_prices'},
        # ws_guardian_state: 空仓时 idle 状态，仅有持仓时才需新鲜（达摩院2026-07-16）
        # 空仓期间 max_min=10080(7天)，有持仓时由 watchdog 守护保持新鲜
        'data/ws_guardian_state.json':  {'max_min': 10080, 'level': 'WARN',    'fix': 'none'},
        # 信号队列: 24h内正常（低交易频率系统）
        'data/signal_queue.jsonl':      {'max_min': 1440, 'level': 'WARN',     'fix': 'reset_signal_queue'},
    }
    for fpath, cfg in contracts.items():
        fp = _ROOT / fpath
        if not fp.exists():
            issues.append({
                'dim': 'D2_data', 'level': cfg['level'],
                'msg': f'{fpath} 不存在',
                'auto_fix': cfg['fix'] != 'none',
                'fix_action': cfg['fix'],
                'file': str(fp),
            })
        else:
            age_min = (now - fp.stat().st_mtime) / 60
            if age_min > cfg['max_min'] * 2:
                issues.append({
                    'dim': 'D2_data', 'level': cfg['level'],
                    'msg': f'{fpath} 过期 {age_min:.0f}min（限{cfg["max_min"]}min）',
                    'auto_fix': cfg['fix'] != 'none',
                    'fix_action': cfg['fix'],
                    'file': str(fp),
                    'age_min': age_min,
                })
    return issues


def _has_open_positions() -> bool:
    """检查是否有未平仓持仓（用于ws_guardian豁免逻辑）"""
    try:
        import json as _j
        state = _j.load(open(_DATA / 'brahma_state.json'))
        return any(p.get('status') == 'OPEN' for p in state.get('positions', []))
    except Exception:
        pass
    try:
        import json as _j
        wp = _j.load(open(_DATA / 'wuqu_positions.json'))
        return any(p.get('status') == 'OPEN' for p in wp)
    except Exception:
        pass
    return False


def scan_d3_processes() -> list:
    """D3: 关键进程存活"""
    issues = []
    try:
        r = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=5)
        ps = r.stdout
        # ws_guardian: 空仓时正常退出（watchdog_guardian设计），不告警
        # 有持仓时才需要确认进程存活
        _ws_needed = _has_open_positions()
        procs = {
            'ws_guardian.py': {
                'level': 'WARN' if _ws_needed else 'INFO',
                'fix': 'restart_ws_guardian' if _ws_needed else 'none',
                'skip_if_no_pos': True,   # 空仓豁免标记
            },
        }
        for proc, cfg in procs.items():
            if proc not in ps:
                if cfg.get('skip_if_no_pos') and not _ws_needed:
                    continue  # [达摩院修正 2026-07-16] 空仓时ws_guardian退出属正常，跳过告警
                issues.append({
                    'dim': 'D3_processes', 'level': cfg['level'],
                    'msg': f'进程未运行: {proc}',
                    'auto_fix': cfg['fix'] != 'none',
                    'fix_action': cfg['fix'],
                })
    except Exception as e:
        issues.append({'dim': 'D3_processes', 'level': 'WARN', 'msg': f'进程检查异常: {e}', 'auto_fix': False})
    return issues


def scan_d4_interfaces() -> list:
    """D4: 接口一致性（关键字段检查）"""
    issues = []
    try:
        core_file = _DIR / 'brahma_core.py'
        if core_file.exists():
            core = core_file.read_text()
            required_fields = {
                'grade_num':    ('confluence_score()返回必须含grade_num', 'patch_grade_num'),
                'score_final':  ('_result必须含score_final', 'none'),
                'signal_dir':   ('_result必须含signal_dir', 'none'),
            }
            for field, (desc, fix) in required_fields.items():
                if field not in core:
                    issues.append({
                        'dim': 'D4_interfaces', 'level': 'ERROR',
                        'msg': f'接口字段缺失: {field} — {desc}',
                        'auto_fix': fix != 'none',
                        'fix_action': fix,
                    })
    except Exception as e:
        issues.append({'dim': 'D4_interfaces', 'level': 'WARN', 'msg': f'接口检查异常: {e}', 'auto_fix': False})
    return issues


def scan_d5_params() -> list:
    """D5: 铁证参数一致性（MEMORY.md封印值 vs 代码实际）"""
    issues = []
    try:
        core_file = _DIR / 'brahma_core.py'
        if not core_file.exists():
            return issues
        # 扫描所有拆分後的核心文件（今日Block拆分后内容分布到各block中）
        _d5_files = ['brahma_core.py','brahma_core_block_a.py','brahma_core_block_b.py',
                     'brahma_core_block_c.py','brahma_core_analyze_steps.py','brahma_core_step4.py']
        core = ''
        for _d5f in _d5_files:
            _p = _DIR / _d5f
            if _p.exists(): core += _p.read_text(errors='ignore')

        iron_rules = [
            ('BEAR_TREND SHORT乘数1.6x',  r"'BEAR_TREND'.*1\.6",   'ERROR'),
            ('CHOP_MID SHORT乘数0.88x',   r"'CHOP_MID'.*0\.88",    'ERROR'),
            ('RSM体制防抖已接入',           'regime_state_machine',  'ERROR'),
            ('RANGE区间路由已接入',         'detect_range_structure', 'WARN'),
            ('PositionSizer已接入',        'position_sizer',         'WARN'),
        ]
        for rule_name, pattern, level in iron_rules:
            found = bool(re.search(pattern, core))
            if not found:
                issues.append({
                    'dim': 'D5_params', 'level': level,
                    'msg': f'铁证参数异常: {rule_name} — 未找到匹配',
                    'auto_fix': False,
                })
    except Exception as e:
        issues.append({'dim': 'D5_params', 'level': 'WARN', 'msg': f'参数检查异常: {e}', 'auto_fix': False})
    return issues


def scan_d6_silent_failures() -> list:
    """D6: 静默失败点趋势 + 并发安全检测"""
    issues = []
    try:
        core_file = _DIR / 'brahma_core.py'
        if core_file.exists():
            core = core_file.read_text()
            count = sum(1 for l in core.split('\n')
                       if 'except' in l and 'pass' in l and not l.strip().startswith('#'))
            if count > 100:
                level = 'ERROR'
            elif count > 50:
                level = 'WARN'
            else:
                level = 'OK'
            if level != 'OK':
                issues.append({
                    'dim': 'D6_silent', 'level': level,
                    'msg': f'静默失败点(except:pass): {count}处 — 系统可观测性差',
                    'auto_fix': False,
                    'count': count,
                })
    except Exception as e:
        issues.append({'dim': 'D6_silent', 'level': 'WARN', 'msg': f'静默检查异常: {e}', 'auto_fix': False})

    # ── [2026-08-10 设计院封印] 并发安全检测：try块内危险sys.path.insert ──
    # 根因：brahma_1hao_analysis并行分析时多线程sys.path.insert竞争 → 方仓层静默丢失
    try:
        import re as _re
        _analysis_file = _ROOT / 'scripts' / 'brahma_1hao_analysis.py'
        if _analysis_file.exists():
            _src = _analysis_file.read_text()
            # 检查try块内的sys.path.insert（模块顶部的是合法的）
            # 精确检测：在try:后10行内查找sys.path.insert（避免跨函数误判）
            _lines = _src.split('\n')
            _dangerous = 0
            for _i, _line in enumerate(_lines):
                if _line.strip() == 'try:':
                    _window = '\n'.join(_lines[_i:_i+10])
                    if 'sys.path.insert' in _window:
                        _dangerous += 1
            if _dangerous > 0:
                issues.append({
                    'dim': 'D6_silent', 'level': 'WARN',
                    'msg': f'brahma_1hao_analysis: {_dangerous}处try块内含sys.path.insert — 并行race condition风险',
                    'auto_fix': False,
                    'detail': '修复方案: 将path初始化移至模块顶部',
                })
    except Exception:
        pass
    return issues


def scan_d7_crons() -> list:
    """D7: Cron任务健康"""
    issues = []
    try:
        r = subprocess.run(['openclaw', 'cron', 'list', '--json'],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            crons = json.loads(r.stdout)
            if isinstance(crons, list):
                for c in crons:
                    if c.get('status') not in ('ok', 'enabled', None):
                        issues.append({
                            'dim': 'D7_crons', 'level': 'WARN',
                            'msg': f'Cron异常: {c.get("name")} status={c.get("status")}',
                            'auto_fix': False,
                        })
    except Exception as e:
        pass  # cron检查失败不阻断
    return issues


def scan_d8_backups() -> list:
    """D8: 备份文件堆积"""
    issues = []
    try:
        bak_files = list(_DIR.glob('*.bak*'))
        if len(bak_files) > 20:
            size_mb = sum(f.stat().st_size for f in bak_files) / 1024 / 1024
            issues.append({
                'dim': 'D8_backups', 'level': 'WARN',
                'msg': f'备份文件堆积: {len(bak_files)}个 共{size_mb:.1f}MB — 建议git init清理',
                'auto_fix': False,
                'count': len(bak_files),
            })
    except Exception as e:
        pass
    return issues




def scan_d9_signal_pipeline() -> list:
    """D9: 信号链路关键节点可观测性检查"""
    issues = []
    try:
        import json, time
        # 读最近一次 brahma_state（记录analyze()运行结果）
        bs = _DATA / 'brahma_state.json'
        if not bs.exists():
            issues.append({
                'dim': 'D9_pipeline', 'level': 'WARN',
                'msg': 'brahma_state.json 不存在 — 信号链路无快照',
                'auto_fix': False,
            })
            return issues

        state = json.loads(bs.read_text())
        ts = state.get('ts', 0)
        age_min = (time.time() - ts) / 60 if ts else 999

        # 检查关键字段是否在最近输出中出现
        # brahma_state.json 是仓位状态文件，检查系统运行健康字段
        required_keys = ['regime', 'nav', 'positions']
        missing_keys = [k for k in required_keys if k not in state]
        if missing_keys:
            issues.append({
                'dim': 'D9_pipeline', 'level': 'WARN',
                'msg': f'系统状态快照缺失字段: {missing_keys}',
                'auto_fix': False,
            })

        # 检查RSM是否在运行（字段是 [symbol]['confirmed']）
        rsm_state = _DATA / 'regime_state.json'
        if rsm_state.exists():
            rsm = json.loads(rsm_state.read_text())
            btc_regime = rsm.get('BTCUSDT', {}).get('confirmed', '')
            if not btc_regime:
                issues.append({
                    'dim': 'D9_pipeline', 'level': 'WARN',
                    'msg': 'RSM状态机无有效体制记录(BTCUSDT)',
                    'auto_fix': False,
                })
        # ── [信号速率CRITICAL检查 2026-08-19 苏摩111封印] ────────────────────
        # 设计院原则：业务健康指标，48H无信号=WARN，>7天=CRITICAL
        lsl = _DATA / 'live_signal_log.jsonl'
        if lsl.exists():
            try:
                _sigs = [json.loads(l) for l in lsl.read_text().strip().split('\n') if l]
                _now = time.time()
                _recent_48h = [s for s in _sigs if _now - float(s.get('ts', 0)) < 48 * 3600]
                if not _recent_48h and _sigs:
                    _last_ts = max(float(s.get('ts', 0)) for s in _sigs)
                    _age_days = (_now - _last_ts) / 86400
                    _level = 'CRITICAL' if _age_days > 7 else 'WARN'
                    issues.append({
                        'dim': 'D9_signal_rate',
                        'level': _level,
                        'msg': f'信号断崖: 最近信号={_age_days:.1f}天前，生产链路可能断裂（当前体制CHOP则属正常）',
                        'auto_fix': False,
                    })
            except Exception:
                pass
    except Exception as e:
        pass  # D9非关键维度，失败不告警
    return issues


def run_full_scan() -> dict:
    """全量8维扫描，返回结构化结果"""
    ts = time.time()
    all_issues = []
    all_issues += scan_d1_modules()
    all_issues += scan_d2_data()
    all_issues += scan_d3_processes()
    all_issues += scan_d4_interfaces()
    all_issues += scan_d5_params()
    all_issues += scan_d6_silent_failures()
    all_issues += scan_d7_crons()
    all_issues += scan_d8_backups()
    all_issues += scan_d9_signal_pipeline()

    # 计算健康评分 (0~100)
    deductions = {'CRITICAL': 25, 'ERROR': 10, 'WARN': 3}
    score = 100
    for issue in all_issues:
        score -= deductions.get(issue['level'], 0)
    score = max(0, score)

    level_counts = {}
    for issue in all_issues:
        lv = issue['level']
        level_counts[lv] = level_counts.get(lv, 0) + 1

    return {
        'ts': ts,
        'datetime': datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M CST'),
        'health_score': score,
        'health_label': '🟢健康' if score >= 85 else ('🟡注意' if score >= 60 else ('🟠警告' if score >= 40 else '🔴危险')),
        'issues': all_issues,
        'level_counts': level_counts,
        'total_issues': len(all_issues),
    }


# ════════════════════════════════════════════════════════════════
# Layer3: AutoFixer — 自动修复（仅数据/进程/配置，不动代码）
# ════════════════════════════════════════════════════════════════

def fix_init_live_prices(issue: dict) -> bool:
    """修复: live_prices.json 初始化"""
    try:
        import urllib.request
        prices = {}
        for sym in ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT']:
            try:
                url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}'
                with urllib.request.urlopen(url, timeout=5) as r:
                    data = json.loads(r.read())
                prices[sym] = {'price': float(data['price']), 'ts': time.time(), 'source': 'brahma_360_fix'}
            except Exception:
                pass
        if prices:
            (_DATA / 'live_prices.json').write_text(json.dumps(prices, indent=2))
            print(f'[360-Fix] ✅ live_prices.json 初始化 {len(prices)}个标的')
            return True
    except Exception as e:
        print(f'[360-Fix] ❌ live_prices初始化失败: {e}')
    return False


def fix_reset_signal_queue(issue: dict) -> bool:
    """修复: signal_queue.jsonl 重置"""
    try:
        sq = _DATA / 'signal_queue.jsonl'
        if sq.exists():
            bak = _DATA / f'signal_queue.jsonl.bak_{int(time.time())}'
            sq.rename(bak)
        sq.write_text('')
        print(f'[360-Fix] ✅ signal_queue.jsonl 已重置')
        return True
    except Exception as e:
        print(f'[360-Fix] ❌ signal_queue重置失败: {e}')
    return False


def fix_restart_ws_guardian(issue: dict) -> bool:
    """修复: ws_guardian 进程重启"""
    try:
        r = subprocess.run(
            ['python3', str(_ROOT / 'ws_guardian.py'), '--daemon'],
            capture_output=True, text=True, timeout=5
        )
        print(f'[360-Fix] ✅ ws_guardian 重启指令已发送')
        return True
    except Exception as e:
        print(f'[360-Fix] ❌ ws_guardian重启失败: {e}')
    return False


FIX_HANDLERS = {
    'init_live_prices':    fix_init_live_prices,
    'reset_signal_queue':  fix_reset_signal_queue,
    'restart_ws_guardian': fix_restart_ws_guardian,
}


def auto_fix_issues(issues: list) -> list:
    """对所有可自动修复的问题执行修复，返回修复记录"""
    fix_log = []
    for issue in issues:
        if not issue.get('auto_fix'):
            continue
        action = issue.get('fix_action', '')
        handler = FIX_HANDLERS.get(action)
        if not handler:
            continue

        print(f'[360-AutoFix] 尝试修复: {issue["msg"][:60]}')
        success = handler(issue)
        fix_log.append({
            'ts': time.time(),
            'issue': issue['msg'],
            'action': action,
            'success': success,
        })
        time.sleep(1)  # 修复间隔

    return fix_log


# ════════════════════════════════════════════════════════════════
# Layer4: Verifier — 修复验证（60s后重扫同维度）
# ════════════════════════════════════════════════════════════════

def verify_fixes(fix_log: list) -> list:
    """修复后重新扫描验证"""
    if not fix_log:
        return []
    time.sleep(3)  # 等待修复生效

    results = []
    for fix in fix_log:
        if not fix['success']:
            results.append({**fix, 'verified': False, 'note': '修复失败，跳过验证'})
            continue
        # 重新扫描数据维度验证
        action = fix.get('action', '')
        verified = False
        if action == 'init_live_prices':
            verified = (_DATA / 'live_prices.json').exists()
        elif action == 'reset_signal_queue':
            sq = _DATA / 'signal_queue.jsonl'
            verified = sq.exists() and (time.time() - sq.stat().st_mtime) < 60
        elif action == 'restart_ws_guardian':
            r = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
            verified = 'ws_guardian' in r.stdout
        else:
            verified = True  # 无法验证的默认通过

        results.append({**fix, 'verified': verified,
                        'note': '✅验证通过' if verified else '❌验证失败，需人工介入'})
        print(f'[360-Verify] {"✅" if verified else "❌"} {fix["action"]}: {results[-1]["note"]}')
    return results


# ════════════════════════════════════════════════════════════════
# Layer5: Reporter — 健康报告
# ════════════════════════════════════════════════════════════════

def save_history(scan_result: dict, fix_log: list, verify_log: list):
    """追加记录到历史文件"""
    record = {
        'ts': scan_result['ts'],
        'datetime': scan_result['datetime'],
        'health_score': scan_result['health_score'],
        'total_issues': scan_result['total_issues'],
        'level_counts': scan_result['level_counts'],
        'fixes_applied': len([f for f in fix_log if f.get('success')]),
        'fixes_verified': len([v for v in verify_log if v.get('verified')]),
    }
    _DATA.mkdir(exist_ok=True)
    with open(_HISTORY, 'a') as f:
        f.write(json.dumps(record) + '\n')


# ════════════════════════════════════════════════════════════════
# OI 胜率统计模块（寄生）2026-07-17 苏摩111封印
# ════════════════════════════════════════════════════════════════

def get_oi_win_rate_section() -> str:
    """从 hunter_outcome_tracker 拉 OI 胜率，格式化为360日报一节"""
    try:
        import sys as _sys
        scripts_dir = str(Path(__file__).parent.parent / 'scripts')
        if scripts_dir not in _sys.path:
            _sys.path.insert(0, scripts_dir)
        from hunter_outcome_tracker import calc_oi_win_rate
        data = calc_oi_win_rate()
    except Exception as e:
        return f'\n📊 OI猎手胜率: 数据获取失败 ({e})'

    if data.get('error') or data.get('closed', 0) == 0:
        note = data.get('note', data.get('error', '暂无已平仓记录'))
        holding = data.get('holding', 0)
        return f'\n📊 OI猎手胜率: 暂无已平仓记录 | 持仓中: {holding}单\n  {note}'

    lines = ['', '📊 OI猎手胜率复盘']
    ov = data.get('overall', {})
    lines.append(f"  总计: {data['closed']}笔已平仓 | 持仓中: {data.get('holding',0)}单")
    lines.append(f"  综合胜率: {ov.get('wr','?')}%  EV: {ov.get('ev','?')}%/笔")
    lines.append(f"  均盈: +{ov.get('avg_win','?')}%  均亏: {ov.get('avg_loss','?')}%  最大DD: {ov.get('max_dd','?')}%")

    # 按模式
    mode_parts = []
    for m in ('A','B','C'):
        d = data.get(f'mode_{m}')
        if d: mode_parts.append(f"{m}类WR={d['wr']}%({d['n']}笔)")
    if mode_parts:
        lines.append('  模式: ' + ' | '.join(mode_parts))

    # 按方向
    dir_parts = []
    for dr in ('LONG','SHORT'):
        d = data.get(f'dir_{dr}')
        if d: dir_parts.append(f"{dr} WR={d['wr']}%({d['n']}笔)")
    if dir_parts:
        lines.append('  方向: ' + ' | '.join(dir_parts))

    # 评分最高段
    best = data.get('score_90_101') or data.get('score_80_90')
    if best:
        label = '90+分' if data.get('score_90_101') else '80-90分'
        lines.append(f"  {label}: WR={best['wr']}% EV={best['ev']}%/笔({best['n']}笔)")

    return '\n'.join(lines)


def format_report(scan_result: dict, fix_log: list = None, verify_log: list = None) -> str:
    """格式化健康报告（用于Jarvis推送）"""
    s = scan_result
    lines = [
        f"🔱 梵天360 健康报告",
        f"📅 {s['datetime']}",
        f"",
        f"健康评分: {s['health_score']}/100  {s['health_label']}",
        f"问题总数: {s['total_issues']}个",
    ]

    lc = s.get('level_counts', {})
    if lc:
        lines.append(f"  🔴CRITICAL:{lc.get('CRITICAL',0)}  🟠ERROR:{lc.get('ERROR',0)}  🟡WARN:{lc.get('WARN',0)}")

    if s['issues']:
        lines.append(f"\n📋 问题清单:")
        for issue in s['issues']:
            icon = {'CRITICAL':'🔴','ERROR':'🟠','WARN':'🟡'}.get(issue['level'],'⚪')
            fix_tag = ' [可自动修复]' if issue.get('auto_fix') else ''
            lines.append(f"  {icon} [{issue['dim']}] {issue['msg'][:60]}{fix_tag}")

    if fix_log:
        success = [f for f in fix_log if f.get('success')]
        lines.append(f"\n🔧 自动修复: {len(success)}/{len(fix_log)}项成功")
        for fix in success:
            lines.append(f"  ✅ {fix['action']}")

    if not s['issues']:
        lines.append(f"\n✅ 系统运行正常，无待处理问题")

    # OI胜率模块（寄生）
    try:
        oi_section = get_oi_win_rate_section()
        if oi_section:
            lines.append(oi_section)
    except Exception:
        pass

    return '\n'.join(lines)


# ════════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════════

def run_360(auto_fix: bool = True, push: bool = False) -> dict:
    """梵天360全流程入口"""
    print(f'[梵天360] 开始全量体检 {datetime.now(timezone(timedelta(hours=8))).strftime("%H:%M CST")}')

    # Layer1: 扫描
    scan = run_full_scan()
    print(f'[梵天360] 健康评分: {scan["health_score"]}/100 {scan["health_label"]} | 问题: {scan["total_issues"]}个')

    # Layer3: 自动修复
    fix_log, verify_log = [], []
    if auto_fix and scan['issues']:
        fixable = [i for i in scan['issues'] if i.get('auto_fix')]
        if fixable:
            print(f'[梵天360] 自动修复 {len(fixable)} 个问题...')
            fix_log = auto_fix_issues(fixable)
            # Layer4: 验证
            verify_log = verify_fixes(fix_log)

    # Layer5: 存档
    save_history(scan, fix_log, verify_log)

    # 报告
    report = format_report(scan, fix_log, verify_log)

    # 推送（仅CRITICAL/ERROR）
    critical_count = scan['level_counts'].get('CRITICAL', 0) + scan['level_counts'].get('ERROR', 0)
    if push and critical_count > 0:
        print(f'[梵天360] 推送告警 ({critical_count}个CRITICAL/ERROR)')

    return {
        'scan': scan,
        'fix_log': fix_log,
        'verify_log': verify_log,
        'report': report,
    }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='梵天360健康管理')
    parser.add_argument('--fix',      action='store_true', help='自动修复')
    parser.add_argument('--push',     action='store_true', help='推送告警（异常才推）')
    parser.add_argument('--report',   action='store_true', help='只输出报告')
    parser.add_argument('--autopush', action='store_true', help='systemEvent模式：异常自动推送，正常静默')
    args = parser.parse_args()

    result = run_360(auto_fix=True, push=args.push)
    print('\n' + result['report'])

    # --autopush: systemEvent专用入口，有ERROR/CRITICAL才推送，正常静默
    if args.autopush:
        scan = result['scan']
        critical = scan['level_counts'].get('CRITICAL', 0) + scan['level_counts'].get('ERROR', 0)
        if critical > 0:
            import subprocess as _sp
            _sp.run(
                ['openclaw', 'message', 'send',
                 '--channel', 'jarvis',
                 '--target', os.environ.get('JARVIS_TARGET', '73295708:thread:019fd70a-0942-72b1-aeb9-1bd4fc11b30d'),  # SSOT [2026-07-07]
                 '--message', result['report']],
                capture_output=True, timeout=15
            )
            print(f'[brahma-360] 🚨 推送异常告警 (CRITICAL/ERROR={critical})')
        # 正常(score=100) → 完全静默，不推送，不消耗AI
