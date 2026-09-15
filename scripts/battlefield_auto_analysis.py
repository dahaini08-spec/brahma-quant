#!/usr/bin/env python3
"""
battlefield_auto_analysis.py — Layer 3: 候选池→分析引擎自动触发
苏摩111 2026-09-14 封印

功能：
  1. 读取battlefield_candidates.json候选池
  2. 对Tier1强共振标的自动跑brahma_manual_analysis
  3. 输出结果保存到data/auto_analysis_latest.json
  4. 供OpenClaw cron每小时触发

接入位置：
  OpenClaw cron → 本脚本 → brahma_manual_analysis.py → VIP卡片
  865标的 → 13候选池 → 仅分析13个 → 95%算力节省
"""
import sys, os, json, time, subprocess
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).resolve().parent.parent
CAND_PATH = BASE / 'data' / 'battlefield_candidates.json'
OUT_PATH = BASE / 'data' / 'auto_analysis_latest.json'

def get_candidate_symbols(tier: str = 'tier1_strong', max_symbols: int = 10) -> list:
    """从候选池获取标的列表
    [9.15苏摩111] BTC+ETH永远在首位，Tier1填充剩余位"""
    if not CAND_PATH.exists():
        print(f'❌ 候选池文件不存在: {CAND_PATH}', file=sys.stderr)
        return ['BTC', 'ETH']  # fallback: 至少跑主力
    d = json.loads(CAND_PATH.read_text())
    tier_list = d.get(tier, [])
    tier1_syms = [c['symbol'].replace('USDT', '') for c in tier_list]
    # BTC+ETH永远在首位
    core = ['BTC', 'ETH']
    # Tier1填充剩余位（去重）
    remaining = [s for s in tier1_syms if s not in core][:max_symbols - len(core)]
    return core + remaining

def run_analysis(symbols: list) -> dict:
    """调用brahma_manual_analysis分析候选池"""
    if not symbols:
        return {'error': '无候选标的', 'symbols': []}
    
    t0 = time.time()
    cmd = [
        sys.executable,
        str(BASE / 'scripts' / 'brahma_manual_analysis.py'),
        '--symbols', *symbols
    ]
    
    print(f'[auto_analysis] 开始分析 {len(symbols)}个标的: {symbols}', flush=True)
    
    try:
        result = subprocess.run(
            cmd, cwd=str(BASE), capture_output=True, text=True,
            timeout=300,  # 5分钟超时
        )
        output = result.stdout
        error = result.stderr
    except subprocess.TimeoutExpired:
        return {'error': '分析超时(300s)', 'symbols': symbols}
    except Exception as e:
        return {'error': str(e), 'symbols': symbols}
    
    elapsed = time.time() - t0
    print(f'[auto_analysis] 分析完成 {elapsed:.1f}s', flush=True)
    
    return {
        'symbols': symbols,
        'output': output,
        'error': error if result.returncode != 0 else '',
        'elapsed_s': round(elapsed, 1),
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'returncode': result.returncode,
    }

def save_result(result: dict):
    """保存分析结果"""
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(result, ensure_ascii=False, default=str))
    tmp.rename(OUT_PATH)
    print(f'[auto_analysis] 结果已保存: {OUT_PATH}', flush=True)

if __name__ == '__main__':
    # 默认分析Tier1强共振标的（最多10个）
    tier = os.environ.get('AUTO_ANALYSIS_TIER', 'tier1_strong')
    max_sym = int(os.environ.get('AUTO_ANALYSIS_MAX', '10'))
    
    symbols = get_candidate_symbols(tier, max_sym)
    if not symbols:
        print(f'无候选标的(tier={tier})，退出')
        sys.exit(0)
    
    result = run_analysis(symbols)
    save_result(result)
    
    # 输出摘要
    if 'error' in result and not result.get('output'):
        print(f'❌ 分析失败: {result["error"]}')
    else:
        print(f'✅ 分析完成: {result.get("elapsed_s", "?")}s, {len(symbols)}个标的')
        # 输出最后500字符摘要
        output = result.get('output', '')
        if output:
            print('\n--- 分析摘要(最后500字符) ---')
            print(output[-500:])
    
    # [9.15苏摩111 精度优化1] auto_analysis → signal_queue 闭环
    # 好信号(score>=140)写入auto_signal_queue.json，供paper_executor自动执行
    try:
        SQ_PATH = BASE / 'data' / 'auto_signal_queue.json'
        # 读取现有signal_queue
        if SQ_PATH.exists():
            sq = json.loads(SQ_PATH.read_text())
            if isinstance(sq, dict):
                sq = sq.get('signals', [])
        else:
            sq = []
        
        # 解析analysis output，提取ENTER信号
        output_text = result.get('output', '')
        # 找所有ENTER信号（score>=140）
        import re
        new_signals = []
        # 匹配VIP卡片中的标的+方向+score
        for sym in symbols:
            # 在output中找该标的的ENTER/WATCH标记
            pattern = rf'{sym}.*?(ENTER|ENTER_FULL|WATCH).*?score[=: ]+(\d+\.?\d*)'
            matches = re.findall(pattern, output_text, re.IGNORECASE)
            for action, score_str in matches:
                score_val = float(score_str)
                if action in ('ENTER', 'ENTER_FULL') and score_val >= 140:
                    new_signals.append({
                        'symbol': sym + 'USDT',
                        'direction': 'LONG' if 'LONG' in output_text[max(0,output_text.find(sym)-200):output_text.find(sym)+200:] else 'SHORT',
                        'score': score_val,
                        'action': action,
                        'regime': 'AUTO',
                        'source': 'auto_analysis',
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    })
        
        if new_signals:
            sq.extend(new_signals)
            SQ_PATH.write_text(json.dumps(sq, indent=2, ensure_ascii=False))
            print(f'[auto_analysis] {len(new_signals)}条ENTER信号(score>=140)已写入signal_queue')
        else:
            print(f'[auto_analysis] 无score>=140的ENTER信号，不写入signal_queue')
    except Exception as e:
        print(f'[auto_analysis] signal_queue写入失败: {e}', file=sys.stderr)
