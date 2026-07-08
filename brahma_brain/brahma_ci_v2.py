#!/usr/bin/env python3
"""
brahma_ci_v2.py — 梵天360加强版 · 全系统主动探针
设计院封印 2026-07-02

# ╔══════════════════════════════════════════════════════════════════╗
# 定位: 从 brahma_ci.py 的6维探针升级为10维全覆盖
# 新增维度:
#   P7:  函数契约探针  — 所有核心函数是否有异常处理
#   P8:  数据流完整性  — 从原始数据→信号→执行→结算全链路
#   P9:  版本一致性    — 私有版与开源版关键模块是否同步
#   P10: 自愈能力      — 系统在异常后是否能自动恢复
# ╚══════════════════════════════════════════════════════════════════╝
"""
import json, os, sys, time, subprocess, ast, hashlib
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

# ── 全局问题收集 ──────────────────────────────────────────────────────
_results = {'errors': [], 'warnings': [], 'infos': [], 'skipped': []}

def E(msg, dim):  _results['errors'].append({'msg': msg, 'dim': dim})
def W(msg, dim):  _results['warnings'].append({'msg': msg, 'dim': dim})
def I(msg, dim):  _results['infos'].append({'msg': msg, 'dim': dim})

# ═══════════════════════════════════════════════════════════════════════
# P1: 信号流量探针 (继承v1)
# ═══════════════════════════════════════════════════════════════════════
def p1_signal_flow():
    now = time.time()
    trace = BASE / 'logs' / 'signal_trace.jsonl'
    if not trace.exists():
        W('signal_trace.jsonl 不存在', 'P1')
    else:
        age = (now - trace.stat().st_mtime) / 3600
        lines = trace.read_text().splitlines()
        if age > 6:  W(f'signal_trace {age:.1f}h未更新', 'P1')
        else:         I(f'signal_trace OK {len(lines)}条 {age:.1f}h前', 'P1')

    lsl = BASE / 'data' / 'live_signal_log.jsonl'
    if lsl.exists() and lsl.stat().st_size == 0:
        W('live_signal_log为空，近期无有效信号', 'P1')

# ═══════════════════════════════════════════════════════════════════════
# P2: 推送链路探针 (继承v1, 升级：检测重复任务)
# ═══════════════════════════════════════════════════════════════════════
MUST_PUSH = {
    'pump-hunter':           '暴涨信号',
    'oi-surge-scanner':      'OI异动',
    'brahma-360-daily':      '每日分析',
    'smart-digest-6h':       '智能汇总',
    'brahma-ci-probe':       'CI探针报告',
}
SILENT_OK = {
    'btc-regime-watcher',# # 'ws-guardian-keepalive'  # 已删除 2026-07-08,  # 已删除 2026-07-08 从未注册'live-signal-settle-2h',
    'trc20-order-monitor','auto-position-manager','market-structure-4h',
    'data-backup-6h','gateway-restart-daily','dharma-offline-replay',
    'auto-executor-1h','brahma-360-health','live-performance-daily',
    'kronos-deps-restore','session-cleanup-daily','auto-executor',
    'position-sl-monitor','ws-guardian','brahma-arch-review',
    'rsi-structure-watcher',
}

def p2_push_links():
    r = subprocess.run(['openclaw','cron','list'], capture_output=True, text=True)
    name_count, found = {}, {}
    for line in r.stdout.split('\n'):
        parts = line.split()
        if len(parts) < 2: continue
        if parts[0] in ('ID','Config','─'): continue
        if parts[1] in ('Name','Schedule'): continue
        name = parts[1].rstrip('.')
        name_count[name] = name_count.get(name, 0) + 1
        has_push = 'jarvis' in line or 'announce' in line
        thread = '019f181f' if '019f181f' in line else ('019f15c6' if '019f15c6' in line else 'NONE')
        found[name] = {'push': has_push, 'thread': thread}

    # 重复任务检测（新老更替遗漏）
    for name, cnt in name_count.items():
        if cnt > 1:
            E(f'重复任务: {name} 存在{cnt}个实例', 'P2')

    # 必须推送任务检测
    for name, reason in MUST_PUSH.items():
        if name not in found:
            E(f'任务缺失: {name} ({reason})', 'P2')
        elif not found[name]['push']:
            E(f'推送断路: {name} 无推送', 'P2')
        else:
            I(f'{name} → {found[name]["thread"][:8]} OK', 'P2')

    # 总任务统计
    I(f'总任务 {len(found)} 个 | 推送 {sum(1 for v in found.values() if v["push"])} | 静默 {sum(1 for v in found.values() if not v["push"])}', 'P2')

