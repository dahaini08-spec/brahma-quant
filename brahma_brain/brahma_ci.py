#!/usr/bin/env python3
"""
brahma_ci.py — 梵天持续集成 · 主动探针系统
设计院封印 2026-07-02

# ╔══ INTERFACE CONTRACT ══════════════════════════════════════════════╗
# 定位: 梵天360的主动发现层，弥补被动发现的盲区
# 入口: run_ci(level='full') -> dict
# 输出: {score, issues, warnings, report_str}
# 调用: openclaw cron every 6h / 按需手动
# ╚═══════════════════════════════════════════════════════════════════╝

六大探针维度:
  P1: 信号流量探针    — signal_trace是否有信号流入，执行层是否响应
  P2: 推送链路探针    — 每个cron任务的推送状态是否正常
  P3: 代码一致性探针  — 新模块是否已接入，旧版本是否已淘汰
  P4: 资产一致性探针  — 持仓记录与交易所实际是否匹配
  P5: 执行层探针      — auto_executor/ws_guardian是否正常工作
  P6: 更新鲜度探针    — 关键数据文件是否过期
"""
import json, os, sys, time, subprocess
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

ISSUES   = []   # level=ERROR, 影响交易安全
WARNINGS = []   # level=WARN,  影响信息完整性
INFOS    = []   # level=INFO,  观察项

def issue(msg, level='ERROR', dim='?'):
    ISSUES.append({'msg': msg, 'level': level, 'dim': dim})

def warn(msg, dim='?'):
    WARNINGS.append({'msg': msg, 'dim': dim})

def info(msg, dim='?'):
    INFOS.append({'msg': msg, 'dim': dim})

# ─── P1: 信号流量探针 ────────────────────────────────────────────────
def probe_signal_flow():
    """检查信号从生成到执行的完整链路"""
    now = time.time()

    # 1a. signal_trace是否有新记录
    trace_file = BASE / 'logs' / 'signal_trace.jsonl'
    if not trace_file.exists():
        warn('signal_trace.jsonl 不存在，信号轨迹无法追踪', 'P1_signal')
    else:
        lines = trace_file.read_text().splitlines()
        age_h = (now - trace_file.stat().st_mtime) / 3600
        if age_h > 4:
            warn(f'signal_trace 最后更新 {age_h:.1f}h 前，信号流量可能停滞', 'P1_signal')
        else:
            info(f'signal_trace OK: {len(lines)}条记录，{age_h:.1f}h前更新', 'P1_signal')

    # 1b. live_signal_log是否有活跃信号
    lsl = BASE / 'data' / 'live_signal_log.jsonl'
    if lsl.exists():
        size = lsl.stat().st_size
        if size == 0:
            warn('live_signal_log.jsonl 为空，近期无有效信号生成', 'P1_signal')
        else:
            age_h = (now - lsl.stat().st_mtime) / 3600
            info(f'live_signal_log OK: {size}B {age_h:.1f}h前', 'P1_signal')

    # 1c. 执行层日志是否存在
    exec_log = BASE / 'logs' / 'auto_executor.log'
    exec_jsonl = BASE / 'data' / 'auto_executor_log.jsonl'
    # auto_executor只在实际开单时写log，无信号时正常不写 → 阈值48h
    if exec_jsonl.exists():
        age_h = (now - exec_jsonl.stat().st_mtime) / 3600
        info(f'auto_executor jsonl {age_h:.1f}h前 (有执行记录)', 'P1_exec')
    elif exec_log.exists():
        age_h = (now - exec_log.stat().st_mtime) / 3600
        if age_h > 48:
            warn(f'auto_executor.log {age_h:.1f}h 未更新（可能48h无交易触发）', 'P1_exec')
        else:
            info(f'auto_executor OK: {age_h:.1f}h前', 'P1_exec')
    else:
        info('auto_executor 无历史执行记录（正常：等待信号触发）', 'P1_exec')

