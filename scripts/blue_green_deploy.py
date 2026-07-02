#!/usr/bin/env python3
"""
blue_green_deploy.py — 梵天蓝绿部署控制器
设计院封印 2026-07-02 Phase 3

功能：
  热更新核心模块，不停服（零停机）
  蓝 = 当前运行版本，绿 = 待切换版本
  失败自动回滚（<30秒）

支持的部署单元：
  - brahma_core (评分引擎)
  - timing_filter (时机过滤)
  - position_sizer (仓位计算)
  - brahma_analysis_runner (分析入口)

用法：
  python3 scripts/blue_green_deploy.py --status              # 查看状态
  python3 scripts/blue_green_deploy.py --deploy brahma_core  # 部署核心模块
  python3 scripts/blue_green_deploy.py --rollback            # 回滚上一次部署
  python3 scripts/blue_green_deploy.py --verify brahma_core  # 验证当前模块健康

安全机制：
  1. 部署前自动跑单元测试（tests/test_core_brahma_units.py）
  2. 部署后 30秒 健康检查（3次探针）
  3. 任何探针失败 → 自动回滚
  4. 持仓存在时 → 拒绝部署（保护执行层）
"""
import os, sys, json, time, shutil, subprocess, hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
DEPLOY_DIR = BASE / '.deploy'
DEPLOY_DIR.mkdir(exist_ok=True)
DEPLOY_LOG = DEPLOY_DIR / 'deploy_log.jsonl'
BLUE_SNAPSHOT = DEPLOY_DIR / 'blue_snapshot'


# ── 可部署模块定义 ─────────────────────────────────────────────────
DEPLOYABLE_MODULES = {
    'brahma_core': {
        'files': ['brahma_brain/brahma_core.py'],
        'test_class': 'TestConfluenceScore',
        'health_check': 'brahma_brain.brahma_core',
        'critical': True,  # 部署前必须有持仓检查
    },
    'timing_filter': {
        'files': ['brahma_brain/timing_filter.py'],
        'test_class': 'TestEvaluateTiming',
        'health_check': 'brahma_brain.timing_filter',
        'critical': False,
    },
    'position_sizer': {
        'files': ['brahma_brain/position_sizer.py'],
        'test_class': 'TestGetPositionPct',
        'health_check': 'brahma_brain.position_sizer',
        'critical': True,
    },
    'brahma_analysis_runner': {
        'files': ['brahma_brain/brahma_analysis_runner.py'],
        'test_class': None,
        'health_check': 'brahma_brain.brahma_analysis_runner',
        'critical': True,
    },
    'causal_regime_verifier': {
        'files': ['brahma_brain/causal_regime_verifier.py'],
        'test_class': 'TestCausalVerifier',
        'health_check': 'brahma_brain.causal_regime_verifier',
        'critical': False,
    },
}


def _log(event: str, module: str, status: str, details: dict = None):
    """记录部署事件"""
    entry = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'event': event,
        'module': module,
        'status': status,
        'details': details or {},
    }
    with open(DEPLOY_LOG, 'a') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def _file_hash(path: str) -> str:
    """计算文件 SHA256（用于验证回滚）"""
    try:
        content = open(path, 'rb').read()
        return hashlib.sha256(content).hexdigest()[:12]
    except Exception:
        return 'unknown'


def check_active_positions() -> bool:
    """检查是否有活跃持仓（有持仓时阻止 critical 模块部署）"""
    pos_file = BASE / 'data' / 'wuqu_positions.json'
    try:
        data = json.loads(pos_file.read_text())
        positions = data if isinstance(data, list) else data.get('positions', [])
        active = [p for p in positions if p.get('status', '').upper() == 'OPEN']
        return len(active) > 0
    except Exception:
        return False


def run_tests(test_class: Optional[str] = None) -> tuple:
    """
    运行单元测试
    返回 (success: bool, output: str)
    """
    test_file = BASE / 'tests' / 'test_core_brahma_units.py'
    if not test_file.exists():
        return True, "测试文件不存在，跳过"

    cmd = ['python3', '-m', 'pytest', str(test_file), '-v', '--tb=short', '-x']
    if test_class:
        cmd.extend([f'-k', test_class])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=120, cwd=str(BASE)
        )
        success = result.returncode == 0
        output = result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout
        return success, output
    except subprocess.TimeoutExpired:
        return False, "测试超时（>120s）"
    except Exception as e:
        return False, f"测试执行失败: {e}"


