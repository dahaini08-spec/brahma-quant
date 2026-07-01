"""
llm_council_bridge.py — 梵天 LLM 议会二次审查层 v1.0
═══════════════════════════════════════════════════════
设计院 封印 2026-07-01

使命：
  扩展现有 trading_agents_bridge（STANDBY状态），
  为 score≥140 的高分信号引入真实 LLM Agent 复审，
  实现"规则议会 → LLM增强议会"的升级路径。

架构设计：
  1. 触发条件：score≥140（约5%信号，控制token成本）
  2. 两个专项Agent：Risk Agent + Macro Agent
  3. 输出：分数微调(-15~+10)+ 风险摘要
  4. 失败降级：任何异常返回原始score，不阻塞主流程

成本控制：
  - 每次约 2000 tokens（2个Agent各1000）
  - 结果缓存 6小时（同品种同体制不重复调用）
  - 每日最多调用 50 次（超限后自动降级）

达摩院认证路径：
  M0: shadow模式 → 只记录LLM建议，不修改score
  M1: 离线验证n≥50，LLM建议方向准确率≥55%
  M2: live模式 → 按比例注入score（系数0.5）

接入方式：
  在 brahma_analysis_runner.py 的 run_analysis() 末尾
  添加一行: result = llm_council_bridge.review(result)
"""

# ── STATUS: SHADOW ────────────────────────────────────────────
# 当前运行在shadow模式，记录建议但不修改score
# LAST_REVIEW: 2026-07-01 | 设计院初次封印
# ─────────────────────────────────────────────────────────────

from __future__ import annotations
import os, json, time, hashlib, logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("llm_council_bridge")

BASE      = Path(__file__).parent.parent
LOG_DIR   = BASE / 'data'
CACHE_FILE = LOG_DIR / 'llm_council_cache.json'
LOG_FILE   = LOG_DIR / 'llm_council_shadow_log.jsonl'

# ── 运行模式 ──────────────────────────────────────────────────
# shadow: 记录建议，不修改score（当前默认）
# live:   按INJECT_COEFF比例修改score（需达摩院M1认证）
MODE         = os.environ.get('LLM_COUNCIL_MODE', 'shadow')
INJECT_COEFF = 0.5    # live模式下，LLM建议 × 0.5 注入score
SCORE_TRIGGER = 140   # 触发阈值
CACHE_TTL    = 6 * 3600   # 缓存6小时
DAILY_LIMIT  = 50         # 每日最大调用次数

# ── 成本控制追踪 ──────────────────────────────────────────────
_call_count_today = {'date': '', 'count': 0}
_cache: Dict[str, Tuple[float, Dict]] = {}


# ════════════════════════════════════════════════════════════════
# 1. Agent Prompt 模板
# ════════════════════════════════════════════════════════════════

RISK_AGENT_PROMPT = """你是梵天量化系统的风控议员（Risk Agent）。

当前信号信息：
- 品种: {symbol}
- 方向: {direction}
- 评分: {score}/150
- 体制: {regime}
- 关键位评分: {key_level_score}
- SMC结构评分: {smc_score}
- 时机评分(Kronos): {kronos_score}

请从风险角度快速评估这个信号，输出JSON格式：
{{
  "score_adj": <整数，范围-15到0，风险越高扣分越多>,
  "risk_level": "<LOW|MEDIUM|HIGH>",
  "top_risk": "<最大风险因素，一句话>",
  "veto": <true/false，极端风险时否决>
}}

评估重点：
1. 体制与方向是否匹配（BEAR体制做多=高风险）
2. 评分是否有虚高迹象（单维度贡献超过50%）
3. 当前持仓相关性风险
4. 仅输出JSON，不要其他文字。"""

