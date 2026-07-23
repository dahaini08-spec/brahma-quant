#!/usr/bin/env python3
"""
brahma_compact_runner.py — 梵天分析结果压缩包装器
将brahma_1hao_analysis.py的135行输出压缩为5行结构化摘要
P0-A 上下文压缩落地 [苏摩111 2026-07-19]
"""
import sys, subprocess, re
from pathlib import Path

BASE = Path(__file__).parent.parent

def run_compact(symbol: str) -> str:
    """运行分析并返回压缩摘要（5行以内）"""
    r = subprocess.run(
        ['python3', 'scripts/brahma_1hao_analysis.py', '--symbols', symbol],
        capture_output=True, text=True, timeout=30, cwd=str(BASE)
    )
    out = r.stdout + r.stderr

    # 提取关键字段
    score = re.search(r'score=([0-9.]+)', out)
    grade = re.search(r'grade=([0-9.]+)', out)
    direction = re.search(r'方向:\s*(LONG|SHORT)', out)
    regime = (re.search(r'[（(]([A-Z_]{4,})[）)]', out) or
               re.search(r'_regime\s+([A-Z_]{4,})', out) or
               re.search(r'(?:体制|REGIME)[：:]\s*([A-Z_]+)', out))
    timing = re.search(r'(READY|MONITOR|WAIT|STANDBY)', out)
    # timing_badge补充：若正则未匹配则调用timing_filter动态计算
    if not timing:
        try:
            import sys as _sys; _sys.path.insert(0, str(BASE / 'brahma_brain'))
            from timing_filter import evaluate_timing, format_timing_badge
            import requests as _rq
            _px = float(_rq.get('https://fapi.binance.com/fapi/v1/ticker/price?symbol=' + symbol, timeout=3).json()['price'])
            _dir = (re.search(r'方向: *(LONG|SHORT)', out) or type('',(),{'group':lambda s,i:"LONG"})())
            _d = _dir.group(1) if hasattr(_dir,'group') else 'LONG'
            _s = float(score.group(1)) if score else 100
            _g = float(grade.group(1)) if grade else 80
            _tr = evaluate_timing(symbol, _d, current_price=_px, entry_lo=_px*0.99, entry_hi=_px*1.001, score=_s, grade=_g)
            _tb = _tr.get('status', 'UNKNOWN')
            timing = type('', (), {'group': lambda self, i: _tb})()
        except Exception:
            pass
    blocked = 'BLOCKED' in out or 'globally_blocked' in out or '封禁' in out
    entry = re.search(r'入场区[：:]\s*([^\n]+)', out)
    sl = re.search(r'参考止损[：:]\s*([^\n]+)', out)
    tp1 = re.search(r'参考TP1[：:]\s*([^\n]+)', out)

    if r.returncode != 0 and not score:
        return f"ERROR: {out[-100:]}"

    s = float(score.group(1)) if score else 0
    g = float(grade.group(1)) if grade else 0

    # GATE-0封禁 → 立即返回，不注入长文本
    if blocked and s < 138:
        return f"HEARTBEAT_OK  [{symbol} score={s:.0f} BLOCKED/低分]"

    # 低分 → 简短返回
    if s < 138:
        return f"HEARTBEAT_OK  [{symbol} score={s:.0f} < 138]"

    # 有效信号 → 结构化5行摘要
    dir_str = direction.group(1) if direction else '?'
    reg_str = regime.group(1) if regime else '?'
    tim_str = timing.group(1) if timing else '?'
    ent_str = entry.group(1).strip()[:40] if entry else '?'
    sl_str  = sl.group(1).strip()[:30] if sl else '?'
    tp1_str = tp1.group(1).strip()[:30] if tp1 else '?'

    return (
        f"SIGNAL  {symbol} {dir_str}  score={s:.0f}  grade={g:.0f}  regime={reg_str}  timing={tim_str}\n"
        f"  进场区: {ent_str}\n"
        f"  止损:  {sl_str}\n"
        f"  TP1:   {tp1_str}\n"
        f"  → score>={'155✅' if s>=155 else '138⚡'} 推送信号卡"
    )

if __name__ == '__main__':
    sym = sys.argv[1] if len(sys.argv) > 1 else 'BTCUSDT'
    print(run_compact(sym))