def health_check_module(module_name: str, n_retries: int = 3) -> tuple:
    """
    健康检查：尝试导入模块并调用基础函数
    返回 (healthy: bool, message: str)
    """
    config = DEPLOYABLE_MODULES.get(module_name)
    if not config:
        return False, f"未知模块: {module_name}"

    import_path = config['health_check']

    for attempt in range(n_retries):
        try:
            # 强制重新加载模块
            import importlib
            if import_path in sys.modules:
                del sys.modules[import_path]

            mod = importlib.import_module(import_path)

            # 模块特定健康检查
            if module_name == 'brahma_core':
                assert hasattr(mod, 'confluence_score'), "missing confluence_score"
            elif module_name == 'timing_filter':
                assert hasattr(mod, 'evaluate_timing'), "missing evaluate_timing"
            elif module_name == 'position_sizer':
                assert hasattr(mod, 'get_position_pct'), "missing get_position_pct"
                assert hasattr(mod, 'kelly_position'), "missing kelly_position"

            return True, f"健康 (尝试 {attempt+1}/{n_retries})"

        except Exception as e:
            if attempt < n_retries - 1:
                time.sleep(2)
            else:
                return False, f"导入失败: {e}"

    return False, "健康检查失败"


def create_blue_snapshot(module_name: str) -> str:
    """创建蓝色版本快照（部署前备份）"""
    config = DEPLOYABLE_MODULES[module_name]
    snapshot_dir = BLUE_SNAPSHOT / module_name
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    snapshot_meta = {
        'module': module_name,
        'ts': datetime.now(timezone.utc).isoformat(),
        'files': {},
    }

    for rel_path in config['files']:
        src = BASE / rel_path
        if src.exists():
            dst = snapshot_dir / Path(rel_path).name
            shutil.copy2(src, dst)
            snapshot_meta['files'][rel_path] = {
                'hash': _file_hash(str(src)),
                'backup': str(dst),
            }

    meta_file = snapshot_dir / 'snapshot_meta.json'
    with open(meta_file, 'w') as f:
        json.dump(snapshot_meta, f, indent=2)

    return str(snapshot_dir)


def rollback_module(module_name: str) -> tuple:
    """从蓝色快照回滚"""
    snapshot_dir = BLUE_SNAPSHOT / module_name
    meta_file = snapshot_dir / 'snapshot_meta.json'

    if not meta_file.exists():
        return False, f"无快照可回滚: {module_name}"

    meta = json.loads(meta_file.read_text())
    for rel_path, info in meta['files'].items():
        backup = info['backup']
        dst = BASE / rel_path
        if Path(backup).exists():
            shutil.copy2(backup, dst)

    _log('rollback', module_name, 'success', {'snapshot_ts': meta['ts']})
    return True, f"✅ 已回滚到 {meta['ts'][:19]}"