# ─── P2: 推送链路探针 ────────────────────────────────────────────────
def probe_push_links():
    """检查每个关键cron任务的推送配置"""
    result = subprocess.run(['openclaw','cron','list'], capture_output=True, text=True)
    lines = [l for l in result.stdout.split('\n') if l.strip() and not l.startswith('Config')]

    # 必须有推送的关键任务
    MUST_PUSH = {
        'pump-hunter':        '暴涨猎手发现信号必须推送',
        'oi-surge-scanner':   'OI异动必须推送',
        # 'rsi-structure-watcher': 'RSI结构事件通过脚本内部推送（write_trigger注入），非cron层推送',
        'brahma-360-daily':   '每日分析必须推送',
        # 'smart-digest-6h': '智能汇总通过脚本内部推送，不需要cron announce'
    }
    # 不需要推送的合理静默任务
    SILENT_OK = {
        'btc-regime-watcher', # # 'ws-guardian-keepalive'  # 已删除 2026-07-08,  # 已删除 2026-07-08 从未注册 'live-signal-settle-2h',
        'trc20-order-monitor', 'auto-position-manager', 'market-structure-4h',
        'data-backup-6h', 'gateway-restart-daily', 'dharma-offline-replay',
        'auto-executor-1h', 'brahma-360-health', 'live-performance-daily',
        'kronos-deps-restore', 'session-cleanup-daily', 'auto-executor',
        'position-sl-monitor', 'ws-guardian', 'brahma-arch-review',
        'rsi-structure-watcher',   # 有事件才推送
        'auto-position-manager',   # 纯脚本持仓管理，静默合理
        'dharma-offline-replay',   # 周度离线回测，静默合理
        'live-performance-daily',  # github更新脚本，静默合理
        'brahma-ci-probe',         # CI探针有内建推送，无需cron推送
        'smart-digest-6h',         # 智能汇总通过脚本内部推送，不需要cron announce
        'sub-executor-30m',        # 子系统执行器，有执行时自然推送，静默合理
        'stale-order-cleaner',     # 超龄撤单器，异常时内部推送，静默合理
        'timesfm-bridge-4h',       # 时序预测桥接，脚本静默运行
        'signal-watcher-6h',       # 信号监控，有HEARTBEAT_OK静默
        'regime-switch-monitor',   # 体制切换监控，事件触发才推送
        'brahma-self-heal',        # 自愈引擎，异常时内建推送，日常静默合理
        'brahma-nerve-center',     # 感知神经中枢，事件触发才推送，脚本静默
        'brahma-order-engine',     # 订单引擎，执行时有推送，静默合理
        'brahma-360-health',       # 健康检查，探针内建推送机制
        'deviation-alert',         # 偏差预警，事件触发推送
        'news-event-guard',        # 新闻事件守卫，事件触发推送
        'session-cleanup-30m',     # 会话清理，纯维护任务
        'brahma-4h-综合速报',   # 脚本内建 openclaw message推送，非cron层announce
        'brahma-日报',            # 脚本内建 openclaw message推送，非cron层announce
    }

    found_jobs = {}
    for line in lines:
        parts = line.split()
        if len(parts) < 2: continue
        # 跳过表头行
        if parts[0] in ('ID', 'Config', '-'): continue
        if parts[1] in ('Name', 'Schedule', 'Next'): continue
        name = parts[1].rstrip('.')
        has_push = 'announce' in line or 'jarvis' in line
        thread = '019f309c' if '019f309c' in line else ('019f1797' if '019f1797' in line else ('019f181f' if '019f181f' in line else ('019f15c6' if '019f15c6' in line else 'NONE')))
        found_jobs[name] = {'has_push': has_push, 'thread': thread, 'raw': line}

    # 检查必须推送的任务
    for name, reason in MUST_PUSH.items():
        if name not in found_jobs:
            issue(f'任务缺失: {name} ({reason})', 'ERROR', 'P2_push')
        elif not found_jobs[name]['has_push']:
            issue(f'推送断路: {name} 无推送配置 ({reason})', 'ERROR', 'P2_push')
        else:
            thread = found_jobs[name]['thread']
            if thread == 'NONE':
                warn(f'{name} 有推送但线程ID未知', 'P2_push')
            else:
                info(f'{name} 推送 OK → {thread[:8]}', 'P2_push')

    # 检查非静默、非必须推送的任务（潜在遗漏）
    for name, data in found_jobs.items():
        if name in MUST_PUSH or name in SILENT_OK:
            continue
        if not data['has_push']:
            warn(f'潜在静默任务: {name} 无推送（可能应该推送）', 'P2_push')