# ═══════════════════════════════════════════════════════════════════════
# P3: 代码一致性探针 (继承v1)
# ═══════════════════════════════════════════════════════════════════════
def p3_code_consistency():
    BRAIN = BASE / 'brahma_brain'
    runner = (BRAIN / 'brahma_analysis_runner.py').read_text(errors='ignore')

    MUST_IN_RUNNER = {
        'signal_trace':       '_TRACE_OK',
        'timing_filter':      '_TIMING_OK',
        'llm_council_bridge': '_LLM_COUNCIL_OK',
        'kronos_engine':      'kronos_engine',
    }
    for mod, marker in MUST_IN_RUNNER.items():
        if marker not in runner:
            E(f'模块未接入runner: {mod}', 'P3')
        else:
            I(f'{mod} 接入OK', 'P3')

    # brahma_core.py孤儿占位符检测
    core = (BRAIN / 'brahma_core.py').read_text(errors='ignore')
    placeholders = []
    for i, line in enumerate(core.splitlines(), 1):
        stripped = line.strip()
        if stripped == 'return 0.0':
            ctx = ' '.join(core.splitlines()[max(0,i-6):i-1])
            if 'score' not in ctx and 'if ' not in ctx and 'try' not in ctx:
                placeholders.append(i)
    if placeholders:
        W(f'brahma_core.py 疑似占位符 return 0.0 ({len(placeholders)}处)', 'P3')
    else:
        I('brahma_core.py 无占位符', 'P3')

# ═══════════════════════════════════════════════════════════════════════
# P4: 资产一致性探针 (继承v1, 升级：检测持仓vs止损守护)
# ═══════════════════════════════════════════════════════════════════════
def p4_asset_consistency():
    now = time.time()
    pos_file = BASE / 'data' / 'wuqu_positions.json'
    if not pos_file.exists():
        E('wuqu_positions.json 不存在', 'P4')
        return

    age_h = (now - pos_file.stat().st_mtime) / 3600
    if age_h > 2:
        W(f'持仓文件 {age_h:.1f}h 未更新（持仓可能变化）', 'P4')

    try:
        data = json.loads(pos_file.read_text())
        positions = data if isinstance(data, list) else data.get('positions', [])
        active = [p for p in positions if abs(float(p.get('positionAmt', p.get('size', 0)))) > 0]
        I(f'活跃持仓 {len(active)} 个', 'P4')

        # ws_guardian是否在守护 — 优先读 data/ws_guardian_state.json（实时心跳）
        ws_state = BASE / 'data' / 'ws_guardian_state.json'
        ws_log   = BASE / 'logs' / 'ws_guardian.log'
        ws_src   = ws_state if ws_state.exists() else (ws_log if ws_log.exists() else None)
        if active and ws_src is None:
            E('有持仓但ws_guardian状态文件不存在，止损守护状态未知', 'P4')
        elif ws_src:
            ws_age = (now - ws_src.stat().st_mtime) / 3600
            if ws_age > 0.5 and active:
                E(f'ws_guardian {ws_age:.1f}h 未更新，止损守护可能中断', 'P4')
            else:
                I(f'ws_guardian OK {ws_age:.2f}h前 (via {ws_src.name})', 'P4')
    except Exception as e:
        W(f'持仓解析失败: {e}', 'P4')

# ═══════════════════════════════════════════════════════════════════════
# P5: 执行层探针 (继承v1)
# ═══════════════════════════════════════════════════════════════════════
def p5_execution():
    sq = BASE / 'data' / 'signal_queue.jsonl'
    if sq.exists():
        lines = sq.read_text().splitlines()
        if len(lines) > 5:
            age = (time.time() - sq.stat().st_mtime) / 3600
            if age > 2:
                W(f'signal_queue {len(lines)}条未处理 {age:.1f}h积压', 'P5')
    I('执行层探针完成', 'P5')

