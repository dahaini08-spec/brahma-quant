# Graph Report - graphify-out  (2026-08-26)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 1969 nodes · 3464 edges · 139 communities (132 shown, 7 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 156 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `4487b69d`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- smc_engine.py
- brahma_kronos.py
- coinglass_engine.py
- brahma_health.py
- GateResult
- CircuitBreaker
- brahma_order_engine.py
- universal_asset_router.py
- brahma_360.py
- brahma_macro.py
- get_klines
- score
- get_narrative_score
- brahma_core_step4.py
- market_state.py
- price_zone_engine.py
- BrahmaEventBus
- calc_block_a
- sl_bandit.py
- review
- apply_tf_confluence
- brahma_cpu.py
- brahma_decision_engine.py
- CrossAssetGate
- gex_scanner.py
- HCMEMatcher
- format_batch_report
- analyze_trigger
- fangcang_engine.py
- portfolio_optimizer.py
- calc_rsi
- brahma_core.py
- brahma_pipeline.py
- get_price
- analyze
- brahma_onchain.py
- get_liq_density
- analyze_tradfi_dump
- BrahmaBus
- calc_block_c
- brahma_position.py
- _f
- macro_calendar.py
- signal_15m_engine.py
- antifragile_guard.py
- market_behavior_model.py
- timesfm_lite.py
- brahma_wiring_v2.py
- ev_feedback.py
- extreme_event_db.py
- signal_queue.py
- brahma_mem_compressor.py
- brahma_analysis_runner.py
- RegimeStateMachine
- brahma_ml_engine.py
- brahma_core_entry.py
- divergence_engine.py
- macro_engine.py
- position_sizer.py
- safety.py
- whale_engine.py
- brahma_context_injector.py
- brainlog.py
- capital_allocator.py
- get_multi_tf_cvd
- enhanced_score
- fangcang_hcme_bridge.py
- get_options_pc
- signal_trace.py
- signal_weight_updater.py
- evaluate_timing
- anomaly_guards.py
- brahma_coordinator.py
- mode_c_detector.py
- brahma_experience_distiller.py
- brahma_fullcycle_builder.py
- brahma_gateway.py
- brahma_learning_loop.py
- brahma_readiness.py
- BrainLogger
- cross_market_engine.py
- klines_to_ohlcv
- ic_tracker.py
- brahma_wiring_check.py
- microstructure_engine.py
- predict_regime_proba
- ssi_engine.py
- tradfi_signal_layer.py
- volume_exhaustion_score
- auto_review.py
- brahma_council.py
- brahma_fangcang_unified.py
- brahma_multiframe.py
- brahma_smoke_test.py
- fangcang_tradfi_db.py
- compute_gex
- online_learner_v2.py
- signal_integrity_gate.py
- brahma_guardian.py
- rl_position_ab.py
- archive/sentiment_engine.py
- bulk_update_from_api
- realtime_liq_tracker.py
- tradfi_router.py
- brahma_trade.py
- exception_injector.py
- decision_weight_matrix.py
- failure_pattern_db.py
- _extract_features
- signal_lifecycle.py
- volume_engine.py
- volume_profile.py
- .klines
- brahma_intel_layer.py
- enrich_signal_grade
- lsr_oi_score
- scan_structure
- modules_deprecated_20260811/signal_selector.py
- confluence_score
- fangcang_builder_30.py
- _scan_history
- get_har_rv
- get_hurst
- llm_council.py
- analyze_orderbook
- tradfi_macro_gate.py
- tradfi_sector_engine.py
- run_batch
- _auto_heal_suggestions
- _check_log_health
- bollinger_score
- evaluate_nodes
- _check_standby_violations_health
- _check_zombie_positions
- enable_offline_network_block
- flush_stale_disk_cache
- _calc_vol_20d_avg

## God Nodes (most connected - your core abstractions)
1. `_f()` - 68 edges
2. `analyze()` - 36 edges
3. `get_klines()` - 33 edges
4. `_analyze_step4()` - 31 edges
5. `analyze()` - 27 edges
6. `run_health_check()` - 26 edges
7. `BrahmaBus` - 24 edges
8. `review()` - 21 edges
9. `run_analysis()` - 21 edges
10. `analyze_smc()` - 18 edges

## Surprising Connections (you probably didn't know these)
- `get_adaptive_weight()` --calls--> `_f()`  [INFERRED]
  archive/brahma_ml_engine.py → s27_gap_bounce_frd.py
- `get_ic_summary()` --calls--> `_f()`  [INFERRED]
  archive/brahma_ml_engine.py → s27_gap_bounce_frd.py
- `online_update()` --calls--> `_f()`  [INFERRED]
  archive/brahma_ml_engine.py → s27_gap_bounce_frd.py
- `update_ic()` --calls--> `_f()`  [INFERRED]
  archive/brahma_ml_engine.py → s27_gap_bounce_frd.py
- `run_batch()` --uses--> `BrahmaCircuitRegistry`  [INFERRED]
  brahma_analysis_runner.py → circuit_breaker.py

## Import Cycles
- None detected.

## Communities (139 total, 7 thin omitted)

### Community 0 - "smc_engine.py"
Cohesion: 0.07
Nodes (51): info(), issue(), probe_asset_consistency(), probe_code_consistency(), probe_data_freshness(), probe_execution_layer(), probe_ob_fvg_quality(), probe_push_links() (+43 more)

### Community 1 - "brahma_kronos.py"
Cohesion: 0.06
Nodes (47): get_kronos_bundle(), get_kronos_score(), get_s23_kronos(), get_s23_score(), get_shadow_stats(), get_volatility_forecast(), Kronos评分主入口，自动降级: bridge(HAR-RV+shadow) → engine(PyTorch) → lite(统计) → 0分, bridge vs lite对比shadow统计 (+39 more)

### Community 2 - "coinglass_engine.py"
Cohesion: 0.09
Nodes (32): _bus_price(), _cached(), get_fear_greed(), get_full_snapshot(), get_funding(), get_liquidation(), get_ls_ratio(), get_oi_momentum() (+24 more)

### Community 3 - "brahma_health.py"
Cohesion: 0.09
Nodes (33): _check_binance_api(), _check_brahma_bus(), _check_cron_route_ssot(), _check_cron_runtime_health(), _check_data_files(), _check_dharma_factor_weights(), _check_external_routes(), _check_learning_loop_importable() (+25 more)

### Community 4 - "GateResult"
Cohesion: 0.10
Nodes (19): _bj_now(), _bj_ts(), format_full_report(), 运行SQE质检，返回 {result, reason} 字典, 主入口：运行梵天1号工程全能力报告 [封印 2026-08-24 苏摩最高封印] 全能力 =…, run_full_analysis(), _run_sqe(), evaluate_signal() (+11 more)

### Community 5 - "CircuitBreaker"
Cohesion: 0.11
Nodes (12): BrahmaCircuitRegistry, CBState, circuit_protected(), CircuitBreaker, CircuitBreakerConfig, CircuitBreakerState, Any, 通过熔断器调用函数 OPEN状态时返回fallback值 (+4 more)

### Community 6 - "brahma_order_engine.py"
Cohesion: 0.12
Nodes (23): check_triggers(), create_trade_plan(), format_plan_card(), get_order_book_imbalance(), get_order_bundle(), get_recent_trades_flow(), order_flow_score(), 一次调用获取订单流+盘口数据（并行） 返回: {imbalance, flow, order_score, ts} (+15 more)

### Community 7 - "universal_asset_router.py"
Cohesion: 0.10
Nodes (24): batch_analyze(), batch_analyze_ranked(), batch_analyze_with_regime(), market_wide_scan(), pump_hunter_parallel_scan(), brahma_parallel_engine.py — 梵天并行引擎层 设计院·达摩院 自主决策 2026-06-29 核心：把 analyze()…, 安全包装 phase3_scanner 的评分逻辑, 全市场并行扫描 + 资产路由 替代 brahma360_full.py 的串行扫描 返回：所有 score >= min_score 的有效信号（排序） (+16 more)