def run_compact_with_memory(symbol: str) -> str:
    """
    带专家记忆的compact分析：
    1. 分析前注入历史专家洞察（替代重新推理）
    2. 分析后自动更新专家记忆
    P1落地 [苏摩111 2026-07-19]
    """
    import sys as _sys
    _sys.path.insert(0, str(BASE / 'scripts'))
    try:
        from expert_memory_manager import (
            get_compact_summary, update_expert, update_consensus, load_memory
        )
        memory_ctx = get_compact_summary()
    except Exception:
        memory_ctx = ''

    # 运行分析
    result = run_compact(symbol)

    # 分析后更新记忆
    try:
        import re as _re, time as _time
        if 'SIGNAL' in result:
            sm = _re.search(r'score=([0-9.]+)', result)
            rm = _re.search(r'regime=([A-Z_]+)', result)
            dm = _re.search(r'(LONG|SHORT)', result)
            tm = _re.search(r'(READY|MONITOR|WAIT)', result)
            if sm and rm:
                score = float(sm.group(1))
                regime = rm.group(1)
                direction = dm.group(1) if dm else 'NEUTRAL'
                timing = tm.group(1) if tm else 'UNKNOWN'
                update_expert('量化工程师', {
                    'last_signal_score': score,
                    'last_signal_symbol': symbol,
                    'last_regime': regime,
                    'calibration_note': f'{symbol} score={score:.0f} {regime} {timing}'
                })
                update_expert('合约交易员', {
                    'current_regime_strategy': f'{regime}: {direction}为主',
                    'position_bias': direction
                })
                if score >= 155:
                    update_consensus(
                        btc_dir=direction if 'BTC' in symbol else None,
                        eth_dir=direction if 'ETH' in symbol else None,
                        agreement=4, confidence=score/200
                    )
                # [FIX-ROOT 2026-07-23 苏摩111] score≥138时自动写入signal_bus
                # 根因：compact_runner只输出stdout，signal_bus.write()从未被调用
                # 修复：在这里直接写入，触发main-signal-watcher推送
                if score >= 138:
                    try:
                        import sys as _sys2, time as _time2, re as _re2
                        _sys2.path.insert(0, str(BASE/'scripts'))
                        _sys2.path.insert(0, str(BASE/'brahma_brain'))
                        _sys2.path.insert(0, str(BASE))
                        from signal_bus import write as _bus_write
                        # 从result文本提取入场参数
                        _elo = _re2.search(r'(\d{3,6}\.?\d*)\s*~', result)
                        _ehi = _re2.search(r'~\s*(\d{3,6}\.?\d*)', result)
                        _slm = _re2.search(r'止损.*?(\d{3,6}\.?\d*)', result)
                        _tp1 = _re2.search(r'TP1.*?(\d{3,6}\.?\d*)', result)
                        _elo_v = float(_elo.group(1)) if _elo else 0
                        _ehi_v = float(_ehi.group(1)) if _ehi else 0
                        _sl_v  = float(_slm.group(1)) if _slm else (_elo_v*0.98 if _elo_v else 0)
                        _tp1_v = float(_tp1.group(1)) if _tp1 else 0
                        _rr    = round((_tp1_v-_ehi_v)/(_ehi_v-_sl_v),2) if (_ehi_v and _sl_v and _tp1_v and _ehi_v!=_sl_v) else 1.0
                        _ts_now = _time2.time()
                        import hashlib as _hlib
                        _sid = _hlib.md5(f"{symbol}{direction}{score}{int(_ts_now//1800)}".encode()).hexdigest()[:12]
                        _sig = {
                            'source': 'brahma_compact_runner',
                            'symbol': symbol,
                            'direction': direction,
                            'score': score,
                            'grade': g if 'g' in dir() else 0,
                            'structure_grade': int(score * 0.5),
                            'effective_grade': float(score * 0.5),
                            'regime': regime,
                            'valid': True,
                            'timing_badge': timing,
                            'entry_lo': _elo_v,
                            'entry_hi': _ehi_v,
                            'stop_loss': _sl_v,
                            'sl': _sl_v,
                            'sl_pct': round((_ehi_v-_sl_v)/_ehi_v*100, 2) if _ehi_v else 2.0,
                            'tp1': _tp1_v,
                            'tp2': round(_tp1_v + (_tp1_v - _ehi_v), 2) if _tp1_v and _ehi_v else 0,
                            'rr1': _rr,
                            'action': 'ENTER',
                            'signal_id': _sid,
                            'output_tag': f'[BRAHMA:SIG:RUNNER:{symbol}:{score:.0f}:{direction}:{regime}:{int(_ts_now)}:{_sid[:8]}]',
                        }
                        _ok = _bus_write(_sig)
                        # [注入 2026-07-23] 跨资产联合推理门控
                        # BTC未到位时ETH信号自动降级为WAIT，防止矛盾开单
                        try:
                            from brahma_brain.cross_asset_gate import get_gate as _get_cag
                            _gate = _get_cag()
                            # 收集当前批次所有信号（从signal_bus最近记录）
                            import json as _json_cag
                            _peer_lines = (BASE/'data'/'signal_bus.jsonl').read_text().strip().split('\n')[-20:]
                            _peers = []
                            for _pl in _peer_lines:
                                try: _peers.append(_json_cag.loads(_pl))
                                except: pass
                            _peers.append(_sig)  # 含当前信号
                            _sig = _gate.check(_sig, peer_signals=_peers)
                            if _sig.get('cross_asset_triggered'):
                                import logging as _log_cag
                                _log_cag.warning('[cross_asset_gate] %s 降级WAIT: %s',
                                                  symbol, _sig.get('cross_asset_reason',''))
                        except Exception as _cag_e:
                            pass  # 门控失败不阻断主流程
                        # [事件驱动] score≥155立即直推，不等watcher轮询
                        if score >= 155 and _ok and not _sig.get('cross_asset_triggered'):
                            try:
                                import sys as _sys3
                                _sys3.path.insert(0, str(BASE/'scripts'))
                                from push_hub import push_signal_card as _push_card
                                _push_card(
                                    sym=symbol,
                                    score=score,
                                    grade=_sig.get('grade', '?'),
                                    direction=direction,
                                    entry_lo=_elo_v,
                                    entry_hi=_ehi_v,
                                    sl=_sl_v,
                                    tp1=_tp1_v,
                                    timing=timing,
                                )
                            except Exception:
                                pass  # 推送失败不影响主流程
                    except Exception as _e_bus:
                        pass  # signal_bus写入失败不影响主流程
    except Exception:
        pass

    # 在结果中附加记忆上下文（仅当有实质内容时）
    if memory_ctx and memory_ctx != '专家记忆: 积累中，首次分析' and 'SIGNAL' in result:
        result = f"{result}\n  记忆: {memory_ctx}"

    return result


if __name__ == '__main__':
    import sys as _sys
    sym = _sys.argv[1] if len(_sys.argv) > 1 else 'BTCUSDT'
    use_memory = '--memory' in _sys.argv
    if use_memory:
        print(run_compact_with_memory(sym))
    else:
        print(run_compact(sym))