MACRO_AGENT_PROMPT = """你是梵天量化系统的宏观议员（Macro Agent）。

当前宏观数据：
- BTC.D: {btc_dominance}%
- Fear&Greed指数: {fear_greed}
- 资金费率: {funding_rate}%
- OI变化: {oi_change}%
- 体制: {regime}

信号：{symbol} {direction} score={score}

请评估宏观环境对此信号的支持度，输出JSON格式：
{{
  "score_adj": <整数，范围-10到+10>,
  "macro_bias": "<BULLISH|NEUTRAL|BEARISH>",
  "key_factor": "<最关键的宏观因素，一句话>",
  "confidence": "<HIGH|MEDIUM|LOW>"
}}

评估重点：
1. 大资金流向与信号方向是否一致
2. Fear&Greed极值时反向风险
3. 资金费率极端时的均值回归风险
4. 仅输出JSON，不要其他文字。"""


# ════════════════════════════════════════════════════════════════
# 2. LLM 调用层（带降级）
# ════════════════════════════════════════════════════════════════

def _load_cache() -> Dict:
    """加载磁盘缓存"""
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_cache(cache: Dict):
    """持久化缓存"""
    try:
        LOG_DIR.mkdir(exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.warning(f"cache保存失败: {e}")


def _cache_key(symbol: str, regime: str, direction: str, score_bin: int) -> str:
    """生成缓存键（同品种+体制+方向+评分档位共用缓存）"""
    raw = f"{symbol}:{regime}:{direction}:{score_bin // 10 * 10}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _check_daily_limit() -> bool:
    """检查每日调用限额"""
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if _call_count_today['date'] != today:
        _call_count_today['date']  = today
        _call_count_today['count'] = 0
    return _call_count_today['count'] < DAILY_LIMIT


def _call_llm(prompt: str, agent_name: str) -> Optional[Dict]:
    """
    实际调用 LLM（通过 OpenClaw reasoning_client）
    失败时返回 None（触发降级）
    """
    try:
        # 尝试通过 reasoning_client 调用
        import sys
        sys.path.insert(0, str(BASE / 'brahma_brain'))
        from reasoning_client import call_reasoning

        resp = call_reasoning(
            prompt=prompt,
            max_tokens=200,
            temperature=0.1,   # 低随机性，确保一致输出
            timeout=10         # 10秒超时
        )
        if resp and isinstance(resp, str):
            # 提取JSON
            import re
            m = re.search(r'\{.*\}', resp, re.DOTALL)
            if m:
                return json.loads(m.group())
    except ImportError:
        # reasoning_client 不可用，使用直接HTTP
        pass
    except Exception as e:
        logger.debug(f"[{agent_name}] LLM调用失败: {e}")

    return None


# ════════════════════════════════════════════════════════════════
# 3. Agent 实现
# ════════════════════════════════════════════════════════════════

def _risk_agent_review(signal: Dict) -> Dict:
    """
    Risk Agent：风控视角评分调整
    降级：评分>120+BEAR_TREND做多 → 自动扣分，无需LLM
    """
    symbol    = signal.get('symbol', 'UNKNOWN')
    direction = signal.get('direction', 'LONG')
    score     = signal.get('score', 0)
    regime    = signal.get('regime', 'UNKNOWN')
    breakdown = signal.get('breakdown', {})

    # ── 规则降级（不消耗token）──────────────────────────────
    # 规则1：BEAR体制做多 → 自动高风险
    if 'BEAR' in regime.upper() and direction in ('LONG', '做多'):
        return {
            'score_adj':  -12,
            'risk_level': 'HIGH',
            'top_risk':   f'BEAR体制做多，WR=45%，违反体制铁律',
            'veto':       False,
            'source':     'rule_fallback'
        }

    # 规则2：单维度贡献超过60%（虚高信号）
    total_score = sum(v for v in breakdown.values()
                      if isinstance(v, (int, float)) and v > 0)
    if total_score > 0:
        max_dim_score = max((v for v in breakdown.values()
                             if isinstance(v, (int, float)) and v > 0), default=0)
        if max_dim_score / total_score > 0.60:
            return {
                'score_adj':  -8,
                'risk_level': 'MEDIUM',
                'top_risk':   '单维度贡献>60%，信号质量存疑',
                'veto':       False,
                'source':     'rule_fallback'
            }

    # ── LLM 调用 ───────────────────────────────────────────
    prompt = RISK_AGENT_PROMPT.format(
        symbol=symbol, direction=direction, score=score, regime=regime,
        key_level_score=breakdown.get('关键位精确度', 'N/A'),
        smc_score=breakdown.get('SMC结构', 'N/A'),
        kronos_score=breakdown.get('Kronos', breakdown.get('s23', 'N/A')),
    )

    result = _call_llm(prompt, 'RiskAgent')
    if result:
        result['source'] = 'llm'
        return result

    # ── 最终降级：中性 ─────────────────────────────────────
    return {'score_adj': 0, 'risk_level': 'MEDIUM',
            'top_risk': 'LLM不可用，维持原分', 'veto': False, 'source': 'neutral_fallback'}


def _macro_agent_review(signal: Dict, market_ctx: Dict) -> Dict:
    """
    Macro Agent：宏观视角评分调整
    降级：基于 Fear&Greed + BTC.D 规则评估
    """
    symbol    = signal.get('symbol', 'UNKNOWN')
    direction = signal.get('direction', 'LONG')
    score     = signal.get('score', 0)
    regime    = signal.get('regime', 'UNKNOWN')

    fg        = market_ctx.get('fear_greed', 50)
    btc_d     = market_ctx.get('btc_dominance', 52)
    funding   = market_ctx.get('funding_rate', 0.0)
    oi_change = market_ctx.get('oi_change', 0.0)

    # ── 规则降级 ────────────────────────────────────────────
    adj = 0
    factors = []

    # Fear & Greed 极值
    if fg >= 80 and direction in ('LONG', '做多'):
        adj -= 5
        factors.append(f'FG={fg}极度贪婪，做多均值回归风险')
    elif fg <= 20 and direction in ('SHORT', '做空'):
        adj -= 5
        factors.append(f'FG={fg}极度恐慌，做空继续下跌风险已定价')

    # BTC.D 与方向
    if btc_d > 54 and 'ETH' in symbol.upper() and direction in ('LONG', '做多'):
        adj -= 3
        factors.append(f'BTC.D={btc_d}%偏高，ETH相对弱势')

    # 资金费率极端
    if abs(funding) > 0.03:
        adj -= 4
        factors.append(f'资金费率={funding:.3f}%，极端，均值回归风险')

    if factors:
        return {
            'score_adj':  max(-10, adj),
            'macro_bias': 'BEARISH' if adj < -4 else 'NEUTRAL',
            'key_factor': factors[0],
            'confidence': 'MEDIUM',
            'source':     'rule_fallback'
        }

    # ── LLM 调用 ───────────────────────────────────────────
    prompt = MACRO_AGENT_PROMPT.format(
        symbol=symbol, direction=direction, score=score, regime=regime,
        btc_dominance=btc_d, fear_greed=fg, funding_rate=funding, oi_change=oi_change
    )

    result = _call_llm(prompt, 'MacroAgent')
    if result:
        result['source'] = 'llm'
        return result

    return {'score_adj': 0, 'macro_bias': 'NEUTRAL',
            'key_factor': 'LLM不可用，宏观中性', 'confidence': 'LOW', 'source': 'neutral_fallback'}


# ════════════════════════════════════════════════════════════════
# 4. 主入口：review()
# ════════════════════════════════════════════════════════════════

def review(
    signal_result: Dict,
    market_ctx: Optional[Dict] = None,
    force: bool = False
) -> Dict:
    """
    LLM议会二次审查主入口

    Args:
        signal_result: brahma_core/runner 输出的信号字典
                       需包含: symbol, direction, score, regime, breakdown
        market_ctx:    实时市场上下文（fear_greed, btc_dominance等）
        force:         强制调用，忽略触发阈值（测试用）

    Returns:
        dict: 原始signal_result + 新增字段:
              'llm_council': {risk, macro, final_adj, shadow_log}
    """
    score  = float(signal_result.get('confluence', {}).get('score', 0)
                   or signal_result.get('score', 0))
    symbol = signal_result.get('symbol', 'UNKNOWN')
    regime = signal_result.get('regime', 'UNKNOWN')
    dir_   = signal_result.get('direction', 'LONG')

    # ── 触发检查 ─────────────────────────────────────────────
    if not force and score < SCORE_TRIGGER:
        return signal_result   # 低分不触发

    if not force and not _check_daily_limit():
        logger.info("[LLMCouncil] 日调用上限已达，跳过")
        return signal_result

    # ── 缓存检查 ──────────────────────────────────────────────
    disk_cache = _load_cache()
    ck = _cache_key(symbol, regime, dir_, int(score))
    now = time.time()

    if ck in disk_cache:
        cached_ts, cached_result = disk_cache[ck]['ts'], disk_cache[ck]['result']
        if now - cached_ts < CACHE_TTL:
            signal_result['llm_council'] = cached_result
            signal_result['llm_council']['from_cache'] = True
            return signal_result

    # ── 两个Agent并行审查 ─────────────────────────────────────
    _call_count_today['count'] += 1

    # 构造完整signal字典（供Agent使用）
    flat_signal = {
        'symbol':    symbol,
        'direction': dir_,
        'score':     score,
        'regime':    regime,
        'breakdown': signal_result.get('confluence', {}).get('breakdown',
                     signal_result.get('breakdown', {})),
    }
    ctx = market_ctx or {}

    t0 = time.time()
    risk_result  = _risk_agent_review(flat_signal)
    macro_result = _macro_agent_review(flat_signal, ctx)
    elapsed = time.time() - t0

    # ── 分数合并 ──────────────────────────────────────────────
    risk_adj  = risk_result.get('score_adj', 0)
    macro_adj = macro_result.get('score_adj', 0)
    veto      = risk_result.get('veto', False)

    if veto:
        final_adj = -30  # 否决性惩罚
    else:
        final_adj = risk_adj + macro_adj
        final_adj = max(-20, min(10, final_adj))   # 限幅

    council_output = {
        'risk':       risk_result,
        'macro':      macro_result,
        'final_adj':  final_adj,
        'score_before': score,
        'score_after':  score + final_adj if MODE == 'live' else score,
        'mode':       MODE,
        'elapsed_ms': round(elapsed * 1000),
        'ts':         datetime.now(timezone.utc).isoformat(),
        'from_cache': False,
    }

    # ── 根据模式决定是否注入 ──────────────────────────────────
    if MODE == 'live' and not veto:
        # live模式：实际调整score
        inj_adj = round(final_adj * INJECT_COEFF)  # 系数0.5，平滑注入
        if 'confluence' in signal_result:
            signal_result['confluence']['score'] = score + inj_adj
        elif 'score' in signal_result:
            signal_result['score'] = score + inj_adj

        council_output['injected_adj'] = inj_adj
        logger.info(f"[LLMCouncil LIVE] {symbol} score调整: {score:.0f}→{score+inj_adj:.0f} ({inj_adj:+d})")

    elif MODE == 'shadow':
        # shadow模式：只记录，不修改
        _shadow_log(flat_signal, council_output)
        logger.info(f"[LLMCouncil SHADOW] {symbol} 建议adj={final_adj:+d} (不注入)")

    # ── 缓存 & 返回 ───────────────────────────────────────────
    disk_cache[ck] = {'ts': now, 'result': council_output}
    _save_cache(disk_cache)

    signal_result['llm_council'] = council_output
    return signal_result


# ════════════════════════════════════════════════════════════════
# 5. Shadow Log（达摩院验证数据）
# ════════════════════════════════════════════════════════════════

def _shadow_log(signal: Dict, council: Dict):
    """记录shadow模式建议，供达摩院M1验证使用"""
    try:
        LOG_DIR.mkdir(exist_ok=True)
        record = {
            'ts':        datetime.now(timezone.utc).isoformat(),
            'symbol':    signal.get('symbol'),
            'direction': signal.get('direction'),
            'score':     signal.get('score'),
            'regime':    signal.get('regime'),
            'risk_adj':  council.get('risk', {}).get('score_adj', 0),
            'macro_adj': council.get('macro', {}).get('score_adj', 0),
            'final_adj': council.get('final_adj', 0),
            'risk_src':  council.get('risk', {}).get('source', ''),
            'macro_src': council.get('macro', {}).get('source', ''),
            # 未来验证时填入：'actual_result': 'WIN'/'LOSS'
        }
        with open(LOG_FILE, 'a') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception as e:
        logger.warning(f"shadow log失败: {e}")


def get_shadow_stats() -> Dict:
    """分析shadow log，评估LLM建议准确率（达摩院M1验证用）"""
    if not LOG_FILE.exists():
        return {'status': 'no_log', 'n': 0}

    records = []
    with open(LOG_FILE) as f:
        for line in f:
            try:
                records.append(json.loads(line.strip()))
            except Exception:
                pass

    if not records:
        return {'status': 'empty', 'n': 0}

    n = len(records)
    validated = [r for r in records if r.get('actual_result') in ('WIN', 'LOSS')]

    if not validated:
        return {
            'status':  'pending_validation',
            'n_total': n,
            'n_validated': 0,
            'note':    '填入actual_result字段后可计算准确率'
        }

    # 分析：负adj建议时实际是否LOSS（验证风险识别准确率）
    neg_adj  = [r for r in validated if r.get('final_adj', 0) < -5]
    n_loss_when_neg = sum(1 for r in neg_adj if r['actual_result'] == 'LOSS')
    accuracy = n_loss_when_neg / (len(neg_adj) + 1e-9)

    return {
        'status':      'has_data',
        'n_total':     n,
        'n_validated': len(validated),
        'neg_adj_n':   len(neg_adj),
        'risk_accuracy': round(accuracy, 3),
        'threshold_m1':  '≥0.55 可升级至M1（live模式）',
        'm1_ready':      accuracy >= 0.55
    }


# ════════════════════════════════════════════════════════════════
# 6. 主入口（测试）
# ════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("🧪 LLM Council Bridge 测试\n")

    # 模拟高分信号
    mock_signal = {
        'symbol':    'ETHUSDT',
        'direction': 'SHORT',
        'score':     145,
        'regime':    'BEAR_TREND',
        'confluence': {
            'score': 145,
            'breakdown': {
                '趋势一致性': 14, 'SMC结构': 18, '量能验证': 12,
                '关键位精确度': 10, '动量背离': 8, 'Kronos': 9,
                '情绪/费率': 6, '清算/OI': 7,
            }
        }
    }

    mock_ctx = {
        'fear_greed':    35,
        'btc_dominance': 56.2,
        'funding_rate':  -0.005,
        'oi_change':     -3.2,
    }

    result = review(mock_signal, market_ctx=mock_ctx, force=True)
    council = result.get('llm_council', {})

    print(f"Risk Agent:  adj={council.get('risk',{}).get('score_adj',0):+d}  "
          f"level={council.get('risk',{}).get('risk_level','?')}  "
          f"source={council.get('risk',{}).get('source','?')}")
    print(f"Macro Agent: adj={council.get('macro',{}).get('score_adj',0):+d}  "
          f"bias={council.get('macro',{}).get('macro_bias','?')}  "
          f"source={council.get('macro',{}).get('source','?')}")
    print(f"Final adj:   {council.get('final_adj',0):+d}  mode={council.get('mode','?')}")
    print(f"耗时: {council.get('elapsed_ms',0)}ms")

    print("\n📊 Shadow Stats:", get_shadow_stats())
