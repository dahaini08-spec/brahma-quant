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
from push_hub import push_jarvis

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
    # [P0-A 2026-09-24 苏摩111] 预算随标的数扩展：单标的~30s，固定300s是10标的的隐形炸弹
    timeout_s = min(60 + 75 * len(symbols), 840)
    cmd = [
        sys.executable,
        str(BASE / 'scripts' / 'brahma_manual_analysis.py'),
        '--symbols', *symbols
    ]
    
    print(f'[auto_analysis] 开始分析 {len(symbols)}个标的: {symbols} (预算{timeout_s}s)', flush=True)
    
    try:
        result = subprocess.run(
            cmd, cwd=str(BASE), capture_output=True, text=True,
            timeout=timeout_s,
        )
        output = result.stdout
        error = result.stderr
    except subprocess.TimeoutExpired:
        return {'error': f'分析超时({timeout_s}s) symbols={len(symbols)}', 'symbols': symbols,
                'timeout': True, 'elapsed_s': round(time.time() - t0, 1)}
    except Exception as e:
        return {'error': f'分析异常: {e}', 'symbols': symbols, 'timeout': False,
                'elapsed_s': round(time.time() - t0, 1)}
    
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

def build_push_text(result: dict, symbols: list) -> str | None:
    """构建推送文本：提取auto_analysis_latest的output（VIP卡区）+ 触发摘要"""
    try:
        data = json.loads(OUT_PATH.read_text())
    except Exception:
        data = result
    out = str(data.get('output') or '')
    if not out.strip():
        return None
    # 取VIP卡起头（首张VIP卡出现处）到结尾，保留最新裁决卡片
    vidx = out.find('──── VIP ────')
    if vidx >= 0:
        vip = out[vidx:]
        # 每标的都有VIP卡区，多标的时截断到3000字符防爆长度
        return vip[:3000]
    return out[-1500:]