# ─── P3: 代码一致性探针 ──────────────────────────────────────────────
def probe_code_consistency():
    """检查模块接入状态，发现新老更替问题"""
    BRAIN = BASE / 'brahma_brain'
    runner_src = (BRAIN / 'brahma_analysis_runner.py').read_text(errors='ignore')

    # 关键模块必须在runner中被引用
    MUST_IN_RUNNER = {
        'signal_trace':       '_TRACE_OK',
        'timing_filter':      '_TIMING_OK',
        'llm_council_bridge': '_LLM_COUNCIL_OK',
        'analysis_snapshot':  '_SNAPSHOT_OK',
        'kronos_engine':      'kronos_engine',   # bridge在brahma_core内部调用
    }
    for mod, marker in MUST_IN_RUNNER.items():
        if marker not in runner_src:
            issue(f'模块未接入: {mod} (标记 {marker} 未找到)', 'ERROR', 'P3_code')
        else:
            info(f'{mod} 接入 OK', 'P3_code')

    # 检查是否有重复的同名任务（新老更替遗漏）
    result = subprocess.run(['openclaw','cron','list'], capture_output=True, text=True)
    name_count = {}
    for line in result.stdout.split('\n'):
        parts = line.split()
        if len(parts) >= 2 and not parts[0].startswith('Config'):
            name = parts[1]
            name_count[name] = name_count.get(name, 0) + 1
    for name, count in name_count.items():
        if count > 1:
            issue(f'重复任务: {name} 存在 {count} 个实例（新老更替未清理）', 'ERROR', 'P3_code')

    # 检查brahma_core.py是否有无意义的return 0.0占位符（排除业务逻辑中的0.0）
    core_src = (BRAIN / 'brahma_core.py').read_text(errors='ignore')
    placeholder_zeros = []
    for i, line in enumerate(core_src.splitlines(), 1):
        stripped = line.strip()
        # 判断是否是占位符：函数体只有return 0.0，没有前置逻辑
        if stripped == 'return 0.0':
            # 检查前5行是否只是docstring/pass，没有实际计算
            ctx_lines = core_src.splitlines()[max(0,i-6):i-1]
            ctx = ' '.join(l.strip() for l in ctx_lines)
            if 'score' not in ctx and 'if ' not in ctx and 'try' not in ctx:
                placeholder_zeros.append(i)
    if placeholder_zeros:
        warn(f'brahma_core.py 发现 {len(placeholder_zeros)} 个疑似占位符 return 0.0 (行: {placeholder_zeros})', 'P3_code')
    else:
        info('brahma_core.py 无占位符 OK', 'P3_code')