### Community 8 - "brahma_360.py"
Cohesion: 0.12
Nodes (25): auto_fix_issues(), fix_init_live_prices(), fix_reset_signal_queue(), format_report(), get_oi_win_rate_section(), _has_open_positions(), 检查是否有未平仓持仓（用于ws_guardian豁免逻辑）, D5: 铁证参数一致性（MEMORY.md封印值 vs 代码实际） (+17 more)

### Community 9 - "brahma_macro.py"
Cohesion: 0.12
Nodes (23): _bus_price(), fib_macro_score(), _get(), get_btc_dominance(), get_dxy_realtime(), get_fear_greed(), get_macro_bundle(), get_nasdaq_realtime() (+15 more)

### Community 10 - "get_klines"
Cohesion: 0.23
Nodes (22): _cache_get(), _cache_key(), _cache_set(), _disk_path(), _get(), get_all_active_symbols(), get_atr_percentile(), get_basis() (+14 more)

### Community 11 - "score"
Cohesion: 0.14
Nodes (21): analyze(), analyze_with_selector(), brahma_orchestrator.py — 梵天分析编排器 v24.0 职责：编排。调用brahma_core.analyze()并注入模块化后处理…, 编排入口。当前透传到brahma_core.analyze() deep=True: 深度分析模式，跳过NEUTRAL快速退出，返回完整数据, [设计院 2026-06-30 全量接入] 双向分析 + signal_selector 裁决 同时跑 LONG + SHORT，由…, _ema_scalar(), _higher_highs(), _klines() (+13 more)