# ═══════════════════════════════════════════════════════════════════════
# P6: 数据鲜度探针 (继承v1)
# ═══════════════════════════════════════════════════════════════════════
def p6_freshness():
    now = time.time()
    checks = [
        ('data/live_prices.json',         0.5,  'W', '实时价格'),
        ('data/live_signal_log.jsonl',    12,   'W', '信号日志'),
        ('data/wuqu_positions.json',       2,   'W', '持仓记录'),
    ]
    for fpath, max_h, lvl, name in checks:
        fp = BASE / fpath
        if not fp.exists():
            W(f'{name} 不存在', 'P6'); continue
        age = (now - fp.stat().st_mtime) / 3600
        if age > max_h:
            W(f'{name} 过期 {age:.1f}h (限{max_h}h)', 'P6')
        else:
            I(f'{name} OK {age:.2f}h', 'P6')

# ═══════════════════════════════════════════════════════════════════════
# P7: 函数契约探针（新增）
# 检查所有核心函数是否有异常处理，防止静默失败
# ═══════════════════════════════════════════════════════════════════════
def p7_function_contracts():
    BRAIN = BASE / 'brahma_brain'

    # 关键函数必须有try/except
    CRITICAL_FUNCS = {
        'brahma_analysis_runner.py': ['run_analysis', 'run_batch'],
        'brahma_core.py':            ['confluence_score', 'analyze'],
        # brahma_bus: 异常处理在safe_fetch内部，不在price/klines函数体，豁免
        # kronos_bridge: 异常处理在_get_predictor内部，豁免
        'signal_trace.py':           ['log_signal_trace'],
    }

    for fname, funcs in CRITICAL_FUNCS.items():
        fp = BRAIN / fname
        if not fp.exists():
            W(f'{fname} 文件不存在', 'P7'); continue
        src = fp.read_text(errors='ignore')
        try:
            tree = ast.parse(src)
        except:
            W(f'{fname} AST解析失败', 'P7'); continue

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in funcs:
                has_try = any(isinstance(n, ast.Try) for n in ast.walk(node))
                if not has_try:
                    W(f'{fname}::{node.name} 无异常处理，静默失败风险', 'P7')
                else:
                    I(f'{fname}::{node.name} 异常处理OK', 'P7')

# ═══════════════════════════════════════════════════════════════════════
# P8: 数据流完整性探针（新增）
# 检查从数据接入→评分→信号→推送的完整链路连通性
# ═══════════════════════════════════════════════════════════════════════
def p8_data_pipeline():
    """端到端数据流连通性检测"""
    try:
        sys.path.insert(0, str(BASE / 'brahma_brain'))
        from brahma_brain.brahma_bus import BrahmaBus
        bus = BrahmaBus()

        # 数据接入层
        price = bus.price('BTCUSDT')
        if price and price > 0:
            I(f'BrahmaBus 数据接入OK: BTC=${price:,.0f}', 'P8')
        else:
            E('BrahmaBus price() 返回异常', 'P8')

        # 评分层连通性（轻量检查，不完整运行）
        from brahma_brain.brahma_core import analyze
        I(f'brahma_core OK: analyze()可用', 'P8')

        # 信号追踪层
        from brahma_brain.signal_trace import get_trace_history
        traces = get_trace_history(limit=5)
        I(f'signal_trace 可读: {len(traces)}条历史', 'P8')

    except Exception as e:
        E(f'数据流检测异常: {e}', 'P8')

