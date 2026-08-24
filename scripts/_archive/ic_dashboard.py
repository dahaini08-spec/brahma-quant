#!/usr/bin/env python3
"""
ic_dashboard.py — 维度IC可视化仪表盘
[预制 2026-08-12 苏摩111 | 来源: machine-learning-visualized 思路]

触发条件: 有breakdown的结算记录 >= 20条
触发方式: 每日360日报时自动检查并生成
输出: data/ic_trend_{date}.png + 推送到Jarvis线程
"""
import sys, os, json, time
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'brahma_brain'))
sys.path.insert(0, str(ROOT / 'scripts'))

LOG_PATH = ROOT / 'data' / 'live_signal_log.jsonl'
OUT_DIR  = ROOT / 'data'
MIN_RECORDS = 20  # 最少需要多少条有breakdown的结算记录

def load_settled_with_breakdown():
    """加载有breakdown且已结算的信号"""
    if not LOG_PATH.exists():
        return []
    records = []
    for line in LOG_PATH.read_text().strip().split('\n'):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            if r.get('outcome') not in ('TP1', 'SL', 'TP2', 'TP3'):
                continue
            cf = r.get('confluence') or {}
            bd = cf.get('breakdown') or {}
            if not bd:
                continue
            records.append(r)
        except Exception:
            continue
    return records


def compute_dim_ic(records):
    """
    计算每个维度的 IC（信息系数）
    IC = corr(维度得分, 信号结果)
    结果: +1=完美正相关(分高=赢), -1=完美负相关, 0=无预测力
    """
    if not records:
        return {}

    # 收集所有维度
    all_dims = set()
    for r in records:
        bd = (r.get('confluence') or {}).get('breakdown') or {}
        all_dims.update(bd.keys())

    ic_dict = {}
    for dim in all_dims:
        scores, outcomes = [], []
        for r in records:
            bd = (r.get('confluence') or {}).get('breakdown') or {}
            s = bd.get(dim)
            if s is None:
                continue
            outcome_val = 1.0 if r.get('outcome') in ('TP1', 'TP2', 'TP3') else -1.0
            scores.append(float(s))
            outcomes.append(outcome_val)

        if len(scores) < 5:  # 样本太少不计算
            continue

        # Pearson 相关系数
        n = len(scores)
        mean_s = sum(scores) / n
        mean_o = sum(outcomes) / n
        cov = sum((s - mean_s) * (o - mean_o) for s, o in zip(scores, outcomes)) / n
        std_s = (sum((s - mean_s) ** 2 for s in scores) / n) ** 0.5
        std_o = (sum((o - mean_o) ** 2 for o in outcomes) / n) ** 0.5
        if std_s == 0 or std_o == 0:
            continue
        ic = cov / (std_s * std_o)
        ic_dict[dim] = {'ic': round(ic, 4), 'n': n}

    return ic_dict