### Community 12 - "get_narrative_score"
Cohesion: 0.12
Nodes (23): get_extreme_event_score_signal(), get_extreme_event_warning(), get_longmem_regime_factor(), get_longmem_score_adj(), brahma_longmem.py — 梵天跨资产20年长期记忆系统…, 根据当前体制，返回基于长期统计规律的调整因子。 整合减半周期 + 美联储周期 + 季节性规律。, 检测当前市场是否与历史极端事件高度相似。 返回 {warning_level, matched_event, similarity, action,…, 梵天长期记忆 → score调整值（注入brahma_core）。 综合： - 跨资产规律（黄金/纳指迁移） - 减半周期因子 - DXY/美联储周期 -… (+15 more)

### Community 13 - "brahma_core_step4.py"
Cohesion: 0.12
Nodes (18): flush_stale(), get_klines(), [P0-6修复 2026-07-16 苏摩111] 使用全局单例bus，不重新实例化, _analyze_step4(), get_klines(), klines_to_ohlcv(), Step4: extra_data 构建层 构建 confluence_score() 需要的全部额外数据字典： klines 1H/4H → OHLCV…, get_cross_fr_basis() (+10 more)

### Community 14 - "market_state.py"
Cohesion: 0.16
Nodes (22): adx(), analyze(), atr(), bb(), _build_summary(), calc_fib_levels(), calc_pivot_points(), detect_regime() (+14 more)

### Community 15 - "price_zone_engine.py"
Cohesion: 0.13
Nodes (20): _atr(), bollinger(), _ema(), macd(), math_utils.py — 梵天数学工具统一库 设计院·达摩院 深度排查 2026-06-29 问题根因： _ema 在 8 个文件中重复定义 _rsi…, ATR 序列（Wilder平滑） 返回与输入等长（首元素用 high-low 代替）, 返回 (macd_line, signal_line, histogram) 最新值, 返回 (upper, mid, lower) 最新值 (+12 more)

### Community 16 - "BrahmaEventBus"
Cohesion: 0.13
Nodes (7): BrahmaEvent, BrahmaEventBus, Event, Any, # STATUS: ACTIVE # 事件总线，模块间通信 # LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链 #…, 发射事件 → 调用所有注册的处理器 persist=True 时写入事件日志文件, 轻量级事件总线 - 同步模式（默认）：直接调用所有处理器 - 异步模式：后台线程队列处理（可选） - 状态持久化：事件日志写入文件

### Community 17 - "calc_block_a"
Cohesion: 0.16
Nodes (19): calc_block_a(), 维度1-6：纯技术分析层 维度1: 趋势一致性 (0~20) - EMA/ADX多周期共识 维度2: 关键位精确度 (0~20) -…, build_multi_tf_context(), _calc_resonance(), _ema(), _fetch_klines(), _parse_ohlcv(), 构建AI议会可读的全周期快照字符串 直接注入 brahma_context_injector 第4层 (+11 more)

### Community 18 - "sl_bandit.py"
Cohesion: 0.17
Nodes (20): _atr14_from_parquet(), compute(), # STATUS: ACTIVE # 动态止损计算，执行辅助 # LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链 #…, 计算动态止损位 Returns: { 'sl_price': 推荐止损价 'sl_pct': 止损幅度% 'atr14': ATR14绝对值…, 将止损吸附到最近的关键位（如果在tolerance范围内）, _snap_to_key_level(), _arm_key(), get_stats() (+12 more)

### Community 19 - "review"
Cohesion: 0.14
Nodes (21): _cache_key(), _call_llm(), _check_daily_limit(), _devil_agent_review(), get_shadow_stats(), _load_cache(), _macro_agent_review(), _quant_agent_review() (+13 more)

### Community 20 - "apply_tf_confluence"
Cohesion: 0.12
Nodes (16): [2026-08-18 苏摩111封印] 实时最新价 — 永不缓存，每次直接拉取币安期货API…, calc_block_b(), 维度7-10：链上/清算/资金费层 维度7: 清算带/OI (ws_guardian实时数据) 维度8: 资金费率+情绪 (funding rate /…, apply_tf_confluence(), get_tf_consensus(), P2-A 主入口: 在 confluence_score 输出后注入多TF共振奖励 Args: score: 原始 confluence_score 总分…, 从breakdown中提取各时间框架的方向共识 Returns: { '15m': 0, # 有效信号数 '1h': 3, '4h': 2, '1d': 1,…, get_score_multiplier() (+8 more)

### Community 21 - "brahma_cpu.py"
Cohesion: 0.15
Nodes (20): _check_position_risk(), _check_soma_online(), _do_alert(), _do_execute(), _do_watch(), _get_nav(), _layer0_fast_reject(), _layer1_score() (+12 more)

### Community 22 - "brahma_decision_engine.py"
Cohesion: 0.16
Nodes (18): BrahmaDecisionEngine, _check_15m_structure(), decide(), _dynamic_sl_max(), _get_15m_struct_sl(), _get_atr_1h(), _get_current_price(), get_decision_engine() (+10 more)

### Community 23 - "CrossAssetGate"
Cohesion: 0.15
Nodes (12): apply_cross_asset_gate(), _calc_beta(), CrossAssetGate, get_gate(), _get_price(), _pub(), 检查单个信号的跨资产一致性 返回: 修改后的signal（可能降级timing_badge为WAIT）, 信号有效性检查 — 修复3大缺陷之首要门: 1. expires_at 字段存在且未过期 2. valid 字段为 True（或无此字段时默认通过） 3.… (+4 more)

### Community 24 - "gex_scanner.py"
Cohesion: 0.15
Nodes (20): black_scholes_gamma(), compute_gex_profile(), _fetch(), format_gex_report(), get_book_summary(), get_gex_score_for_signal(), get_gex_state(), get_option_instruments() (+12 more)

### Community 25 - "HCMEMatcher"
Cohesion: 0.15
Nodes (13): _cosine(), _get_ath(), HCMEMatcher, Matches a live signal against historical context for confidence adjustment.…, Load pre-built index or rebuild from signals., Pre-compute feature vectors for all signals and persist., Convert signal → 15-dim normalized feature vector. Dims: 0 regime_enc [-1, +1]…, Find top-k most similar historical signals via cosine similarity. Returns… (+5 more)

### Community 26 - "format_batch_report"
Cohesion: 0.16
Nodes (18): format_batch_report(), 批量格式化输出 — 封印版标准报告 每张卡片头部强制嵌入 BRAHMA 标签，防混淆防误识别 mode: 'card' — 精简信号卡（推送用） 'full'…, format_fangcang_card(), brahma_panorama_report(), _build_action_guide(), build_output_tag(), extract_standard_fields(), _fmt_price() (+10 more)

### Community 27 - "analyze_trigger"
Cohesion: 0.13
Nodes (19): calc_trade_params(), _nearest_swing_above(), _nearest_swing_below(), 找到入场价上方最近的摆动高点（用于做空止损）, 找到入场价下方最近的摆动低点（用于做多止损）, 精确交易参数生成 — v13.0 四层止损架构…, analyze_trigger(), _bus_price() (+11 more)

### Community 28 - "fangcang_engine.py"
Cohesion: 0.15
Nodes (19): _build_probability_matrix(), _build_summary(), _calc_best_entry_window(), _detect_main_force_intent(), get_fangcang_context(), _integrate_hcme(), _integrate_m4_bias(), _load_klines() (+11 more)

### Community 29 - "portfolio_optimizer.py"
Cohesion: 0.16
Nodes (19): build_corr_matrix(), _calc_diversity(), _calc_ev(), _calc_portfolio_risk_mult(), check_correlation_risk(), filter_signals(), get_pair_correlation(), _load_returns() (+11 more)

### Community 30 - "calc_rsi"
Cohesion: 0.15
Nodes (15): _calc_rsi(), RSI极值检测评分 SHORT: RSI_1H > 75 (极度超买) → +10 RSI_1H > 68 → +6 RSI_1H > 60 → +3…, RSI计算 — 委托math_utils统一实现 [2026-08-24 设计院精简], rsi_extreme_score(), get_market_context(), get_market_phase(), get_quadrant(), 识别当前市场所处阶段 Returns: { 'phase':… (+7 more)

### Community 31 - "brahma_core.py"
Cohesion: 0.13
Nodes (16): _calc_mtf_alignment(), calc_trade_params(), format_report(), _nearest_swing_above(), _nearest_swing_below(), 找到入场价上方最近的摆动高点（用于做空止损）, 找到入场价下方最近的摆动低点（用于做多止损）, [已移至 brahma_core_entry.py 2026-07-01] 向后兼容别名 (+8 more)

### Community 32 - "brahma_pipeline.py"
Cohesion: 0.16
Nodes (17): get_funding(), get_oi(), _async_council_followup(), brahma_pipeline.py — 梵天全能力分析强制流水线…, 调用price_zone_engine.calc_zones() + format_zone_report()。 返回高空区/低多区/路径概率。, 把6步结果组装成VIP卡片格式，带price_ts时间戳。, 后台线程：等议会跑完，把结论追加推送给苏摩。 不阻塞主流程，卡片已先发出。, 梵天全能力分析唯一入口。 6步强制走完，任何步骤失败明确说明原因。 (+9 more)

### Community 33 - "get_price"
Cohesion: 0.22
Nodes (17): get_price(), 统一价格查询 — bus缓存优先，fallback裸HTTP，所有模块应迁移到此接口, auto_paper_trade(), close_paper_trade(), generate_daily_report(), get_paper_nav(), get_paper_positions(), _log_trade() (+9 more)

### Community 34 - "analyze"
Cohesion: 0.14
Nodes (16): analyze(), 梵天大脑主入口 symbol: 交易对（如 ETHUSDT） signal_dir: 强制方向（LONG/SHORT），None=自动判断 deep:…, s27_gap_bounce_frd.py — 统计模式维度（Ponytail重构 2026-08-24） # ponytail:…, s27_gap_up(), s28_bounce_setup(), s29_first_red_day(), apply_ob_decay_penalty(), evaluate_structure_quality() (+8 more)

### Community 35 - "brahma_onchain.py"
Cohesion: 0.20
Nodes (16): get_enhanced_liq_context(), get_funding_trend(), get_liq_density(), get_liquidation_data(), get_long_short(), get_miner_pressure(), get_oi_change(), get_onchain_bundle() (+8 more)

### Community 36 - "get_liq_density"
Cohesion: 0.15
Nodes (17): _bus_price(), _empty_liq(), _get_binance_force_orders(), _get_binance_ws_cache(), _get_bybit_liquidations(), _get_hyperliquid_liquidations(), get_hyperliquid_oi(), get_liq_density() (+9 more)

### Community 37 - "analyze_tradfi_dump"
Cohesion: 0.16
Nodes (17): analyze_tradfi_dump(), backtest_sndk_0727(), identify_dump_type(), is_tradfi_token(), m1_top_distribution_detector(), m2_price_volume_divergence(), m3_obv_divergence_weight(), m4_swing_high_decay() (+9 more)

### Community 38 - "BrahmaBus"
Cohesion: 0.18
Nodes (3): BrahmaBus, 清除缓存（pattern=None 清全部）, 统一数据总线：所有引擎共用同一缓存层 使用方式：from brahma_brain.brahma_bus import bus

### Community 39 - "calc_block_c"
Cohesion: 0.16
Nodes (12): _bc_get(), _bc_set(), calc_block_c(), 维度11-19 + s20-s22 + s_research 高级信号层 全部 try/except fail-safe，任何依赖缺失均归零, _load_experience_pool(), 贝叶斯增量评分 Returns: (adj_score, detail_dict), 从 live_signal_log.jsonl 构建经验池, score() (+4 more)

### Community 40 - "brahma_position.py"
Cohesion: 0.21
Nodes (16): apply_headroom(), check_correlation_risk(), filter_signals(), get_fg_position_cap(), get_pair_correlation(), get_position_bundle(), get_position_pct(), kelly_position() (+8 more)

### Community 41 - "_f"
Cohesion: 0.24
Nodes (16): add_signal(), apply_post_filters(), check_integrity(), check_signal_quality(), enhance_signal(), evaluate_signal(), filter_signals(), get_enhanced_score() (+8 more)

### Community 42 - "macro_calendar.py"
Cohesion: 0.16
Nodes (15): get_active_risk(), _get_btc_dominance(), _get_fng(), get_upcoming_events(), _load_cache(), macro_calendar.py — 宏观事件日历引擎 设计院 P3修复 · 2026-07-12 职责： 实时返回近期高影响宏观事件 CPI / FOMC…, 返回当前宏观风险状态 供 brahma_core extra_data['macro_calendar'] 使用, _save_cache() (+7 more)

### Community 43 - "signal_15m_engine.py"
Cohesion: 0.19
Nodes (16): _atr(), _detect_choch_bos_15m(), _detect_fvg_15m(), _detect_ob_15m(), _ema(), generate_15m_signal(), 检测15M FVG（Fair Value Gap） 三K线型：bars[i-2], bars[i-1], bars[i] 多头FVG:…, 检测15M Order Block（最近有效OB） 多头OB：大阴线后转大阳线的阴线区域（机构买入区） 空头OB：大阳线后转大阴线的阳线区域（机构卖出区）… (+8 more)

### Community 44 - "antifragile_guard.py"
Cohesion: 0.21
Nodes (15): check_blackswan(), check_emotion_extreme(), check_exchange_anomaly(), format_guard_report(), full_guard_check(), get_size_multiplier(), _load_state(), 记录每笔交易结果，触发连亏保护 outcome: 'WIN' | 'LOSS' | 'TIMEOUT' (+7 more)

### Community 45 - "market_behavior_model.py"
Cohesion: 0.17
Nodes (15): build_market_behavior_model(), compute_fakeout_stats(), compute_integer_levels(), compute_time_bias(), _load_4h_data(), print_current_bias(), print_integer_level_analysis(), Market Behavior Model (M4) ============================ Statistically derives… (+7 more)

### Community 46 - "timesfm_lite.py"
Cohesion: 0.19
Nodes (15): _covariate_adjustment(), _direction_probability(), _ema(), get_timesfm_score(), _holt_forecast(), _multiscale_features(), ndarray, # STATUS: ACTIVE # TimesFM轻量版，时序预测 # LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链… (+7 more)

### Community 47 - "brahma_wiring_v2.py"
Cohesion: 0.18
Nodes (15): _check_module(), check_price_ts(), clear_known_issues(), _load_state(), _maybe_push(), brahma_wiring_v2.py — 梵天接线健康巡检 v2.0…, 全量接线巡检，返回 {ok, issues, report} push_on_error: True 时对新出现的断线通过 Jarvis 推送告警, 仅当断线是「新出现」时推送，静默已知持续断线 (+7 more)

### Community 48 - "ev_feedback.py"
Cohesion: 0.19
Nodes (15): _generate_nudge(), get_ev_summary(), _load_matrix(), on_settlement(), ev_feedback.py — EV实时反馈模块 设计院·达摩院 封印 2026-06-30 职责：每笔交易结算后，自动更新EV矩阵并触发参数微调…, 评分分档：<120 / 120-139 / 140-159 / 160+, 每10笔触发参数微调建议 仅写入建议文件，不直接修改brahma_core（设计院安全原则）, settler结算完成后调用此接口 封装所有EV反馈逻辑，对settler零侵入 (+7 more)

### Community 49 - "extreme_event_db.py"
Cohesion: 0.20
Nodes (15): build_extreme_events(), _euclidean(), _load_events(), _load_klines_gz(), match_current_similarity(), extreme_event_db.py — A2 极端事件库 梵天设计院封印 2026-08-25 功能: build_extreme_events() —…, 计算当前市场状态与历史极端事件的相似度。 返回: { 'current_rsi' : float, 'current_3d_change': float,…, Wilder 平滑 RSI，输入至少 period+1 个收盘价，不足则返回 50.0 (+7 more)

### Community 50 - "signal_queue.py"
Cohesion: 0.23
Nodes (15): add_signal(), _corr_group(), _get_cooldown_min(), get_next(), get_status(), _is_in_cooldown(), _load_recent_wr(), _load_state() (+7 more)

### Community 51 - "brahma_mem_compressor.py"
Cohesion: 0.24
Nodes (14): auto_distill_to_memory(), compress_signal_context(), _extract_oi_summary(), _extract_position_summary(), _extract_recent_signals(), _extract_regime_summary(), _get_active_positions_summary(), get_global_context_snapshot() (+6 more)

### Community 52 - "brahma_analysis_runner.py"
Cohesion: 0.22
Nodes (14): check_correlation_risk(), 全景分析接口 — 设计院 2026-07-13 封印 在 run_analysis() 基础上附加： - _panorama_card :…, 相关性去重防错（设计院 2026-07-01） BTC+ETH同向开仓时，实际风险敞口 = 1.85x BTC（相关系数≈0.85）…, 检查 analyze() 结果是否包含所有必需字段 返回缺失字段列表（空列表=全部完整）, 单标的分析 — 封印版唯一入口 规则： - 必须走 brahma_core.analyze(deep=True) - 不得绕过此函数直接调用…, run_analysis(), run_analysis_full(), trace_generated() (+6 more)

### Community 53 - "RegimeStateMachine"
Cohesion: 0.17
Nodes (5): get_regime_status(), 梵天体制状态机 负责将 detect_regime 的原始单点输出转化为稳定的确认体制, 持久化状态（始终同步confirmed_cn，防止历史遗留字段错位）, 输入原始单点体制，返回经过稳定处理的确认体制 INT-2: 支持 HMM 概率化辅助（设计院六方联合 2026-07-11） 当 symbol +…, RegimeStateMachine

### Community 54 - "brahma_ml_engine.py"
Cohesion: 0.18
Nodes (13): get_adaptive_weight(), get_har_rv_forecast(), get_hurst_index(), get_ic_score_adj(), get_ic_summary(), get_ml_bundle(), har_rv_score(), hurst_regime_check() (+5 more)

### Community 55 - "brahma_core_entry.py"
Cohesion: 0.20
Nodes (12): [WFV-v3.0 2026-05-28] sl_mult 默认改为 0.6x（达摩院无穿越训练全局冠军） 达摩院v3.0 OOS验证: SL=0.6xATR…, rebase_params(), analyze_funding_trend(), get_deribit_pcr(), get_fear_greed(), get_max_pain(), _pcr_signal(), CoinGlass v4 MaxPain：最大痛点价格 + 认购/认沽OI 到期日价格趋向MaxPain（做市商对冲机制） (+4 more)

### Community 56 - "divergence_engine.py"
Cohesion: 0.24
Nodes (13): calc_macd_series(), calc_rsi_series(), _calc_volume_contraction(), detect_candlestick_patterns(), detect_macd_divergence(), detect_rsi_divergence(), divergence_score(), find_pivots() (+5 more)

### Community 57 - "macro_engine.py"
Cohesion: 0.22
Nodes (13): _get(), get_btc_dominance(), get_dxy_realtime(), get_nasdaq_realtime(), macro_score(), macro_score_v2(), DXY 美元指数实时（Yahoo Finance /v8，免费） 返回：price, chg_1h_pct, chg_24h_pct, direction, 纳指期货 NQ=F 实时（Yahoo Finance，免费） BTC与纳指相关系数≈0.7，宏观共振确认 (+5 more)

### Community 58 - "position_sizer.py"
Cohesion: 0.19
Nodes (12): get_narrative_position_mult(), 叙事仓位修正乘数（供 position_sizer.get_position_pct 调用） 规则： FG<20（极度恐惧）+ LONG → ×1.15…, apply_headroom(), get_fg_position_cap(), get_headroom_factor(), get_position_pct(), kelly_position(), 根据恐贪指数返回仓位上限和说明 返回: (cap_pct: float, reason: str) | None表示不限制 (+4 more)

### Community 59 - "safety.py"
Cohesion: 0.26
Nodes (12): get_max_nav_pct(), get_min_score(), is_live_trading_enabled(), is_paper_only(), _load_safety_config(), 检查API密钥是否从环境变量正确加载（非空）, 读取 safety.yaml，失败时 fail-closed, 执行层必须调用此函数。 如果实盘未启用，抛出 RuntimeError。 (+4 more)

### Community 60 - "whale_engine.py"
Cohesion: 0.23
Nodes (13): _bus_price(), _fallback_exchange_flow(), _get(), get_derivatives_smart_money(), get_exchange_flow(), get_whale_activity(), 降级：用 Binance 大额 aggTrades 作为代理, 巨鲸活动分析 - 近5分钟大额聚合成交（>50万美元） - Taker 方向 vs 小单方向分化 (+5 more)

### Community 61 - "brahma_context_injector.py"
Cohesion: 0.24
Nodes (12): get_brahma_rules(), get_extreme_analog(), get_fangcang_summary(), get_top3_similar(), inject_brahma_context(), _load_wr_matrix(), brahma_context_injector.py — 梵天AI记忆注射器…, 把梵天专有知识压缩成AI可读的系统提示词前缀。 每次AI议会调用前注入，让AI拥有梵天专属思维。 参数: symbol: 交易对 regime: 当前体制… (+4 more)

### Community 62 - "brainlog.py"
Cohesion: 0.19
Nodes (8): berror(), binfo(), bwarn(), _format(), get_logger(), brainlog.py — 梵天统一日志格式封装 设计院·增效减负 2026-07-01…, 零迁移成本快捷函数，直接替换 print(f'[{tag}] {msg}'), 格式：[MODULE:LEVEL] message 生产模式省略时间戳（减少IO开销）

### Community 63 - "capital_allocator.py"
Cohesion: 0.27
Nodes (12): compute(), _get_active_exposure(), get_budget_summary(), _get_nav(), Path, # STATUS: ACTIVE # 资金分配引擎，多仓位管理 # LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链 #…, 返回 (n_active, total_risk_used), 计算本次可分配资金 Returns: { 'position_usdt': 建议开仓USDT 'risk_usdt': 本次风险敞口… (+4 more)

### Community 64 - "get_multi_tf_cvd"
Cohesion: 0.27
Nodes (12): _aggTrades_cvd(), _classify(), cvd_score_for_signal(), _get(), get_multi_tf_cvd(), _kline_cvd(), 主接口：获取多周期 CVD 分析 返回 micro / meso / macro 三层 + 综合评分, 简化接口：供 enhanced_signal_engine.enhanced_score() 调用 返回 (score, notes) (+4 more)

### Community 65 - "enhanced_score"
Cohesion: 0.24
Nodes (12): _bus_price(), enhanced_score(), _get(), get_cvd(), get_liquidation_levels(), get_lsr_trend(), get_session_weight(), 获取清算热力图数据 关键：多空清算不对称 → 识别价格猎杀方向 返回：liq_above（上方清算密度）+ liq_below（下方清算密度） (+4 more)

### Community 66 - "fangcang_hcme_bridge.py"
Cohesion: 0.21
Nodes (12): feedback_settlement(), get_fangcang_hcme_score(), get_feedback_stats(), _load_fangcang_cases(), _load_weights(), _normalize_new_case(), fangcang_hcme_bridge.py — 方仓增强型HCME桥接引擎 设计院 2026-08-23 苏摩111封印 架构升级：…, 主入口：替换旧HCME调用 从ms提取BBW/RSI/regime，调用方仓相似度匹配，返回评分 返回格式与旧HCME兼容：… (+4 more)

### Community 67 - "get_options_pc"
Cohesion: 0.23
Nodes (12): _from_binance(), _from_cache_file(), _from_deribit(), _get(), get_options_pc(), options_pc_ratio.py — 梵天期权P/C比层 (s_options / _options_pc_v56) 设计院 2026-08-01…, 主入口 — brahma_engine 调用 symbol: 'BTC' / 'ETH'（不含USDT） direction: 'SHORT' / 'LONG', Deribit 公开 API 获取 BTC/ETH 期权 OI (+4 more)

### Community 68 - "signal_trace.py"
Cohesion: 0.24
Nodes (12): format_audit_report(), get_trace_history(), log_signal_trace(), _parse_tag(), signal_trace.py — 信号执行轨迹审计日志 brahma_brain · 设计院封印 2026-07-02 # ╔══ INTERFACE…, 格式化审计报告（用于llm_council或健康检查）, 记录一条信号轨迹到 logs/signal_trace.jsonl, _sha8() (+4 more)

### Community 69 - "signal_weight_updater.py"
Cohesion: 0.22
Nodes (12): _calc_rolling_wr(), _load_signal_weights(), _load_trades(), _new_multiplier(), signal_weight_updater.py — 结算闭环权重更新器 v1.0 设计院封印 2026-08-09 苏摩111 职责： 每次…, 根据实盘滚动WR平滑调整 multiplier。 保守更新：每次最多调整 ±0.1，避免剧烈波动。, 主入口：扫描实盘结算数据，动态更新 signal_weights.json。 返回： { 'updated': int, # 更新的key数量…, 加载simfactory_trades.jsonl，返回已结算记录 (+4 more)

### Community 70 - "evaluate_timing"
Cohesion: 0.19
Nodes (12): _check_bearish_4h_streak(), evaluate_timing(), format_timing_badge(), Any, 层3：Kronos p_up 评分 (0~20) p_up = 价格上涨概率 [0,1] 做空：p_up 越低越好 做多：p_up 越高越好 Pro 版：接入…, [P1-5实现 2026-07-16 苏摩111] BTC单边下行豁免通道：检查N根连续4H阴线, 时机过滤器主入口 Args: symbol: 交易对 signal_dir: 'LONG' 或 'SHORT' score: 梵天信号总分 grade:…, 层1：价格位置评分 (0~40) 价格在入场区间内 → 满分 偏离越远 → 分数越低 超过 6% → 0分（GapGate 信号过期机制） (+4 more)

### Community 71 - "anomaly_guards.py"
Cohesion: 0.21
Nodes (11): detect_correlation_alert(), detect_regime_switch_warning(), detect_vol_price_anomaly(), _fetch_klines(), fmt_no_bull_ob_template(), P1 量价异常检测 + P2 多币联动预警 + P3 框架切换机制 梵天设计院封印 2026-07-24 · 苏摩111批准 P1: 滞涨放量/瀑布启动识别…, 检测相关币种是否同步异动 current_drop_pct: 当前币种1H跌幅（负数表示下跌） 返回: {'alert': bool, 'message':…, BULL_TREND + BEAR_CHOCH → 体制转换警告（不封禁做空，只输出警告） BEAR_TREND + BULL_CHOCH →… (+3 more)

### Community 72 - "brahma_coordinator.py"
Cohesion: 0.30
Nodes (10): build_coord_context(), get_episodic_context(), get_ic_context(), get_macro_context(), get_oi_context(), get_pump_context(), _load_json(), 宏观引擎：FG + BTC.D + 下次FOMC (+2 more)

### Community 73 - "mode_c_detector.py"
Cohesion: 0.21
Nodes (11): detect(), ev_filter(), hedge_health(), _load_state(), quick_mode_check(), mode_c_detector.py — 梵天2.0 Phase 1a · MODE_C 庄家行情识别器 设计院×达摩院 封印 2026-07-20 职责：…, 对冲组合健康报告 返回多空比、净δ、强平缓冲、时间成本、建议操作, 计算入场期望值并给出入场建议 EV = WR×(position_pct×leverage×rr) -… (+3 more)

### Community 74 - "brahma_experience_distiller.py"
Cohesion: 0.23
Nodes (9): build_coin_wr_table(), distill(), infer_regime_from_case(), _is_win(), _norm_dir(), 返回多层索引： { "by_regime_dir_tf": { "BEAR_TREND:SHORT:4h": {"n":49, "wr":0.61,…, 格式: { "BTC": { "SHORT": {"4h": {"wr":0.61,"n":49}, "1h": {...}, ...}, "LONG":…, 多单收益>0为胜；空单收益<0为胜（future_return以多头视角计） (+1 more)

### Community 75 - "brahma_fullcycle_builder.py"
Cohesion: 0.32
Nodes (11): build_all(), build_cases_for_tf(), build_cases_macro_tf(), build_symbol_tf(), calc_atr(), calc_bbw(), calc_ema(), calc_rsi() (+3 more)

### Community 76 - "brahma_gateway.py"
Cohesion: 0.26
Nodes (11): classify_intent(), extract_symbol(), handle(), _handle_analyze(), _handle_execute(), _handle_portfolio(), _handle_scan(), brahma_gateway.py — 梵天统一入口网关 ══════════════════════════════════════ 设计院… (+3 more)

### Community 77 - "brahma_learning_loop.py"
Cohesion: 0.32
Nodes (11): fetch_price(), load_jsonl(), main(), now_utc(), Path, 基于 EV 统计，动态建议信号阈值 逻辑：找到 EV>0 且 WR>55% 的最低 score 桶作为新阈值, 从 wuqu_positions.json 读取持仓，实时拉取价格，输出盈亏快照 替代硬编码持仓列表, 消化信号校准日志，按 regime:direction:score_bucket 统计 WR / EV 写入 ev_buckets/ 和… (+3 more)

### Community 78 - "brahma_readiness.py"
Cohesion: 0.32
Nodes (10): _check_l1_data(), _check_l2_analysis(), _check_l3_decision(), _check_l4_execution(), _check_l5_monitor(), _get_cron_last_status(), _get_cron_status(), 从 runs JSONL 读最近状态，gateway restart中断不计入consecutive (+2 more)

### Community 79 - "BrainLogger"
Cohesion: 0.30
Nodes (5): BrainLogger, 直接输出，不加前缀（兼容现有 print(f'[s7-xxx]...') ）, 轻量日志器，每个模块一个实例 特性： - 线程安全（锁保护） - 异常自动格式化 - 支持tag子分类（如 log.info('...',…, _should_log(), Exception

### Community 80 - "cross_market_engine.py"
Cohesion: 0.32
Nodes (11): cross_market_score(), _get(), get_btc_eth_corr(), _get_closes(), get_dxy_proxy(), get_risk_regime(), _pct_returns(), _pearson() (+3 more)

### Community 81 - "klines_to_ohlcv"
Cohesion: 0.30
Nodes (11): klines_to_ohlcv(), analyze_multitf(), _ema(), _macd_signal(), multitf_score(), # STATUS: ACTIVE # 多时框架引擎，MTF计算 # LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链 #…, 六周期完整分析 返回： directions: {tf: dir} consensus: 加权共识方向 alignment: 对齐分数 0~10…, 多周期对齐评分接口 → 0~20分 替换/补充现有 趋势一致性 维度 (+3 more)

### Community 82 - "ic_tracker.py"
Cohesion: 0.24
Nodes (11): calc_ic(), compute_all_ic(), get_dim_weight_adjust(), load_ic_state(), load_signal_data(), ic_tracker.py — 维度IC（信息系数）滚动追踪 设计院 2026-06-30 | 补齐与顶级量化差距路径 IC = 维度分数 与 最终方向结果…, 计算所有维度的IC（按体制分组） 返回：{regime: {dim_key: ic_value}}, 根据IC返回维度权重调整建议 IC > 0.1 → 正向有效，权重+ IC < -0.1 → 反向有效（反转），权重- |IC| < 0.1 →… (+3 more)

### Community 83 - "brahma_wiring_check.py"
Cohesion: 0.27
Nodes (10): _check_caller(), _check_import(), _check_result_key(), 通过analyze()检查result_key是否出现, 静态并发安全扫描（路线A 2026-08-10） S1: scan_runtime_path_injection —…, 接入验证门：扫描零引用孤岛，分级报告。 返回: { 'critical': [...], # 高价值孤岛，健康扣分 'watch': [...], #…, run_check(), _run_static_concurrency_scan() (+2 more)

### Community 84 - "microstructure_engine.py"
Cohesion: 0.29
Nodes (10): _bus_price(), detect_absorption(), detect_order_flow_imbalance(), detect_price_stall(), _get(), microstructure_score(), 买卖失衡检测：连续N笔成交中买方/卖方主导比例 高度失衡 = 方向性压力，价格即将跟随, 检测价格在关键位附近的停顿行为 连续多根K线在同一价位附近 = 积累/分配 (+2 more)

### Community 85 - "predict_regime_proba"
Cohesion: 0.29
Nodes (10): _extract_features(), fit_hmm(), get_weighted_multiplier(), predict_regime_proba(), ndarray, regime_hmm_v2.py — 梵天Regime HMM概率模型 v2.0 设计院 P3-A | 2026-07-08 升级: 规则硬分类 →…, 主入口: 返回当前体制概率分布 Returns: { 'BEAR_TREND': 0.65, 'CHOP_MID': 0.22, 'BULL_TREND':…, 根据HMM概率分布返回加权乘数 替代当前硬切换的 REGIME_MULT 矩阵 Example: BEAR=0.65, CHOP=0.22,… (+2 more)

### Community 86 - "ssi_engine.py"
Cohesion: 0.27
Nodes (10): compute_ssi(), _detect_phase(), get_ssi_level(), is_squeeze_over(), _load_state(), ssi_engine.py — 梵天2.0 Phase 2a · 轧空强度指数 (Short Squeeze Index) 设计院×达摩院 封印…, 判定轧空生命周期阶段 返回 (phase_int, phase_name), 判断轧空是否已结束（阶段4/5持续N根K线） 返回 {over: bool, confidence: float, reason: str} (+2 more)

### Community 87 - "tradfi_signal_layer.py"
Cohesion: 0.27
Nodes (10): compute_tradfi_context(), _fetch_rwa_price(), _get_market_status(), _is_us_close_window(), _is_us_open_window(), _load_rwa_contracts(), 获取RWA市场状态（overnight/regular/premarket等）, 是否在美股开盘冲击波窗口（14:00~14:45 UTC，禁止新仓） (+2 more)

### Community 88 - "volume_exhaustion_score"
Cohesion: 0.25
Nodes (10): detect_pin_bar(), detect_volume_decay(), detect_volume_exhaustion(), detect_volume_price_divergence(), Pin Bar（钉形K线）： - LONG底部pin bar：长下影线 > 实体2倍，下影>上影3倍，收盘在上半段 - SHORT顶部pin bar：长上影线…, 底背离：价格创新低，但对应成交量不创新高（卖方越来越没力气） 顶背离：价格创新高，但对应成交量不创新高（买方越来越没动力）, 连续3根以上缩量 = 主动抛压/追涨动能终结, 综合量能衰竭评分，最高15分 用于 brahma_brain 评分流水线注入 返回： { 'score': int, # 0~15 'components':… (+2 more)

### Community 89 - "auto_review.py"
Cohesion: 0.29
Nodes (8): check_standby_violations(), compute_stats(), generate_review(), load_real_trades(), 单笔平仓即时复盘（由ws_guardian触发）, 扫描 STATUS: STANDBY 模块是否被非白名单文件引用 设计院 2026-07-01, review_single(), run_batch_review()

### Community 90 - "brahma_council.py"
Cohesion: 0.27
Nodes (8): call_reasoning(), council_review(), 三专家AI议会主入口 (MODE=live): RiskAgent → claude (风控+清算) MacroAgent → Qwen (宏观+DXY)…, 统一议会入口: - use_llm=True (默认): 三专家LLM议会 (score≥140时推荐) - use_llm=False: 纯规则议会…, review(), rule_council(), 调用llm_council_bridge.review()，3专家并行裁决。 返回 {final_adj, votes, council_summary}, step3_council()

### Community 91 - "brahma_fangcang_unified.py"
Cohesion: 0.27
Nodes (9): _genuine_breakout_weight(), _get_cases_adj(), _get_engine_adj(), brahma_fangcang_unified.py — 梵天统一方仓查询层 v1.0…, 检查案例库中假突破比例，高假突破率降低整体可信度。 返回权重乘数 [0.5, 1.0], 梵天统一方仓查询层。 合并系统1（K线结构）+ 系统2（案例库WR）→ unified_adj 参数： symbol: 交易对，如 BTCUSDT ms:…, 从 fangcang_engine 的输出解析方向信号，转换为 adj 分数。 返回 (adj: float, confidence: str, n: int), 从 fangcang_hcme_bridge 案例库匹配结果，转换为 adj 分数。 直接读案例库，不经过… (+1 more)

### Community 92 - "brahma_multiframe.py"
Cohesion: 0.29
Nodes (9): _empty(), opens_approx(), brahma_multiframe.py — 全周期FVG/OB扫描引擎 设计院封印 2026-08-20 苏摩指令：15m/1H/4H/日线/周线全周期验证…, 用上根收盘近似开盘（Binance K线无独立开盘价字段）, 全周期FVG/OB扫描主函数 返回：multiframe_context, mtf_bias, mtf_score_adj, mtf_summary, 扫描Order Block（最近N根K线）, scan(), _scan_fvg() (+1 more)

### Community 93 - "brahma_smoke_test.py"
Cohesion: 0.29
Nodes (5): call_reasoning(), AI议会信号门控。 接收 brahma_core 分析结果，调用 LLM 给出 PASS/WARN/BLOCK 裁决。 inject_context=True…, 调用 LLM 推理。返回字符串响应，失败返回 None。 model: 'advanced'(bedrock-claude) |…, reasoning_gate(), _rule_fallback()

### Community 94 - "fangcang_tradfi_db.py"
Cohesion: 0.40
Nodes (9): _build(), _clip(), _ensure(), get_index_info(), _normalize_bbw(), query_tradfi(), fangcang_tradfi_db.py — TradFi方仓向量检索库 v1.0 设计院封印 2026-08-10 苏摩111 S级(7只):…, 查询最相似的TradFi历史方仓案例。 参数: token — Binance代币符号（如 NVDAUSDT） bb_width_raw —… (+1 more)

### Community 95 - "compute_gex"
Cohesion: 0.33
Nodes (9): bs_gamma(), compute_gex(), _deribit_get(), main(), _parse_instrument(), s22 GEX 评分: -10 ~ +8 规则: 负GEX regime（做市商空Gamma，放大波动）: SHORT 方向 → 波动放大有利于做空 →…, BTC-10JUN26-61000-C → {strike, type, exp_ms}, 从 Deribit 获取期权数据并计算 GEX 分布 返回: total_gex : 总净GEX (USD) call_gex : Call侧GEX… (+1 more)

### Community 96 - "online_learner_v2.py"
Cohesion: 0.33
Nodes (9): calibrate_weights(), compute_dimension_bias(), load_performance(), load_weights(), 计算各维度的预测偏差 偏差 = (实际胜率 - 预期胜率) / 预期胜率, 根据偏差校准权重 正偏差（实际>预期）→ 增加权重 负偏差（实际<预期）→ 降低权重, run(), save_weights() (+1 more)

### Community 97 - "signal_integrity_gate.py"
Cohesion: 0.22
Nodes (8): gate_check(), get_dynamic_regime_mult(), SignalIntegrityGate — P0~P2 信号完整性门控 梵天设计院封印 2026-07-26 · 苏摩授权自主执行 P0: consensus…, 主入口：从brahma_engine的cf/params/ms中提取字段，执行P0+P1检查 Returns (pass: bool, reason: str), P0~P2 信号完整性门控 在brahma_engine最终 _valid 计算前调用 返回 (pass: bool, reason: str), Returns (True, '') if signal passes all gates Returns (False, reason) if…, P2: 体制动态衰减 Returns (adjusted_mult: float, note: str), SignalIntegrityGate

### Community 98 - "brahma_guardian.py"
Cohesion: 0.31
Nodes (5): full_guardian_check(), 一次调用完成所有健康检查 # ponytail: 5个函数合1，调用方只需要一行, run_ci(), run_cron_doctor(), run_health_check()

### Community 99 - "rl_position_ab.py"
Cohesion: 0.33
Nodes (8): decide_position_size(), evaluate_ab_performance(), get_rl_suggestion(), _load_state(), 评估A/B两组历史表现，决定是否升级ab_ratio, 获取RL模型的仓位建议 当前: SHADOW模式用规则近似（torch RL模型接入后替换此函数） Returns: {'size_mult': float,…, A/B分流主入口 Args: signal_id: 信号ID（用于确定性分流） std_nav_pct: 梵天标准仓位比例（如0.075） Returns:…, _save_state()

### Community 100 - "archive/sentiment_engine.py"
Cohesion: 0.33
Nodes (8): analyze(), _fg_to_score(), _fg_trend_score(), _get_fg(), _get_fg_history(), # ── STATUS: AUXILIARY ────────────────────────────────────────── # 情绪分析引擎，s8辅助…, 主接口：返回 sentiment_nlp 标准字典, FNG趋势加分 v1.2 (设计院 2026-06-29) SHORT: FNG连续3天下降（市场情绪恶化）→ SHORT割势 +3分…

### Community 101 - "bulk_update_from_api"
Cohesion: 0.28
Nodes (8): bulk_update_from_api(), get_best_price(), get_live_price(), 批量从 REST API 更新价格（ws_guardian 启动时或空仓时调用）, ws_guardian 每次收到 markPrice 时调用, 读取最新实时价格。 降级链：WS文件（30s内）→ 0（让调用方降级到ticker）, 返回最优价格 + 来源标注 优先级: WS实时(30s) > ticker.lastPrice(15s) > k1h[-1], update_price()

### Community 102 - "realtime_liq_tracker.py"
Cohesion: 0.31
Nodes (8): _empty_liq_result(), get_liq_score(), get_recent_liq(), # STATUS: ACTIVE # 实时清算追踪，WebSocket # LAST_REVIEW: 2026-07-01 |…, 从 ws_guardian_state.json 读取清算事件, 返回 (加分, 描述) 供 brahma_core 调用, 读取近 N 分钟清算统计 返回： long_liq_usd : 多单被清算总额（USD） short_liq_usd : 空单被清算总额（USD）…, _read_from_ws_guardian_state()

### Community 103 - "tradfi_router.py"
Cohesion: 0.33
Nodes (8): classify(), compute_router_delta(), get_session(), get_tradfi_report_header(), TradFi路由器核心函数：根据A/B/C类返回评分调整delta + 操作标签。 Args: symbol: 分析标的，如 'NVDAUSDT'…, 生成分析报告头部TradFi标注（供Jarvis推送格式使用）。, 品种分类。返回 'A' / 'B' / 'C' / 'CRYPTO'。 CRYPTO = 普通加密合约，走原始梵天逻辑，不经过此路由器。, 当前美股时段信息。 Returns: { 'session': 'ASIA' | 'PREOPEN' | 'REGULAR' | 'AFTERHOURS',…

### Community 104 - "brahma_trade.py"
Cohesion: 0.29
Nodes (7): calc_trade_params(), _nearest_swing_above(), _nearest_swing_below(), 找到入场价上方最近的摆动高点（用于做空止损）, 找到入场价下方最近的摆动低点（用于做多止损）, [已移至 brahma_core_entry.py 2026-07-01] 向后兼容别名, rebase_params()

### Community 105 - "exception_injector.py"
Cohesion: 0.46
Nodes (7): classify_function(), FunctionReport, generate_patch_template(), generate_report(), 为指定层生成异常处理补丁模板 输出每个裸函数应该添加什么样的try-except, scan_file(), scan_layer()

### Community 106 - "decision_weight_matrix.py"
Cohesion: 0.29
Nodes (6): apply_to_signal(), fuse_score(), _normalize_ai_adj(), 直接接收 brahma_core 信号 + reasoning_gate 结果，返回融合决策 signal: brahma_core.analyze()…, 将AI议会的final_adj(-25~+12) 归一化为35维分等比的调整值 公式: adj_norm = final_adj * (35维均值150 /…, 融合35维评分 + AI议会裁决 → 最终综合分 + 优先级 参数: score_35d : brahma_core 35维评分 (典型范围 80-200)…

### Community 107 - "failure_pattern_db.py"
Cohesion: 0.36
Nodes (6): get_current_risk_score(), get_failure_patterns(), get_stats(), _load_records(), 查询当前组合的历史失败模式 返回: { 'total': int, 'loss_n': int, 'loss_rate': float,…, 实时风险评分: 当前信号组合的历史失败率查询 供analyze()注入，让梵天在信号发出前知道历史失败率

### Community 108 - "_extract_features"
Cohesion: 0.29
Nodes (8): _calc_bollinger_width(), _calc_rsi(), _extract_15m_features(), _extract_features(), RSI计算 — 委托math_utils统一实现 [2026-08-24 设计院精简], 计算布林带宽度百分比（BBW = (upper-lower)/middle * 100）, 对最近 48 根15m K线（12H）提取微结构特征： - choch_count: CHoCH次数（价格跌破前低或突破前高） - bos_count:…, 提取25维特征向量 维度拆分： - 原有10维（价格形态 norm_closes + 9个scalar） -…

### Community 109 - "signal_lifecycle.py"
Cohesion: 0.25
Nodes (7): audit_score_with_realtime(), calc_dynamic_tp(), P3 信号生命周期管理 + P4 TP动态计算 + P5 评分数据实时性 梵天设计院封印 2026-07-26 · 苏摩授权自主执行, P4: 基于清算集群密度动态计算TP1/TP2 Returns (tp1, tp2, method), 检查所有OPEN信号的生命周期状态 返回需要推送的告警列表, P5: 对关键评分维度附上实时原始数据 返回增强的breakdown字典，每个维度附上2~3个原始指标值, tick_signal_lifecycle()

### Community 110 - "volume_engine.py"
Cohesion: 0.43
Nodes (7): analyze_volume(), calc_obv(), calc_volume_profile(), calc_vwap(), detect_obv_divergence(), 计算VWAP（按session，默认96根1H K线≈4天）, volume_score()

### Community 111 - "volume_profile.py"
Cohesion: 0.36
Nodes (7): _empty(), _fetch_klines(), get_volume_profile(), get_vp_score(), volume_profile.py — 成交量分布密度分析（Volume Profile） 设计院·达摩院 三院审核修复 2026-07-08 职责： 1.…, 供 brahma_core s8 调用的评分接口, 计算当前价格区间的成交密度 返回： density_ratio : 当前区间密度 / 均值密度 density_label : HIGH_DENSITY /…

### Community 112 - ".klines"
Cohesion: 0.29
Nodes (4): K线数据（60s缓存） 返回格式：[[open_time, open, high, low, close, volume, ...], ...], 返回 (opens, highs, lows, closes, volumes), RSI 序列（Wilder平滑） series: 收盘价序列（升序，最新在末尾） period: RSI周期（默认14） 返回：RSI 序列（长度 =…, _rsi()

### Community 113 - "brahma_intel_layer.py"
Cohesion: 0.33
Nodes (4): get_today_timeline(), identify_pattern(), 识别当前市场情境类型 返回：pattern_type, confidence, intent, best_strategy, summarize_intent()

### Community 114 - "enrich_signal_grade"
Cohesion: 0.33
Nodes (5): enrich_signal_grade(), parse_grade(), grade_utils.py — 梵天全局grade解析工具 设计院封印 2026-07-22 苏摩111 问题根因： - brahma_core.py /…, 统一grade解析函数 — 所有模块的唯一入口 优先级： 1. grade_val 是 int/float → 直接用 2. grade_val 是字符串 →…, 给信号字典注入 grade_num 字段（整数），不修改原始 grade 字段。 在信号写入 live_signal_log.jsonl 之前调用。…

### Community 115 - "lsr_oi_score"
Cohesion: 0.38
Nodes (6): lsr_oi_score(), lsr_score(), oi_direction_score(), OI方向与价格变化交叉验证评分 核心洞察（今日ETH实证）： OI减少 + 价格上涨 = 空头回补 → 反弹质量差，不可持续 OI增加 + 价格上涨 =…, 联合评分入口 — 供brahma_brain.py调用 优先使用传入参数（已从market_state拉取）， fallback到实时API拉取。…, 多空比逆向评分 Args: long_pct: 多头占比（%），如 70.9 signal_dir: 'LONG' 或 'SHORT' Returns:…

### Community 116 - "scan_structure"
Cohesion: 0.43
Nodes (6): format_report(), main(), _push(), openclaw message send 直接推送，保留换行格式, 扫描 OB / LIQ / GEX / FVG 四维结构，返回综合结果字典, scan_structure()

### Community 117 - "modules_deprecated_20260811/signal_selector.py"
Cohesion: 0.47
Nodes (4): _build_signal(), 核心裁决函数 参数： short_analysis : brahma_analyze(symbol, 'SHORT') 结果 long_analysis :…, _regime_summary(), select()

### Community 118 - "confluence_score"
Cohesion: 0.33
Nodes (5): confluence_score(), 150分共振评分引擎 基于 skills/ta-engine/references/analysis_engine.md, get_regime_mult(), regime_config.py — 梵天体制×方向乘数矩阵 SSOT 设计院 2026-08-24 从brahma_core.py提取封印 职责:…, 统一入口：根据标的、体制、方向返回乘数 brahma_core.confluence_score() 调用此函数替代内联矩阵

### Community 119 - "fangcang_builder_30.py"
Cohesion: 0.60
Nodes (5): build_all(), build_cases(), calc_bbw(), calc_rsi(), fetch_klines()

### Community 120 - "_scan_history"
Cohesion: 0.33
Nodes (6): _bbw_golden_zone_bonus(), [设计院封印 2026-08-09 苏摩111] BBW黄金区间相似度奖励（减小相似度距离 = 提升优先级） 铁证：6.5年3071案例深度验证…, 25维综合相似度得分（越小越相似） 权重分配： 价格形态 40% + 振幅 12% + 移动 12% + RSI 16% + 成交量趋势 5% + BBW…, 扫描历史，返回最相似TOP_N案例列表 每条包含：dt / score / future_ret / future_max / future_min /…, _scan_history(), _similarity_score()

### Community 121 - "get_har_rv"
Cohesion: 0.47
Nodes (5): _calc_realized_vol(), _fetch_klines(), get_har_rv(), 计算n根K线的已实现波动率（对数收益率标准差×√n）, 计算HAR-RV预测值和评分调整 Returns: dict with keys: rv_1d, rv_5d, rv_22d, rv_forecast,…

### Community 122 - "get_hurst"
Cohesion: 0.47
Nodes (5): calc_hurst_rs(), _fetch_closes(), get_hurst(), 计算Hurst指数并返回体制验证结果 Returns: dict with: H, regime_validated, trend_strength,…, R/S分析法计算Hurst指数 使用对数收益率序列的R/S统计量

### Community 123 - "llm_council.py"
Cohesion: 0.33
Nodes (5): council_verdict(), format_verdict_line(), llm_council.py — 梵天本地LLM Council裁决层 设计院 2026-08-06 自主创建 职责：…, 格式化为vip_template_F.md的LLM行： LLM: 偏多 — 体制顺势，SMC结构强, 返回: bias: '偏多' | '偏空' | '中性' reason: str (≤30字) action: 'ENTER' | 'WAIT' |…

### Community 124 - "analyze_orderbook"
Cohesion: 0.47
Nodes (4): analyze_orderbook(), _cached(), get_depth(), 分析订单簿，返回评分和关键指标 signal_dir: LONG | SHORT

### Community 125 - "tradfi_macro_gate.py"
Cohesion: 0.40
Nodes (5): compute_tradfi_macro_gate(), _load_macro_state(), tradfi_macro_gate.py — 美股代币宏观联动门控 [设计院 2026-08-11 苏摩111封印] 整体落地，非补丁式…, 安全读取 macro_state.json，失败返回空字典, 计算宏观联动门控评分 Args: symbol: 当前分析标的 direction: 'LONG' 或 'SHORT' asset_type:…

### Community 126 - "tradfi_sector_engine.py"
Cohesion: 0.33
Nodes (5): compute_tradfi_sector_score(), get_quick_rsi_1h(), tradfi_sector_engine.py — 美股代币板块联动评分引擎 [设计院 2026-08-11 苏摩111封印] 整体落地，非补丁式…, 轻量级RSI_1H获取（不走brahma_bus缓存，直接取最近数据） 用于板块联动扫描时快速获取同组成员数据, 计算板块联动评分 Args: symbol: 当前分析标的，如 'MUUSDT' direction: 'LONG' 或 'SHORT' rsi_1h_fn:…

### Community 127 - "run_batch"
Cohesion: 0.40
Nodes (4): 多标的并发分析 — 封印版唯一入口 规则： - 必须走 brahma_parallel_engine.batch_analyze() - 4x加速，数据层通过…, run_batch(), berr(), brahma_log.py — 日志函数shim（兼容层） 设计院 2026-08-10 | 精简后恢复 提供 berr() 函数，委托给…

### Community 128 - "_auto_heal_suggestions"
Cohesion: 0.33
Nodes (4): _auto_heal_suggestions(), _check_signal_card_importable(), 根据健康报告输出自愈建议 返回：[{'action': str, 'command': str, 'priority': int}], signal_card_formatter已归档，改检测brahma_signal [2026-08-24 设计院精简]

### Community 129 - "_check_log_health"
Cohesion: 0.50
Nodes (4): _check_log_health(), 检查 brainlog 错误/警告计数 高错误率 = 系统内部异常信号, get_stats(), 供 brahma_health 调用，返回错误/警告计数

## Knowledge Gaps
- **1 isolated node(s):** `BrahmaEvent`
  These have ≤1 connection - possible missing edges or undocumented components.
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `analyze()` connect `analyze` to `smc_engine.py`, `brahma_kronos.py`, `brahma_macro.py`, `get_klines`, `get_narrative_score`, `brahma_core_step4.py`, `BrahmaEventBus`, `sl_bandit.py`, `apply_tf_confluence`, `gex_scanner.py`, `calc_rsi`, `brahma_core.py`, `brahma_pipeline.py`, `BrahmaBus`, `_f`, `antifragile_guard.py`, `position_sizer.py`, `capital_allocator.py`, `brahma_fangcang_unified.py`, `brahma_smoke_test.py`, `compute_gex`, `bulk_update_from_api`, `lsr_oi_score`, `confluence_score`?**
  _High betweenness centrality (0.072) - this node is a cross-community bridge._
- **Why does `_f()` connect `_f` to `brahma_kronos.py`, `analyze`, `brahma_onchain.py`, `brahma_order_engine.py`, `brahma_position.py`, `brahma_macro.py`, `brahma_analysis_runner.py`, `brahma_ml_engine.py`, `brahma_council.py`?**
  _High betweenness centrality (0.062) - this node is a cross-community bridge._
- **Why does `BrahmaBus` connect `BrahmaBus` to `analyze`, `brahma_health.py`, `brahma_core_step4.py`, `.klines`, `apply_tf_confluence`, `brahma_core.py`?**
  _High betweenness centrality (0.060) - this node is a cross-community bridge._
- **Are the 64 inferred relationships involving `_f()` (e.g. with `get_adaptive_weight()` and `get_har_rv_forecast()`) actually correct?**
  _`_f()` has 64 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `_analyze_step4()` (e.g. with `Exception` and `sentiment_score()`) actually correct?**
  _`_analyze_step4()` has 3 INFERRED edges - model-reasoned connections that need verification._
- **What connects `BrahmaEvent` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `smc_engine.py` be split into smaller, more focused modules?**
  _Cohesion score 0.06936026936026936 - nodes in this community are weakly interconnected._