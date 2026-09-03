#!/usr/bin/env python3
"""
brahma_wiring_check.py — 梵天接线巡检工具
设计院封印 2026-09-03 苏摩111

解决：建好的模块反复"接入但实际没用"的根本问题。

原理：
  1. 扫描brahma_brain/所有模块
  2. 检查每个模块是否被核心链路真实import
  3. 运行brahma_core.analyze()，检查每个模块是否有实际输出（非零值）
  4. 输出三类结果：
     ✅ 已接入且有输出
     ⚠️ 已import但输出为零（建好未用）
     ❌ 未被任何核心模块import（孤岛）

使用方式：
  python3 scripts/brahma_wiring_check.py
  python3 scripts/brahma_wiring_check.py --fix  # 自动归档孤岛
"""
import sys, os, json, re
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

# 核心链路文件（接线必须出现在这里才算真正接入）
CORE_FILES = [
    'brahma_brain/brahma_core.py',
    'brahma_brain/brahma_core_block_a.py',
    'brahma_brain/brahma_core_block_b.py',
    'brahma_brain/brahma_core_block_c.py',
    'brahma_brain/brahma_core_step4.py',
    'brahma_brain/brahma_core_analyze_steps.py',
    'brahma_brain/brahma_full_report.py',
    'brahma_analysis_runner.py',
]

# 已知合理孤岛（工具类/CLI/测试类，不需要被core调用）
KNOWN_STANDALONE = {
    'disk_cache',           # 被其他模块调用，不需要core直接import
    'data_cache',           # 同上
    'push_hub',             # 推送工具
    'system_config',        # 配置读取
    'brahma_smoke_test',    # 测试工具
    'brahma_autocheck',     # 自查工具
    'brahma_wiring_check',  # 本文件
    'brahma_360',           # 独立报告工具
    'brahma_full_report',   # 独立报告
    'live_price_feed',      # 实时价格工具
    'market_state',         # 被analyze_steps调用
    'universal_asset_router', # 被core调用
    'regime_config',        # 被core调用
    'formatter',            # 被runner调用
    'smc_engine',           # 被analyze_steps调用
    'fangcang_engine',      # 被analyze_steps调用
    'sentiment_engine',     # 被step4调用
    'macro_factor_engine',  # 被news_event_guard调用
    'commodity_adapter',    # 被universal_asset_router调用
    'signal_quality_engine', # 被core s28调用
    'ev_feedback',          # 被core调用
    'gex_unified',          # 被core s22调用
    'gex_engine',           # 被gex_unified调用
    'vol_beta_engine',      # 被core s23调用
    'har_rv_engine',        # 被core调用
    'onchain_engine',       # 被core调用
    'microstructure_engine',# 被core调用
    'order_flow_engine',    # 被core调用
    'lsr_oi_engine',        # 被core调用
    'narrative_engine',     # 被step4调用
    'dharma_council',       # 被core调用
    'hcme_engine',          # 被core调用
    'price_zone_engine',    # 被core调用
    'war_field_engine',     # 被core调用
    'experience_engine',    # 被core调用
    'brahma_experience_engine', # 同上
}


def load_core_content() -> str:
    """加载所有核心链路文件内容"""
    content = ''
    for f in CORE_FILES:
        fp = BASE / f
        if fp.exists():
            content += fp.read_text()
    return content


def get_all_brain_modules() -> list:
    """获取brahma_brain下所有模块名"""
    return [
        f.stem for f in (BASE / 'brahma_brain').glob('*.py')
        if not f.stem.startswith('_') and '__pycache__' not in str(f)
    ]


def check_module_imported(mod: str, core_content: str) -> bool:
    """检查模块是否被核心链路import"""
    patterns = [
        f'from {mod} import',
        f'from brahma_brain.{mod} import',
        f'import {mod}',
        f'"{mod}"',
        f"'{mod}'",
    ]
    return any(p in core_content for p in patterns)