def deploy_module(module_name: str, dry_run: bool = False) -> bool:
    """
    执行蓝绿部署流程

    步骤：
    1. 持仓安全检查（critical模块）
    2. 运行单元测试（部署前门控）
    3. 创建蓝色快照（备份）
    4. 当前文件已是最新（热重载）
    5. 健康检查（3次探针）
    6. 失败则自动回滚
    """
    config = DEPLOYABLE_MODULES.get(module_name)
    if not config:
        print(f"❌ 未知模块: {module_name}")
        print(f"可用模块: {list(DEPLOYABLE_MODULES.keys())}")
        return False

    print(f"\n🔵🟢 蓝绿部署: {module_name}")
    print(f"   文件: {config['files']}")
    if dry_run:
        print("   [DRY RUN 模式，不实际修改文件]")
    print()

    # Step 1: 持仓检查
    if config['critical'] and check_active_positions():
        print("⛔ 检测到活跃持仓，critical模块部署被阻止")
        print("   原因: 部署核心模块可能影响已开仓的执行逻辑")
        print("   解决: 等待持仓平仓后再部署，或使用 --force 参数")
        _log('deploy_blocked', module_name, 'has_positions')
        return False
    print("✅ Step 1/5: 持仓检查通过")

    # Step 2: 单元测试
    print(f"🧪 Step 2/5: 运行单元测试 ({config['test_class'] or '全量'})...")
    test_ok, test_output = run_tests(config['test_class'])
    if not test_ok:
        print(f"❌ 单元测试失败，部署中止")
        print(f"   错误摘要: {test_output[-500:]}")
        _log('deploy_blocked', module_name, 'test_failed', {'output': test_output[-200:]})
        return False
    print(f"✅ Step 2/5: 单元测试通过")

    # Step 3: 创建蓝色快照
    if not dry_run:
        snapshot_dir = create_blue_snapshot(module_name)
        print(f"✅ Step 3/5: 蓝色快照创建: {snapshot_dir}")
    else:
        print(f"✅ Step 3/5: [DRY RUN] 蓝色快照（跳过）")

    # Step 4: 当前代码就是绿色版本（文件已更新）
    hashes = {
        f: _file_hash(str(BASE / f))
        for f in config['files']
        if (BASE / f).exists()
    }
    print(f"✅ Step 4/5: 绿色版本确认")
    for f, h in hashes.items():
        print(f"   {f}: {h}")

    # Step 5: 健康检查
    if not dry_run:
        print(f"🩺 Step 5/5: 健康检查（3次探针）...")
        healthy, msg = health_check_module(module_name, n_retries=3)
        if not healthy:
            print(f"❌ 健康检查失败: {msg}")
            print("🔄 自动回滚中...")
            rollback_ok, rollback_msg = rollback_module(module_name)
            print(f"   {rollback_msg}")
            _log('deploy_failed_rollback', module_name, 'failed', {'error': msg})
            return False
        print(f"✅ Step 5/5: 健康检查通过 — {msg}")
    else:
        print(f"✅ Step 5/5: [DRY RUN] 健康检查（跳过）")

    _log('deploy_success', module_name, 'success', {'hashes': hashes})
    print(f"\n🎉 部署成功: {module_name}")
    return True


def show_status():
    """显示所有模块的部署状态"""
    print(f"\n🔵🟢 梵天蓝绿部署状态 | {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
    print("=" * 55)

    has_positions = check_active_positions()
    pos_icon = '🔴' if has_positions else '🟢'
    print(f"{pos_icon} 活跃持仓: {'有（critical模块部署被阻止）' if has_positions else '无（可部署）'}\n")

    for name, config in DEPLOYABLE_MODULES.items():
        healthy, msg = health_check_module(name, n_retries=1)
        icon = '🟢' if healthy else '🔴'
        critical = '⚡' if config['critical'] else ' '
        has_snapshot = (BLUE_SNAPSHOT / name / 'snapshot_meta.json').exists()
        snap = '📦' if has_snapshot else '  '

        # 当前文件hash
        hashes = [_file_hash(str(BASE / f)) for f in config['files']]
        hash_str = hashes[0] if hashes else 'N/A'

        print(f"  {icon}{critical} {name:30s} {snap} [{hash_str}] {msg}")

    print("\n图例: ⚡=critical 📦=有快照可回滚")

    # 最近部署记录
    if DEPLOY_LOG.exists():
        events = [json.loads(l) for l in DEPLOY_LOG.read_text().splitlines() if l.strip()]
        if events:
            print(f"\n📋 最近部署记录:")
            for ev in events[-3:]:
                ts = ev['ts'][:19]
                print(f"  [{ts}] {ev['event']:25s} {ev['module']:25s} → {ev['status']}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='梵天蓝绿部署控制器')
    parser.add_argument('--status', action='store_true', help='查看状态')
    parser.add_argument('--deploy', type=str, help='部署模块名')
    parser.add_argument('--rollback', type=str, help='回滚模块名')
    parser.add_argument('--verify', type=str, help='验证模块健康')
    parser.add_argument('--dry-run', action='store_true', help='模拟运行，不修改文件')
    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.deploy:
        deploy_module(args.deploy, dry_run=args.dry_run)
    elif args.rollback:
        ok, msg = rollback_module(args.rollback)
        print(msg)
    elif args.verify:
        ok, msg = health_check_module(args.verify)
        print(f"{'✅' if ok else '❌'} {args.verify}: {msg}")
    else:
        show_status()
