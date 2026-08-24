#!/usr/bin/env python3
"""
brahma_engine_selfcheck.py — 梵天引擎自检工具
[封印 2026-08-24 苏摩追问封印]

职责：
  - 验证step4所有9个引擎的import实际成功（不止标志位）
  - 验证函数名正确（不止模块存在）
  - 验证brahma_full_report端到端输出各引擎有值
  - 发现「名义接通但实际空转」缺陷

用法：
  python3 brahma_brain/brahma_engine_selfcheck.py
  python3 brahma_brain/brahma_engine_selfcheck.py --e2e   # 含end-to-end验证
"""
import sys, os, time, importlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# step4所有引擎：(模块名, 函数名, 描述, extra_data_key)
STEP4_ENGINES = [
    ('volume_exhaustion_engine', 'volume_exhaustion_score', '量能衰竭',  'vol_exhaustion'),
    ('divergence_engine',        'multitf_divergence_score','多周期背离', 'multitf_div'),
    ('microstructure_engine',    'microstructure_score',    '微观结构',  'microstructure'),
    ('cross_market_engine',      'cross_market_score',      '跨资产',    'cross_market'),
    ('pattern_engine',           'pattern_score',           '谐波形态',  'harmonic'),
    ('macro_engine',             'macro_score_v2',          '宏观引擎',  'macro_v2'),
    ('order_flow_engine',        'order_flow_score',        '订单流',    'order_flow'),
    ('multitf_engine',           'multitf_score',           '多周期对齐','multitf'),
    ('whale_engine',             'whale_score',             '鲸鱼引擎',  'whale'),
]

def check_imports():
    """Level1: import + 函数名验证"""
    results = []
    for mod, fn, desc, key in STEP4_ENGINES:
        try:
            m = importlib.import_module(mod)
            if hasattr(m, fn):
                results.append((desc, '✅', f'{mod}.{fn}'))
            else:
                fns = [x for x in dir(m) if not x.startswith('_') and callable(getattr(m,x))]
                results.append((desc, '❌ 函数缺失', f'{mod}.{fn} 不存在 | 实际: {fns[:5]}'))
        except ModuleNotFoundError:
            results.append((desc, '❌ 模块缺失', f'{mod} 不存在'))
        except Exception as e:
            results.append((desc, f'⚠️ {type(e).__name__}', str(e)[:50]))
    return results

def check_e2e(symbol='BTCUSDT'):
    """Level2: end-to-end验证 extra_data各引擎是否有值"""
    from brahma_brain.brahma_analysis_runner import run_analysis
    r = run_analysis(symbol)
    extra = r.get('extra', {})
    results = []
    for mod, fn, desc, key in STEP4_ENGINES:
        v = extra.get(key)
        err = extra.get(key + '_err', '')
        score = v.get('score', 0) if isinstance(v, dict) else None
        if v is None:
            results.append((desc, '❌ extra=None', f'err={err[:40]}' if err else '无错误信息'))
        elif score == 0 or score is None:
            results.append((desc, '⚠️ score=0', f'key={key} type={type(v).__name__}'))
        else:
            results.append((desc, f'✅ score={score}', key))
    return results, r.get('score', 0)

def run(e2e=False):
    print("=" * 65)
    print("🔌 梵天引擎自检 — " + time.strftime('%Y-%m-%d %H:%M CST', time.localtime()))
    print("=" * 65)

    print("\n[Level1] import + 函数名验证:")
    l1 = check_imports()
    l1_ok = sum(1 for _,s,_ in l1 if s.startswith('✅'))
    for desc, status, detail in l1:
        print(f"  {status:<20} {desc:<12} {detail}")
    print(f"\n  结果: {l1_ok}/{len(l1)} 通过")

    if e2e:
        print("\n[Level2] end-to-end验证 extra_data:")
        l2, score = check_e2e()
        l2_ok = sum(1 for _,s,_ in l2 if s.startswith('✅'))
        for desc, status, detail in l2:
            print(f"  {status:<20} {desc:<12} {detail}")
        print(f"\n  score={score:.1f} | 引擎激活: {l2_ok}/{len(l2)}")

    broken = [desc for desc, status, _ in l1 if not status.startswith('✅')]
    if broken:
        print(f"\n🚨 CRITICAL: {len(broken)}个引擎import失败: {broken}")
        print("  这些引擎在系统运行时静默归零，不会报错！")
        return False
    print("\n✅ 所有引擎import正常")
    return True

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--e2e', action='store_true', help='包含end-to-end验证')
    args = p.parse_args()
    ok = run(e2e=args.e2e)
    sys.exit(0 if ok else 1)
