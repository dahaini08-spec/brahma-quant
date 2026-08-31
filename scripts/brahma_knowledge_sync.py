#!/usr/bin/env python3
"""
brahma_knowledge_sync.py — 梵天知识库同步
[P2-B 2026-08-31 苏摩111封印]

从WR矩阵+MEMORY.md提炼铁证到knowledge/目录
实现claude-obsidian思路的梵天专属版本
"""
import json, os
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent
KNOWLEDGE = ROOT / 'knowledge'

def sync_wr_evidence():
    """WR矩阵铁证 → knowledge/wr_evidence/"""
    with open(ROOT / 'data/wr_matrix_realtime.json') as f:
        wr = json.load(f)
    matrix = wr.get('matrix', {})

    updated = 0
    for key, v in matrix.items():
        if v.get('settled', 0) < 3:
            continue
        wr_val = v.get('wr', 0)
        ev_val = v.get('ev', 0)
        n = v.get('n', 0)
        settled = v.get('settled', 0)

        parts = key.split(':')
        if len(parts) < 2:
            continue
        regime = parts[0]
        direction = parts[1]
        score_bin = parts[2] if len(parts) > 2 else 'all'

        fname = f"{regime}_{direction}_{score_bin.replace('<','lt').replace('+','plus')}.md"
        fpath = KNOWLEDGE / 'wr_evidence' / fname

        # 评级
        if wr_val >= 0.75: grade = 'S级铁证🔥'
        elif wr_val >= 0.60: grade = 'A级有效✅'
        elif wr_val >= 0.50: grade = 'B级参考⚠️'
        else: grade = 'C级危险❌'

        content = f"""# WR铁证: {key}
更新时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

## 核心数据
- **WR**: {wr_val:.1%}
- **EV**: {ev_val:+.3f}%/笔
- **n**: {n}条信号
- **settled**: {settled}条已结算
- **评级**: {grade}

## 使用规则
- 体制: {regime}
- 方向: {direction}
- Score区间: {score_bin}
- 最小仓位: {'5%NAV' if wr_val>=0.9 else '3%NAV' if wr_val>=0.65 else '2%NAV' if wr_val>=0.55 else '0.5%NAV'}

## 关联
- [[regime_cases/{regime}]] — 历史体制案例
- [[lessons/wr_usage]] — WR矩阵使用规则
"""
        fpath.write_text(content, encoding='utf-8')
        updated += 1

    print(f'✅ WR铁证库: {updated}条更新 → knowledge/wr_evidence/')
    return updated


def create_index():
    """生成知识库索引"""
    index_path = KNOWLEDGE / 'INDEX.md'
    wr_files = list((KNOWLEDGE / 'wr_evidence').glob('*.md'))
    case_files = list((KNOWLEDGE / 'regime_cases').glob('*.md'))
    lesson_files = list((KNOWLEDGE / 'lessons').glob('*.md'))

    # 找最强铁证
    best = []
    for f in wr_files:
        content = f.read_text()
        if 'S级铁证' in content:
            best.append(f.stem)

    content = f"""# 梵天知识库索引
更新时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

## 🔥 S级铁证（WR≥75%）
{chr(10).join(f'- [[wr_evidence/{b}]]' for b in best) or '- 暂无'}

## 📊 WR铁证库 ({len(wr_files)}条)
knowledge/wr_evidence/ — 所有体制×方向×Score的历史胜率

## 📋 体制案例库 ({len(case_files)}条)
knowledge/regime_cases/ — 历史体制切换案例

## 🎓 教训索引 ({len(lesson_files)}条)
knowledge/lessons/ — 每次犯错的根因+修复记录

## 使用方式
苏摩问「BEAR_RECOVERY历史」→ knowledge/wr_evidence/BEAR_RECOVERY_*.md
苏摩问「今日踩踏原因」→ knowledge/regime_cases/
梵天犯错后 → knowledge/lessons/ 新建记录
"""
    index_path.write_text(content, encoding='utf-8')
    print(f'✅ 知识库索引: {index_path}')


def log_lesson(title: str, root_cause: str, fix: str, commit: str = ''):
    """记录教训到knowledge/lessons/"""
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    fname = f"{today}_{title.replace(' ','_')[:30]}.md"
    fpath = KNOWLEDGE / 'lessons' / fname

    content = f"""# 教训: {title}
日期: {today}

## 根因
{root_cause}

## 修复
{fix}

## Commit
{commit or '未记录'}

## 封印状态
✅ 已封印，不再发生
"""
    fpath.write_text(content, encoding='utf-8')
    print(f'✅ 教训记录: {fname}')


if __name__ == '__main__':
    print('=== 梵天知识库同步 ===')
    sync_wr_evidence()
    create_index()
    # 记录今日关键教训
    log_lesson(
        'CHOP_MID体制双向信号混淆',
        'CHOP_MID体制下S2同时输出做空+做多机会，CHOP多头死穴但仍给低多区信号',
        'brahma_full_report.py S2段：CHOP_MID/BEAR_TREND体制低多区改为观察位',
        'cf76485'
    )
    log_lesson(
        'Score作为信号门控锁死预判',
        'Score<门槛→Valid=False→不发信号，但战场三维已显示强空信号被忽略',
        'position_sizer.py新增calc_war_field_alignment+get_war_field_position，Score退位为仓位计算器',
        'a6bf999'
    )
    print('=== 完成 ===')
