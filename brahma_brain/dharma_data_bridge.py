#!/usr/bin/env python3
"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# 达摩院数据桥接,训练辅助
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层,修改前确认调用链
# ─────────────────────────────────────────────────────────────
dharma_data_bridge.py - 达摩院数据桥接器 v2.0
设计院 × 达摩院 2026-06-18

职责:
  1. brahma_core.analyze() 完成后,将信号写入 live_signal_log.jsonl
  2. 标准化字段格式(signal_id / ts / 入场区 / 评分 / 关键位12字段)
  3. 写入失败不阻断主流程(try/except 静默降级)

[BUG-FIX 2026-06-18] 文件不存在导致整个实训闭环断路,本文件补全修复。
"""

import json
import time
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

BASE = Path(__file__).parent.parent
LOG_PATH = BASE / 'data' / 'live_signal_log.jsonl'
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

# ── 结算状态枚举 ──────────────────────────────────────────────────
STATUS_OPEN      = 'OPEN'
STATUS_WIN_T1    = 'WIN_T1'
STATUS_WIN_T2    = 'WIN_T2'
STATUS_LOSS      = 'LOSS'
STATUS_TIMEOUT   = 'TIMEOUT'
STATUS_EXPIRED   = 'EXPIRED'
STATUS_SUPERSEDED = 'SUPERSEDED'


def _make_signal_id(symbol: str, ts: float, direction: str, entry_lo: float = 0.0) -> str:
    """生成唯一信号ID(12位hex)
    [P1-2 设计院修复 2026-07-18 苏摩111封印]
    加入entry_lo作为hash因子，防止同一entry区间在1s内重复写入产生不同signal_id
    """
    # ts精度到1s（原3ms导致同次扫描两路调用产生不同id）
    # entry_lo取整数部分作为区分因子
    _entry_bucket = int(entry_lo * 1000) if entry_lo > 0 else 0
    raw = f"{symbol}:{int(ts)}:{direction}:{_entry_bucket}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def log_signal(result: dict) -> bool:
    """
    将 brahma_core.analyze() 的返回结果写入 live_signal_log.jsonl。

    参数:
        result: brahma_core.analyze() 的完整返回dict

    返回:
        True = 写入成功,False = 跳过(无效信号或score不足)
    """
    try:
        cf      = result.get('confluence', {}) or {}
        params  = result.get('params', {}) or {}
        symbol  = result.get('symbol', '')
        direction = result.get('signal_dir', result.get('direction', ''))
        score   = float(cf.get('total', result.get('score', 0)) or 0)
        action  = cf.get('action', '')
        regime  = result.get('regime', '')
        price   = float(result.get('price', 0) or 0)
        grade   = cf.get('grade', '')
        now_ts  = time.time()

        # 跳过无效信号
        if not symbol or not direction or score < 60:
            return False

        # ── v5.0 Step5: BB宽度过滤(设计院2026-07-01 苏摩111批准)──────────
        # BB宽度<0.5%=极度压缩期,方向未定,信号噪音极高,强制跳过写入
        # 节省积分:过滤掉压缩期产生的低质量信号
        _bb_w = float(result.get('bb_width', 1.0) or result.get('confluence', {}).get('bb_width', 1.0) or 1.0)
        if _bb_w < 0.5 and score < 155:
            pass  # [静默]
            return False
        # ────────────────────────────────────────────────────────────────────

        # TTL(基于timeframe + score + 体制)
        # [v25.7 P0b 2026-06-21] 动态TTL:高分信号 + 顺势体制 多等
        # [v5.3 2026-07-08] TTL硬上限6H,信号窗口最长30min,15H的TTL导致过期信号反复被扫描
        _ttl_map = {'4H': 3*3600, '1H': 1*3600, '1D': 6*3600}
        primary_tf = cf.get('primary_tf', params.get('primary_tf', '1H'))
        ttl = _ttl_map.get(str(primary_tf).upper(), 2*3600)
        # 动态延长:评分越高 + 顺势体制 → 多等
        _regime_for_ttl = regime or ''
        _dir_for_ttl    = direction or ''
        _score_for_ttl  = float(score or 0)
        # 评分加成(神级信号多等一天)
        if _score_for_ttl >= 165:
            ttl = int(ttl * 2.0)   # 神级:2倍(最多48H居察期)
        elif _score_for_ttl >= 155:
            ttl = int(ttl * 1.2)   # 标准门槛: 1.2倍
        # 顺势体制加成(铁证顺势方向多等待)
        _strong_regimes = {
            ('BEAR_TREND', 'SHORT'), ('BEAR_EARLY', 'SHORT'),
            ('BULL_TREND', 'LONG'),  ('BULL_EARLY', 'LONG'),
            ('BEAR_RECOVERY', 'LONG'),  # WR=72.5%,反直觉但铁证顼
        }
        if (_regime_for_ttl, _dir_for_ttl) in _strong_regimes:
            ttl = int(ttl * 1.25)  # 顺势体制额外+25%
        # 上限:任何信号TTL不超过48H(CHOP体制市场就是乱的)
        _ttl_ceil = {'CHOP_HIGH': 4*3600, 'CHOP_MID': 6*3600, 'CHOP_LOW': 8*3600}
        if _regime_for_ttl in _ttl_ceil:
            ttl = min(ttl, _ttl_ceil[_regime_for_ttl])
        else:
            ttl = min(ttl, 6*3600)   # 硬上限6H(v5.3)

        signal = {
            # 基础标识
            'signal_id':      _make_signal_id(symbol, now_ts, direction, float(params.get('entry_lo', 0) or 0)),
            'ts':             now_ts,
            'ts_iso':         datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat(),
            'symbol':         symbol,
            'signal_dir':     direction,
            'direction':      direction,
            'regime':         regime,
            'regime_cn':      result.get('regime_cn', ''),

            # 评分
            'score':          score,
            'score_final':    float(result.get('score_final', score) or score),  # [P0-3 fix 2026-07-18]
            'grade':          grade,
            'action':         action,
            # [FIX-v25.5] valid单一来源: params['valid'](brahma_core正确计算RR的结果)
                        # [v5.3 2026-07-08 设计院封印] BULL_TREND特例门槛修复
            # v5.2设置 score≥138 → 导致 score=144 被强制valid=True,与宪法(score≥155)冲突
            # v5.3修复: 门槛与宪法一致,score≥155
            'valid': (
                # [v5.5 设计院自主修复 2026-07-13] SAMSUNG漏洞根因封印
                # 根因：ATR扩展重算分支(brahma_engine L1152)直接设params['valid']=True，绕过score门控
                # 表现：score=90.3 BEAR_TREND SHORT → valid=True（signal_trace展示误导）
                # 真正防线：entry_lo=null时不进入执行链；signal_bus有score≥100二次门控
                # 此处新增：score绝对下限门控，无论引擎层如何设置params['valid']
                # 任何score<100的信号，在bridge层一律拦截（不影响正常信号：120+才能到达此处）
                (
                    bool(params.get('valid', False))
                    and float(score or 0) >= 100  # [v5.5 绝对下限门控] 防止ATR扩展分支绕过score=90.3进入有效信号
                )
                # ── v5.4 修复 [2026-07-10 苏摩111批准] ──────────────────────────────
                # 根因诊断：20条score≥135信号全部valid=False，所有高分信号永远无法执行
                # 原限制：仅BULL_TREND途径 + score≥1551道门
                # 扩展：将BULL_TREND/BEAR_RECOVERY多体制覆盖 + score限刽降至135（与宣法auto_executor一致）
                # 铁证依据：
                #   BULL_TREND  LONG  WR实测>68%  n=2000+
                #   BEAR_RECOVERY LONG WR=72.5%  n=603
                #   CHOP_MID  SHORT WR=65%+  n=800+  EV=+0.811%/笔
                or (
                    (
                        'BULL_TREND' in (regime or '')    # BULL_TREND LONG
                        or 'BEAR_RECOVERY' in (regime or '')  # BEAR_RECOVERY LONG "反直觉alpha"
                        or ('CHOP' in (regime or '') and (direction or '') == 'SHORT')  # CHOP 做空 EV=+0.811%
                        or ('BEAR_TREND' in (regime or '') and (direction or '') == 'SHORT')  # BEAR做空主方向
                    )
                    and float(score or 0) >= 120         # [P0-C 设计院六方 2026-07-11] CHOP门槛 130→120，解锁更多 CHOP SHORT有效信号
                    # 铁证：CHOP_MID SHORT WR=65%+ n=800+ EV=+0.811%/笔，score=120~130也正期望
                    # 简化：全体体制门槛统一降至120（防止CHOP被过度拦截）
                    and float(params.get('rr1', 0) or 0) >= 1.0  # RR阈值不变
                    and float(params.get('sl_pct', 0) or 0) <= 15.0
                    and action not in ('SKIP',)          # SKIP是死穴，不开封
                )
            ),

            # 价格参数
            'price':          price,
            'generated_price': price,
            'entry_lo':       params.get('entry_lo', 0),
            'entry_hi':       params.get('entry_hi', 0),
            'stop_loss':      params.get('stop_loss', params.get('sl', 0)),
            'tp1':            params.get('tp1', 0),
            'tp2':            params.get('tp2', 0),
            'sl_pct':         params.get('sl_pct', 0),
            'rr1':            params.get('rr1', 0),
            'primary_tf':     primary_tf,
            'entry_tf':       cf.get('entry_tf', params.get('entry_tf', '')),

            # TTL
            'expires_at':     datetime.fromtimestamp(now_ts + ttl, tz=timezone.utc).isoformat(),
            'ttl_hours':      ttl / 3600,

            # 关键位12字段(达摩院key_level_validator使用)
            'entry_source':   params.get('entry_source', ''),
            'ob_dist_pct':    params.get('ob_dist_pct', 0),
            'ob_top':         params.get('ob_top', 0),
            'ob_bottom':      params.get('ob_bottom', 0),
            'ob_source_type': params.get('ob_source_type', ''),
            'fvg_active':     params.get('fvg_active', False),
            'fvg_top':        params.get('fvg_top', 0),
            'fvg_bottom':     params.get('fvg_bottom', 0),
            'swing_high_4h':  params.get('swing_high_4h', 0),
            'swing_low_4h':   params.get('swing_low_4h', 0),
            'key_level_proximity': params.get('key_level_proximity', 0),
            'mtf_override':   params.get('mtf_override', False),

            # MTF元数据
            'mtf_mode':       cf.get('v2_mode', ''),
            'mtf_4h_align':   (cf.get('v2_breakdown') or {}).get('v2_mtf_4h_align', ''),
            'kelly_pct':      cf.get('v2_pos_pct', 0),

            # 结算状态(初始OPEN)
            'status':         STATUS_OPEN,
            'result':         None,
            'exit_price':     None,
            'pnl_pct':        None,
            'settled_at':     None,

            # [设计院 A2 2026-06-30] BRAHMA标签 + 饱满字段集(防混淤防误执行)
            'output_tag':      result.get('_runner_meta', {}).get('output_tag', ''),
            'structure_grade': int(float(cf.get('effective_grade', cf.get('structure_grade', 0)) or 0)),
            'gex_min':         (cf.get('breakdown', {}) or {}).get('_gex_min'),
            'trigger_conf':    (result.get('extra', {}) or {}).get('trigger', {}).get('confidence'),
            'consensus':       (result.get('extra', {}) or {}).get('multitf', {}).get('consensus', ''),
            'rsi_1h':          (result.get('momentum', {}) or {}).get('rsi_1h'),
            'rsi_4h':          (result.get('momentum', {}) or {}).get('rsi_4h'),
            # [fix 2026-07-18 苏摩111] timing层字段回写：brahma_engine调用log_signal时从 result提取
            # brahma_analysis_runner在run_analysis后把timing写入result，然后brahma_engine直接调用本函数
            # result中可能已有timing字段或无，无则留空供后续回写
            'timing_badge':    result.get('timing_badge', ''),
            'timing_status':   result.get('timing_status', ''),
            'timing_score':    result.get('timing_score', 0),
        }

        # ── [P2 设计院 2026-07-13] 信号去重修复版 ─────────────────────────────
        # 修复：(1) 只读最近200行（不全量扫描），(2) entry_lo=0时走signal_id去重
        # 修复：(3) 已结算(status!=OPEN)的记录不参与去重判断
        _entry_lo_new  = float(signal.get('entry_lo', 0) or 0)
        _dedup_window  = 0.005  # 0.5%
        _dedup_ts_window = 3 * 3600  # 3H去重窗口（原6H过长）
        if LOG_PATH.exists():
            try:
                # 只读最后200行，避免全量IO
                _all_lines = LOG_PATH.read_text(encoding='utf-8').splitlines()
                _recent = []
                for _l in _all_lines[-200:]:
                    try: _recent.append(json.loads(_l))
                    except: pass
                _now_ts = time.time()
                for _r in _recent:
                    if _r.get('symbol') != symbol: continue
                    if (_r.get('signal_dir') or _r.get('direction')) != direction: continue
                    if _r.get('status') != STATUS_OPEN: continue  # 跳过已结算
                    _age = _now_ts - float(_r.get('ts', 0) or 0)
                    if _age > _dedup_ts_window: continue           # 超过3H不去重
                    # entry_lo去重
                    if _entry_lo_new > 0:
                        _entry_lo_r = float(_r.get('entry_lo', 0) or 0)
                        if _entry_lo_r > 0 and abs(_entry_lo_r - _entry_lo_new) / max(_entry_lo_new, 1) < _dedup_window:
                            return False  # 去重命中
                    else:
                        # entry_lo=0时用score+regime去重（防止无参数信号重复写入）
                        _score_r = float(_r.get('score', 0) or 0)
                        if abs(_score_r - score) < 5 and _r.get('regime') == regime:
                            return False  # score近似+体制相同视为重复
            except Exception:
                pass  # 去重失败不阻断

        # 写入(追加模式,原子写入)
        line = json.dumps(signal, ensure_ascii=False) + '\n'
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(line)

        # [v5.2 设计院 2026-07-04] 同步写入统一信号总线
        if signal.get('valid') and float(signal.get('score', 0)) >= 100:
            try:
                import sys as _sys
                _sys.path.insert(0, str(BASE / 'scripts'))
                from signal_bus import write as _bus_write
                _bus_write({
                    'source':     'main',
                    'symbol':     signal.get('symbol'),
                    'direction':  signal.get('direction') or signal.get('signal_dir'),
                    'score':      signal.get('score'),
                    'valid':      signal.get('valid'),
                    'regime':     signal.get('regime'),
                    'entry_lo':   signal.get('entry_lo'),
                    'entry_hi':   signal.get('entry_hi'),
                    'sl':         signal.get('stop_loss') or signal.get('sl'),
                    'sl_pct':     signal.get('sl_pct'),
                    'tp1':        signal.get('tp1'),
                    'tp2':        signal.get('tp2'),
                    'rr1':        signal.get('rr1'),
                    'expires_at': signal.get('expires_at'),
                    'signal_id':  signal.get('signal_id') or signal.get('output_tag'),
                })
            except Exception:
                pass  # 不阻断主流程

        return True

    except Exception as e:
        # 不阻断主流程
        import sys
        pass  # [静默] f'[DharmaBridge] ⚠ 写入失败: {e}', file=sys.stderr
        return False


def get_stats() -> dict:
    """统计 live_signal_log.jsonl 当前状态"""
    try:
        lines = [json.loads(l) for l in LOG_PATH.read_text().splitlines() if l.strip()]
        total    = len(lines)
        settled  = [s for s in lines if s.get('result')]
        wins     = [s for s in settled if 'WIN' in str(s.get('result', ''))]
        losses   = [s for s in settled if s.get('result') == STATUS_LOSS]
        timeouts = [s for s in settled if s.get('result') in (STATUS_TIMEOUT, STATUS_EXPIRED)]
        wr = len(wins) / (len(wins) + len(losses)) if (wins or losses) else 0
        return {
            'total':    total,
            'settled':  len(settled),
            'running':  total - len(settled),
            'wins':     len(wins),
            'losses':   len(losses),
            'timeouts': len(timeouts),
            'win_rate': wr,
        }
    except Exception:
        return {'total': 0, 'settled': 0, 'running': 0,
                'wins': 0, 'losses': 0, 'timeouts': 0, 'win_rate': 0}


if __name__ == '__main__':
    import sys
    if '--stats' in sys.argv:
        s = get_stats()
        print(f"📊 live_signal_log 统计")
        print(f"   总计: {s['total']} | 已结算: {s['settled']} | 运行中: {s['running']}")
        print(f"   WR={s['win_rate']*100:.1f}% W={s['wins']} L={s['losses']} T={s['timeouts']}")
    else:
        print(f"DharmaBridge v2.0 | 日志路径: {LOG_PATH}")
        print(f"当前记录: {get_stats()['total']} 条")

# 差异化阈值 [苏摩111批准 2026-07-14]
REGIME_THRESHOLD = {
    'BEAR_RECOVERY': 120,
    'BULL_TREND':    160,
    'BEAR_TREND':    138,
    'CHOP_MID':      138,
    'BEAR_EARLY':    138,
    'BULL_EARLY':    150,
}

def get_threshold(regime: str) -> int:
    return REGIME_THRESHOLD.get(regime, 155)