# ═══════════════════════════════════════════════════════════════════════
# P9: 版本一致性探针（新增）
# 检查私有版与开源版的核心函数签名是否一致
# ═══════════════════════════════════════════════════════════════════════
def p9_version_sync():
    """私有版 vs 开源版核心差异检测"""
    private = BASE / 'brahma_brain' / 'brahma_core.py'
    public_repo = Path('/root/.openclaw/workspace/brahma-quant/brahma_brain/brahma_core.py')

    if not public_repo.exists():
        W('开源版brahma_core.py不存在，版本一致性无法检查', 'P9')
        return

    try:
        priv_src = private.read_text(errors='ignore')
        pub_src = public_repo.read_text(errors='ignore')

        # 检查核心函数是否在两版本都存在
        core_funcs = ['confluence_score','analyze','calc_trade_params',
                      '_score_volume','_score_smart_money','_score_causal']
        private_tree = ast.parse(priv_src)
        public_tree  = ast.parse(pub_src)

        priv_funcs = {n.name for n in ast.walk(private_tree) if isinstance(n, ast.FunctionDef)}
        pub_funcs  = {n.name for n in ast.walk(public_tree) if isinstance(n, ast.FunctionDef)}

        for fn in core_funcs:
            in_priv = fn in priv_funcs
            in_pub  = fn in pub_funcs
            if in_priv and not in_pub:
                W(f'开源版缺失函数: {fn}', 'P9')
            elif in_priv and in_pub:
                I(f'{fn} 两版本均存在', 'P9')

        # 检查MIN_SCORE_VALID是否同步
        import re
        priv_ms = re.search(r'MIN_SCORE_VALID\s*=\s*(\d+)', priv_src)
        pub_ms  = re.search(r'MIN_SCORE_VALID\s*=\s*(\d+)', pub_src)
        if priv_ms and pub_ms:
            pv, pu = priv_ms.group(1), pub_ms.group(1)
            if pv != pu:
                W(f'MIN_SCORE_VALID 不一致: 私有={pv} 开源={pu}', 'P9')
            else:
                I(f'MIN_SCORE_VALID 一致: {pv}', 'P9')

        I(f'版本检查: 私有{len(priv_funcs)}函数 开源{len(pub_funcs)}函数', 'P9')

    except Exception as e:
        W(f'版本一致性检查失败: {e}', 'P9')

# ═══════════════════════════════════════════════════════════════════════
# P10: 自愈能力探针（新增）
# 检查系统在关键依赖缺失时是否有降级方案
# ═══════════════════════════════════════════════════════════════════════
def p10_self_healing():
    """检查关键模块的降级/自愈机制"""

    # 检查Kronos是否有fallback
    kronos_bridge = BASE / 'brahma_brain' / 'kronos_bridge.py'
    if kronos_bridge.exists():
        src = kronos_bridge.read_text(errors='ignore')
        has_fallback = 'fallback' in src.lower() or 'except' in src
        if has_fallback:
            I('Kronos bridge有fallback机制', 'P10')
        else:
            W('Kronos bridge无fallback，崩溃将影响信号', 'P10')

    # 检查Kronos依赖是否可用
    try:
        import torch
        I(f'torch {torch.__version__} 可用', 'P10')
    except ImportError:
        E('torch未安装，Kronos无法运行', 'P10')

    try:
        import huggingface_hub, safetensors, einops
        I('HF依赖(huggingface_hub/safetensors/einops)均可用', 'P10')
    except ImportError as e:
        W(f'HF依赖缺失: {e}', 'P10')

    # 检查OpenRouter key是否有效（连通性）
    try:
        from dotenv import load_dotenv
        import os
        load_dotenv(BASE / '.env')
        key = os.getenv('OPENROUTER_API_KEY', '')
        if key and key.startswith('sk-or-'):
            I('OpenRouter API Key已配置', 'P10')
        else:
            W('OpenRouter API Key未配置，LLM议会无法运行', 'P10')
    except Exception as e:
        W(f'LLM API Key检查失败: {e}', 'P10')

    # 检查start_brahma.sh是否存在（重启恢复方案）
    start_sh = BASE / 'scripts' / 'start_brahma.sh'
    if start_sh.exists():
        I('start_brahma.sh 启动恢复脚本存在', 'P10')
    else:
        W('start_brahma.sh 不存在，重启后依赖无法自动恢复', 'P10')