def generate_ic_heatmap(ic_dict, out_path):
    """用 Pillow 生成 IC 热力图（纯 ASCII，无CJK字体依赖）"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print('[ic_dashboard] Pillow 未安装，跳过图片生成')
        return False

    if not ic_dict:
        return False

    # 按 IC 绝对值排序
    sorted_dims = sorted(ic_dict.items(), key=lambda x: abs(x[1]['ic']), reverse=True)[:20]

    W, H = 800, max(400, len(sorted_dims) * 30 + 100)
    img = Image.new('RGB', (W, H), color=(20, 20, 30))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 14)
        font_title = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 16)
    except Exception:
        font = ImageFont.load_default()
        font_title = font

    title = f'Brahma Dim IC Heatmap | {datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")}'
    draw.text((20, 10), title, fill=(200, 200, 255), font=font_title)
    draw.text((20, 30), f'n_dims={len(sorted_dims)} | min_samples=5', fill=(150, 150, 150), font=font)

    bar_start = 300
    bar_max_w = 400

    for i, (dim, data) in enumerate(sorted_dims):
        y = 70 + i * 28
        ic_val = data['ic']
        n = data['n']

        # 维度名（截断到25字符，ASCII安全）
        dim_label = dim[:25] if all(ord(c) < 128 for c in dim) else f'dim_{i+1:02d}'
        draw.text((20, y + 5), dim_label, fill=(200, 200, 200), font=font)
        draw.text((260, y + 5), f'n={n}', fill=(120, 120, 120), font=font)

        # IC 条形图
        bar_w = int(abs(ic_val) * bar_max_w / 1.0)
        color = (80, 200, 80) if ic_val > 0 else (200, 80, 80)  # 绿=正IC, 红=负IC
        if ic_val > 0:
            draw.rectangle([bar_start, y + 3, bar_start + bar_w, y + 22], fill=color)
        else:
            draw.rectangle([bar_start - bar_w, y + 3, bar_start, y + 22], fill=color)

        # 中线
        draw.line([(bar_start, y + 2), (bar_start, y + 24)], fill=(150, 150, 150), width=1)

        # IC值
        draw.text((bar_start + bar_w + 5 if ic_val > 0 else bar_start - bar_w - 45, y + 5),
                  f'{ic_val:+.3f}', fill=color, font=font)

    img.save(str(out_path))
    return True


def run(push=False):
    records = load_settled_with_breakdown()
    n = len(records)

    if n < MIN_RECORDS:
        print(f'[ic_dashboard] 数据不足: {n}/{MIN_RECORDS}条有breakdown结算记录，跳过')
        print(f'  预计还需: {MIN_RECORDS - n}条 (当前信号频率约{MIN_RECORDS - n}~{(MIN_RECORDS - n)*2}天)')
        return False

    print(f'[ic_dashboard] 开始计算 | 样本={n}条')
    ic_dict = compute_dim_ic(records)
    print(f'[ic_dashboard] 有效维度: {len(ic_dict)}个')

    if not ic_dict:
        print('[ic_dashboard] 无有效维度数据')
        return False

    # 打印 top IC
    sorted_ic = sorted(ic_dict.items(), key=lambda x: abs(x[1]['ic']), reverse=True)
    print('\n=== 维度IC排行 ===')
    for dim, data in sorted_ic[:10]:
        bar = '█' * int(abs(data['ic']) * 20)
        sign = '+' if data['ic'] > 0 else '-'
        print(f'  {sign} {dim[:30]:<30} IC={data["ic"]:+.3f} n={data["n"]:3d} {bar}')

    # 生成图片
    date_str = datetime.now(tz=timezone.utc).strftime('%Y%m%d')
    out_path = OUT_DIR / f'ic_trend_{date_str}.png'
    success = generate_ic_heatmap(ic_dict, out_path)
    if success:
        print(f'\n[ic_dashboard] 图片已生成: {out_path}')

    # 推送到 Jarvis
    if push:
        try:
            from system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
            import subprocess
            msg_lines = [f'📊 维度IC日报 {date_str} | 样本={n}条']
            for dim, data in sorted_ic[:5]:
                icon = '🟢' if data['ic'] > 0.1 else ('🔴' if data['ic'] < -0.1 else '⚪')
                msg_lines.append(f'{icon} {dim[:20]}: IC={data["ic"]:+.3f} (n={data["n"]})')
            msg = '\n'.join(msg_lines)
            subprocess.run([
                'openclaw', 'message', 'send',
                '-t', f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}',
                '--channel', 'jarvis',
                '--message', msg
            ], capture_output=True)
            print('[ic_dashboard] 已推送到Jarvis')
        except Exception as e:
            print(f'[ic_dashboard] 推送失败: {e}')

    return True


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='梵天维度IC可视化仪表盘')
    parser.add_argument('--push', action='store_true', help='推送结果到Jarvis')
    parser.add_argument('--force', action='store_true', help='强制运行(忽略样本数限制)')
    args = parser.parse_args()
    if args.force:
        MIN_RECORDS = 0
    run(push=args.push)