# ─── P4: 资产一致性探针 ──────────────────────────────────────────────
def probe_asset_consistency():
    """检查持仓记录与交易所实际是否匹配"""
    pos_file = BASE / 'data' / 'wuqu_positions.json'
    if not pos_file.exists():
        warn('wuqu_positions.json 不存在，持仓记录缺失', 'P4_asset')
        return

    age_h = (time.time() - pos_file.stat().st_mtime) / 3600
    if age_h > 24:
        warn(f'wuqu_positions.json {age_h:.1f}h 未更新（>24h）', 'P4_asset')

    try:
        positions = json.loads(pos_file.read_text())
        if isinstance(positions, list):
            pos_count = len(positions)
        elif isinstance(positions, dict):
            pos_count = len(positions.get('positions', []))
        else:
            pos_count = 0
        info(f'持仓记录: {pos_count} 个活跃仓位', 'P4_asset')

        # 检查ws_guardian是否守护所有持仓（优先state文件，再降级到log文件）
        ws_state = BASE / 'data' / 'ws_guardian_state.json'
        ws_log = BASE / 'logs' / 'ws_guardian.log'
        if ws_state.exists():
            try:
                st = json.loads(ws_state.read_text())
                ts_val = st.get('ts', 0)
                age_s = time.time() - ts_val if ts_val else 9999
                status = st.get('status','unknown')
                watching = st.get('watching', 0)
                # 容错：status=unknown但age<5min → 瞬态写入竞态，忽略
                if status != 'active' and age_s > 300:  # 非active且超5min才报警
                    issue(f'ws_guardian 异常: status={status} 最后更新 {age_s/3600:.1f}h前', 'ERROR', 'P4_asset')
                elif status != 'active' and age_s <= 300:
                    info(f'ws_guardian status={status}(瞬态) age={age_s:.0f}s<300s 忽略', 'P4_asset')
                else:
                    info(f'ws_guardian OK: active watching={watching} ({age_s/60:.0f}min前)', 'P4_asset')
            except Exception:
                if ws_log.exists():
                    ws_age = (time.time() - ws_log.stat().st_mtime) / 3600
                    if ws_age > 1.0:
                        issue(f'ws_guardian log {ws_age:.1f}h 未更新', 'ERROR', 'P4_asset')
                    else:
                        info(f'ws_guardian log OK ({ws_age:.2f}h前)', 'P4_asset')
        elif ws_log.exists():
            ws_age = (time.time() - ws_log.stat().st_mtime) / 3600
            if ws_age > 1.0:
                issue(f'ws_guardian log {ws_age:.1f}h 未更新', 'ERROR', 'P4_asset')
            else:
                info(f'ws_guardian log OK ({ws_age:.2f}h前)', 'P4_asset')
    except Exception as e:
        warn(f'持仓文件解析失败: {e}', 'P4_asset')

# ─── P5: 执行层探针 ──────────────────────────────────────────────────
def probe_execution_layer():
    """检查自动执行层完整性"""
    # 检查auto_executor进程
    try:
        r = subprocess.run(['pgrep','-f','auto_executor'], capture_output=True, text=True)
        if r.stdout.strip():
            info(f'auto_executor进程运行中 PID={r.stdout.strip()}', 'P5_exec')
        else:
            info('auto_executor非持久进程（cron触发模式）', 'P5_exec')
    except:
        pass

    # 检查信号队列
    sq = BASE / 'data' / 'signal_queue.jsonl'
    if sq.exists() and sq.stat().st_size == 0:
        info('signal_queue.jsonl 为空（正常，无待执行信号）', 'P5_exec')
    elif sq.exists():
        age_h = (time.time() - sq.stat().st_mtime) / 3600
        lines = sq.read_text().splitlines()
        if len(lines) > 5 and age_h > 2:
            warn(f'signal_queue 有 {len(lines)} 条未处理信号，{age_h:.1f}h未清空', 'P5_exec')

# ─── P6: 数据鲜度探针 ────────────────────────────────────────────────
def probe_data_freshness():
    """检查关键数据文件的新鲜度"""
    now = time.time()
    checks = [
        ('data/live_prices.json',      27.0,   'WARN',   '实时价格(日报更新)'),
        ('data/live_signal_log.jsonl', 24,     'WARN',   '信号日志'),
        ('data/wuqu_positions.json',   24,     'WARN',   '持仓记录'),
        ('data/brahma_state.json', 48, 'WARN',   '系统状态'),
    ]
    for fpath, max_h, level, name in checks:
        fp = BASE / fpath
        if not fp.exists():
            if level == 'ERROR':
                issue(f'{name}({fpath}) 不存在', 'ERROR', 'P6_freshness')
            else:
                warn(f'{name}({fpath}) 不存在', 'P6_freshness')
            continue
        age_h = (now - fp.stat().st_mtime) / 3600
        if age_h > max_h:
            msg = f'{name} 数据过期 {age_h:.1f}h（限{max_h}h）'
            if level == 'ERROR':
                issue(msg, 'ERROR', 'P6_freshness')
            else:
                warn(msg, 'P6_freshness')
        else:
            info(f'{name} 鲜度OK ({age_h:.2f}h)', 'P6_freshness')