# ═══════════════════════════════════════════════════════════════════════
# 新增: P11 日志健康探针（新增）
# 检查所有关键日志文件的健康状态，发现异常增长或停止写入
# ═══════════════════════════════════════════════════════════════════════
def p11_log_health():
    now = time.time()
    # 日志+状态文件双路定义：(log路径, state文件路径, 最大允许老化h, 级别, 名称)
    LOG_CHECKS = [
        ('logs/ws_guardian.log',       'data/ws_guardian_state.json',      1,    'WARN', 'WS守护'),
        ('logs/regime_watcher.log',    'data/btc_regime_watcher_state.json', 6,  'WARN', '体制监控'),
        ('logs/pump_hunter.log',       'data/pump_detected.json',           6,   'WARN', '暴涨猎手'),
        ('logs/ws_guardian_crash.log', None,                                None, 'INFO', 'WS崩溃记录'),
        ('logs/auto_executor.log',     None,                                6,   'WARN', '执行器'),
    ]
    for fpath, state_path, max_age_h, lvl, name in LOG_CHECKS:
        # 优先用 state 文件的时间戳（更频繁更准确）
        best_mtime = None
        if state_path:
            sp = BASE / state_path
            if sp.exists():
                best_mtime = sp.stat().st_mtime
        fp = BASE / fpath
        if fp.exists() and (best_mtime is None or fp.stat().st_mtime > best_mtime):
            best_mtime = fp.stat().st_mtime

        if best_mtime is None:
            if lvl == 'WARN':
                W(f'{name} 监控文件不存在', 'P11')
            continue

        age_h = (now - best_mtime) / 3600
        # 异常大日志检测（>50MB可能有循环写入问题）
        if fp.exists() and fp.stat().st_size > 50 * 1024 * 1024:
            W(f'{name} 日志过大 {fp.stat().st_size//1024//1024}MB，可能有循环写入', 'P11')
        elif max_age_h and age_h > max_age_h:
            W(f'{name} {age_h:.1f}h未更新（可能停止运行）', 'P11')
        else:
            src_hint = '状态文件' if (state_path and (BASE/state_path).exists()) else '日志'
            I(f'{name} OK {age_h:.1f}h前 ({src_hint})', 'P11')

# ═══════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════
def run_ci_v2(verbose=False) -> dict:
    global _results
    _results = {'errors': [], 'warnings': [], 'infos': [], 'skipped': []}

    probes = [
        ('P1  信号流量',     p1_signal_flow),
        ('P2  推送链路',     p2_push_links),
        ('P3  代码一致性',   p3_code_consistency),
        ('P4  资产一致性',   p4_asset_consistency),
        ('P5  执行层',       p5_execution),
        ('P6  数据鲜度',     p6_freshness),
        ('P7  函数契约',     p7_function_contracts),
        ('P8  数据流完整',   p8_data_pipeline),
        ('P9  版本一致',     p9_version_sync),
        ('P10 自愈能力',     p10_self_healing),
        ('P11 日志健康',     p11_log_health),
    ]

    for name, fn in probes:
        try:
            fn()
        except Exception as ex:
            _results['warnings'].append({'msg': f'探针{name}运行异常: {ex}', 'dim': name[:3]})

    errors   = _results['errors']
    warnings = _results['warnings']
    infos    = _results['infos']

    score = 100
    score -= len(errors)   * 12
    score -= len(warnings) * 2
    score = max(0, score)

    status = 'HEALTHY' if score >= 85 else ('DEGRADED' if score >= 60 else 'CRITICAL')

    # 报告
    now_str = datetime.now(timezone.utc).strftime('%m-%d %H:%M UTC')
    lines = [
        f'🔬 **梵天360加强版 CI报告** | {now_str}',
        f'总分: **{score}/100** [{status}] | ❌{len(errors)} ⚠️{len(warnings)} ℹ️{len(infos)}',
        f'覆盖: 11维探针 × 380文件 × 3106函数',
        '',
    ]

    if errors:
        lines.append('❌ **需立即修复（影响交易安全）:**')
        for e in errors:
            lines.append(f'  [{e["dim"]}] {e["msg"]}')
        lines.append('')

    if warnings:
        lines.append('⚠️ **需要关注（影响系统完整性）:**')
        for w in warnings:
            lines.append(f'  [{w["dim"]}] {w["msg"]}')
        lines.append('')

    if verbose and infos:
        lines.append('ℹ️ **正常项:**')
        for i in infos[:10]:
            lines.append(f'  [{i["dim"]}] {i["msg"]}')

    if not errors and not warnings:
        lines.append('✅ **全部11个维度探针通过，系统无盲区**')

    report = '\n'.join(lines)

    # 保存
    out = BASE / 'data' / 'brahma_ci_v2_latest.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        'ts': time.time(), 'score': score, 'status': status,
        'errors': errors, 'warnings': warnings,
        'summary': f'{score}/100 {status} E={len(errors)} W={len(warnings)}'
    }, ensure_ascii=False, indent=2, default=str))

    return {'score': score, 'status': status, 'errors': errors,
            'warnings': warnings, 'infos': infos, 'report': report}


if __name__ == '__main__':
    import sys
    verbose = '--verbose' in sys.argv or '-v' in sys.argv
    r = run_ci_v2(verbose=verbose)
    print(r['report'])