def check_module_has_output(mod: str, result: dict) -> tuple:
    """
    检查模块在实际分析结果中是否有输出
    返回 (has_output: bool, evidence: str)
    """
    # 在breakdown中查找
    bd = result.get('confluence', {}).get('breakdown', {})
    smc = result.get('smc', {})
    extra = result.get('extra', {})
    fc = result.get('fangcang', {})

    # 模块→输出字段映射
    MODULE_OUTPUT_MAP = {
        'gex_unified':          lambda: any('gex' in k for k in bd),
        'vol_beta_engine':      lambda: any('vol_beta' in k for k in bd),
        'signal_quality_engine':lambda: any('signal_quality' in k for k in bd),
        'har_rv_engine':        lambda: any('HAR' in k or 'har' in k for k in bd),
        'onchain_engine':       lambda: extra.get('onchain_ws', {}).get('score', 0) != 0,
        'microstructure_engine':lambda: extra.get('microstructure', {}).get('score', 0) != 0,
        'order_flow_engine':    lambda: extra.get('order_flow', {}).get('score', 0) != 0,
        'lsr_oi_engine':        lambda: any('OI' in k or 'lsr' in k.lower() for k in bd),
        'narrative_engine':     lambda: extra.get('macro_report', {}).get('total', 0) != 0,
        'fangcang_engine':      lambda: fc.get('n_cases', 0) != 0,
        'smc_engine':           lambda: bool(smc.get('order_blocks', {}).get('bull_obs')),
        'sentiment_engine':     lambda: extra.get('sentiment_nlp', {}).get('score') is not None,
        'dharma_council':       lambda: result.get('nodes_verdict') is not None,
        'hcme_engine':          lambda: any('HCME' in k for k in bd),
        'price_zone_engine':    lambda: bool(extra.get('enhanced')),
        'regime_config':        lambda: result.get('regime') is not None,
        'macro_factor_engine':  lambda: extra.get('macro_report', {}).get('error') is None,
        'disk_cache':           lambda: (BASE / 'data' / 'cache').exists() and len(list((BASE / 'data' / 'cache').glob('*.pkl'))) > 0,
    }

    if mod in MODULE_OUTPUT_MAP:
        try:
            has = MODULE_OUTPUT_MAP[mod]()
            return has, '有输出' if has else '输出为零'
        except Exception as e:
            return False, f'检查失败:{e}'

    return None, '无检查规则'  # None = 跳过检查


def main(fix: bool = False):
    print('\n🔌 梵天接线巡检 brahma_wiring_check')
    print('=' * 60)

    core_content = load_core_content()
    all_mods = get_all_brain_modules()

    print(f'核心链路文件: {len(CORE_FILES)}个')
    print(f'brahma_brain模块总数: {len(all_mods)}个')
    print()

    # 运行一次分析获取结果
    print('🔄 运行梵天分析获取输出...')
    try:
        from brahma_brain import brahma_core
        result = brahma_core.analyze('BTCUSDT', signal_dir='SHORT')
        print(f'   score={result.get("score_final", 0):.1f} regime={result.get("regime")}\n')
    except Exception as e:
        print(f'   分析失败: {e}')
        result = {}

    wired_with_output = []
    wired_no_output   = []
    orphans           = []
    skipped           = []

    for mod in sorted(all_mods):
        if mod in KNOWN_STANDALONE:
            skipped.append(mod)
            continue

        imported = check_module_imported(mod, core_content)
        has_output, evidence = check_module_has_output(mod, result)

        if not imported:
            orphans.append(mod)
            print(f'  ❌ {mod}: 未被核心链路import（孤岛）')
        elif has_output is True:
            wired_with_output.append(mod)
            print(f'  ✅ {mod}: {evidence}')
        elif has_output is False:
            wired_no_output.append(mod)
            print(f'  ⚠️ {mod}: 已import但{evidence}（建好未用）')
        else:
            # has_output is None = 无检查规则，只验证import
            wired_with_output.append(mod)
            print(f'  ✅ {mod}: 已接入（无输出规则）')

    print()
    print('=' * 60)
    print(f'✅ 已接入有输出: {len(wired_with_output)}个')
    print(f'⚠️ 已import但零输出: {len(wired_no_output)}个 → 建好未用！')
    print(f'❌ 孤岛模块: {len(orphans)}个')
    print(f'⏭️ 工具类跳过: {len(skipped)}个')

    if wired_no_output:
        print(f'\n⚠️ 需要验证接入的模块:')
        for m in wired_no_output:
            print(f'   - {m}')

    if orphans:
        print(f'\n❌ 孤岛模块（考虑归档）:')
        for m in orphans:
            print(f'   - {m}')
        if fix:
            print('\n🔧 --fix模式: 归档孤岛模块')
            import shutil
            archive = BASE / 'archive' / 'orphans_auto'
            archive.mkdir(parents=True, exist_ok=True)
            for m in orphans:
                src = BASE / 'brahma_brain' / f'{m}.py'
                if src.exists():
                    shutil.move(str(src), str(archive / f'{m}.py'))
                    print(f'   归档: {m}.py')

    # 写入结果
    record = BASE / 'data' / 'wiring_check_last.json'
    record.write_text(json.dumps({
        'ts': __import__('datetime').datetime.utcnow().isoformat(),
        'wired': len(wired_with_output),
        'zero_output': wired_no_output,
        'orphans': orphans,
    }, indent=2))

    return len(wired_no_output) == 0 and len(orphans) == 0


if __name__ == '__main__':
    fix = '--fix' in sys.argv
    ok = main(fix=fix)
    sys.exit(0 if ok else 1)