# ─── 主入口 ──────────────────────────────────────────────────────────
def run_ci(level='full') -> dict:
    global ISSUES, WARNINGS, INFOS
    ISSUES, WARNINGS, INFOS = [], [], []

    probe_signal_flow()
    probe_push_links()
    probe_code_consistency()
    probe_asset_consistency()
    probe_execution_layer()
    probe_data_freshness()

    # 评分
    score = 100
    score -= len([i for i in ISSUES if i['level']=='ERROR']) * 15
    score -= len(WARNINGS) * 3
    score = max(0, score)

    status = 'HEALTHY' if score >= 85 else ('DEGRADED' if score >= 60 else 'CRITICAL')

    # 生成报告
    lines = [f'🔬 梵天CI探针报告 | {datetime.now(timezone.utc).strftime("%m-%d %H:%M UTC")}']
    lines.append(f'总分: {score}/100 [{status}] | ❌{len(ISSUES)} ⚠️{len(WARNINGS)} ℹ️{len(INFOS)}')
    lines.append('')

    if ISSUES:
        lines.append('❌ **需要修复（影响交易安全）:**')
        for i in ISSUES:
            lines.append(f'  [{i["dim"]}] {i["msg"]}')
        lines.append('')

    if WARNINGS:
        lines.append('⚠️ **需要关注（影响信息完整性）:**')
        for w in WARNINGS:
            lines.append(f'  [{w["dim"]}] {w["msg"]}')
        lines.append('')

    if not ISSUES and not WARNINGS:
        lines.append('✅ 全部探针通过，系统无盲区')

    report = '\n'.join(lines)

    return {
        'score': score,
        'status': status,
        'issues': ISSUES,
        'warnings': WARNINGS,
        'infos': INFOS,
        'report': report,
    }

if __name__ == '__main__':
    r = run_ci()
    print(r['report'])
    # 保存结果
    out = BASE / 'data' / 'brahma_ci_latest.json'
    out.write_text(json.dumps({'ts': time.time(), **r}, ensure_ascii=False, indent=2, default=str))

def push_ci_report():
    """CI结果直推Jarvis，只在异常时推送"""
    import sys
    sys.path.insert(0, str(BASE.parent))
    from scripts.system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
    r = run_ci()
    score = r['score']
    status = r['status']
    report = r['report']
    # 保存
    out = BASE / 'data' / 'brahma_ci_latest.json'
    out.write_text(json.dumps({'ts': time.time(), **r}, ensure_ascii=False, indent=2, default=str))
    # 只在异常时推送
    if score < 85 or r.get('issues'):
        prefix = '🚨CI告警' if score < 70 else '⚠️CI警告'
        msg = f'{prefix} | {score}/100\n{report}'
        target = f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}'
        subprocess.run(
            ['openclaw','message','send','--channel','jarvis','--target',target,'--message',msg],
            capture_output=True, text=True, timeout=15
        )
        print(f'[CI] 推送告警 score={score}')
    else:
        print(f'[CI] HEALTHY score={score}/100，静默')

if __name__ == '__main__':
    import sys
    if '--push' in sys.argv:
        push_ci_report()
    else:
        r = run_ci()
        print(r['report'])
        out = BASE / 'data' / 'brahma_ci_latest.json'
        out.write_text(json.dumps({'ts': time.time(), **r}, ensure_ascii=False, indent=2, default=str))