def _push_result(result: dict, symbols: list):
    """推送裁决到Jarvis：脚本层独立推送，不依赖AI cron存活
    P2级别+6h去重：同裁决内容在TTL内只推一次，防轰炸
    [P0-A 2026-09-24 苏摩111] 失败/超时显式告警——静默失败=全灭不知情
    """
    if result.get('timeout') or (result.get('error') and not result.get('output')):
        try:
            alert = (f"🚨 梵天批量分析失败\n"
                     f"标的数: {len(symbols)} | 耗时: {result.get('elapsed_s','?')}s\n"
                     f"错误: {str(result.get('error'))[:120]}\n"
                     f"→ 下一轮自动重试，若连续失败请检查supercronic/内存")
            push_jarvis(alert, priority='P1', dedup_key=f"analysis_fail_{datetime.now(timezone.utc).strftime('%Y%m%d%H')}", dedup_ttl=3600)
            print('[push] 失败告警已推送', flush=True)
        except Exception as e:
            print(f'[push] 告警推送也失败: {e}', flush=True)
        return
    try:
        data = json.loads(OUT_PATH.read_text())
    except Exception:
        data = result
    ssot = data.get('ssot_signals', []) if isinstance(data, dict) else []
    out = str(data.get('output') or '')
    if not out.strip():
        print('[push] 无输出，跳过推送', flush=True)
        return
    text = build_push_text(result, symbols)
    if not text:
        return
    ts = data.get('timestamp') or datetime.now(timezone.utc).isoformat()
    dedup_key = None
    for sig in ssot:
        dedup_key = f"analysis_{sig.get('symbol','')}_{sig.get('direction','')}_{sig.get('action','')}"
        break
    if not dedup_key:
        dedup_key = f"analysis_regular_{ts[:13]}"  # 每小时一波的例行裁决，小时级去重
    ok = push_jarvis(text, priority='P2', dedup_key=dedup_key, dedup_ttl=6*3600)
    print(f'[push] 推送dedup_key={dedup_key} -> {"OK" if ok else "FAIL"}', flush=True)


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

    # [9.23 P0修复 苏摩111] 脚本层独立推送+信号入队（不再依赖AI cron存活）
    _push_result(result, symbols)

    # 输出摘要
    if 'error' in result and not result.get('output'):
        print(f'❌ 分析失败: {result["error"]}')
        # [P0-A 2026-09-24] 失败必须以非零退出码暴露给supercronic——禁止静默success
        sys.exit(3 if result.get('timeout') else 4)
    else:
        print(f'✅ 分析完成: {result.get("elapsed_s", "?")}s, {len(symbols)}个标的')
        # 输出最后500字符摘要
        output = result.get('output', '')
        if output:
            print('\n--- 分析摘要(最后500字符) ---')
            print(output[-500:])
    
    # [唯一裁判封印 2026-09-23 苏摩111] 正则抓文本直写queue已废除：
    # 原逻辑用正则匹配output找ENTER+score>=140并猜方向——完全绕过Gate1-4，
    # 且方向是±200字符猜的，是负分ENTER旁路之一。
    # 新逻辑：优先读result携带的SSOT信号（脚本内联传递），
    # 回退读auto_analysis_latest.json的ssot_signals字段（9.23修复：此前读的
    # 是battlefield_auto_analysis自己save_result写入的字段，而信号由
    # brahma_manual_analysis写入同名文件时被覆盖丢失）
    try:
        SQ_PATH = BASE / 'data' / 'auto_signal_queue.json'
        ssot_signals = result.get('ssot_signals', [])
        if not ssot_signals:
            try:
                _d = json.loads(OUT_PATH.read_text())
                ssot_signals = _d.get('ssot_signals', []) if isinstance(_d, dict) else []
            except Exception:
                ssot_signals = []
        if ssot_signals:
            try:
                # [entry-SSOT P2 2026-09-26 苏摩111] ssot_signals读决策层
                # state['decision']字段优先 → 回退state顶层（entry四字段）
                # 决策层action非ENTER系 = 被唯一裁判否决，不入队
                _enriched = 0
                for _sig in ssot_signals:
                    try:
                        _sym0 = str(_sig.get('symbol', '')).replace('USDT', '').replace('usdt', '').lower()
                        _sp = BASE / 'data' / f'brahma_state_{_sym0}.json'
                        if not _sp.exists():
                            continue
                        _st = json.loads(_sp.read_text())
                        _dec = _st.get('decision') if isinstance(_st.get('decision'), dict) else {}
                        _dec_act = str(_dec.get('action', '') or '').upper()
                        if _dec_act:
                            _sig['action'] = _dec_act
                        for _k in ('entry_lo', 'entry_hi', 'stop_loss', 'entry_source'):
                            _v = _dec.get(_k) if _dec.get(_k) is not None else _st.get(_k)
                            if _v is not None and _sig.get(_k) is None:
                                _sig[_k] = _v
                        _enriched += 1
                    except Exception:
                        continue
                ssot_signals = [s for s in ssot_signals
                                if str(s.get('action', '')).upper().startswith('ENTER')]
                # [9.29 F4 苏摩111] direction硬校验：analysis异常降级时signal_dir='NEUTRAL'
                # （brahma_core L774），入队executor必被R2拒——fail-closed前移，不浪费executor轮次
                _before = len(ssot_signals)
                ssot_signals = [s for s in ssot_signals
                                if str(s.get('direction', '')).upper() in ('LONG', 'SHORT')]
                if len(ssot_signals) < _before:
                    print(f'[auto_analysis] F4: 过滤{_before - len(ssot_signals)}条direction无效信号(NEUTRAL/空)')
                print(f'[auto_analysis] 决策层富化{_enriched}条，剩{len(ssot_signals)}条ENTER系')
                sq = []
                if SQ_PATH.exists():
                    _old = json.loads(SQ_PATH.read_text())
                    if isinstance(_old, dict):
                        sq = _old.get('signals', [])
                    elif isinstance(_old, list):
                        sq = _old
            except Exception:
                sq = []
            sq.extend(ssot_signals)
            SQ_PATH.write_text(json.dumps(sq, indent=2, ensure_ascii=False))
            print(f'[auto_analysis] {len(ssot_signals)}条SSOT信号已入队（calc_factors action裁决，已过Gate1）')
        else:
            print(f'[auto_analysis] 无SSOT ENTER系信号，不写入signal_queue')
    except Exception as e:
        print(f'[auto_analysis] signal_queue写入失败: {e}', file=sys.stderr)
