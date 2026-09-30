#!/usr/bin/env python3
"""
free_llm_client.py — OpenRouter免费LLM客户端
设计院深度思考封印 2026-09-05 苏摩111

▌ 梵天思维全局注入版
  - 每个模型调用自动携带梵天宪法 System Prompt
  - 任务路由表：不同任务使用最适合的专项模型
  - 冷却管理：防429，自动轮换

▌ 任务路由表（TASK_MODEL_MAP）：
  council      → minimax-m3        中文AI议会主裁决
  regime       → nemotron-super    宏观体制推理
  wr_audit     → ling-fin          金融WR审核
  hcme         → inkling           思维链历史镜像
  chop         → nemotron-light    快速CHOP突破验证
  safety       → nemotron-safety   AVOID安全门控
  vip          → minimax-m3        VIP一句话摘要
  review       → ling-fin          结算复盘lesson
  oi           → minimax-m3        OI聪明钱解读
  default      → minimax-m3        通用兜底

接入位置：
  brahma_brain/llm_council.py   (AI议会真实LLM裁决)
  scripts/regime_switch_monitor.py
  scripts/oi_watchlist_monitor.py
  scripts/wr_feedback_engine.py
  scripts/signal_settler.py
  brahma_brain/fangcang_engine.py
  brahma_brain/chop_breakout_detector.py
"""
import json, os, ssl, time, urllib.request, urllib.error
from pathlib import Path
import sys
import datetime as _dt

# ── Key加载 ──────────────────────────────────────────────────────────────
def _load_key() -> str:
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith('OPENROUTER_API_KEY='):
                return line.split('=', 1)[1].strip()
    k = os.environ.get('OPENROUTER_API_KEY', '')
    if k:
        return k
    return ''

API_KEY  = _load_key()
BASE_URL = 'https://openrouter.ai/api/v1/chat/completions'

# ── 任务路由表：task → 专项模型 (2026-09-05 苏摩111封印 / 2026-09-27重修) ─
# [2026-09-27 苏摩111] 通道全灭事故修复：
#   - minimax-m3:free被OpenRouter下架转付费（404 "unavailable for free"）→ 永久踢出
#   - super-120b/lightning为思考链泄漏模型（content装英文推理，finish=length）→ 降级fallback
#   - ultra-550b实测干净可用+中文最佳（finish=stop）→ 全任务主模型
#   - ling-fin/gemma受上游provider日配额限流（429波动）→ 尾部fallback
# 永久不可用: inkling系列(403) / deepseek全系(404付费) / llama-4-maverick(404付费)
TASK_MODEL_MAP = {
    'council':  'nvidia/nemotron-3-ultra-550b-a55b:free',    # 中文最佳★★★★★ 议会主裁决
    'vip':      'nvidia/nemotron-3-ultra-550b-a55b:free',    # 中文最佳 VIP摘要
    'oi':       'nvidia/nemotron-3-ultra-550b-a55b:free',    # 中文最佳 OI解读
    'regime':   'nvidia/nemotron-3-ultra-550b-a55b:free',    # 550B深度推理★★★★☆ 宏观体制
    'wr_audit': 'nvidia/nemotron-3-ultra-550b-a55b:free',    # 金融可用主力
    'review':   'nvidia/nemotron-3-ultra-550b-a55b:free',    # 复盘lesson
    'hcme':     'nvidia/nemotron-3-ultra-550b-a55b:free',    # 550B长上下文 历史镜像
    'chop':     'nvidia/nemotron-3-ultra-550b-a55b:free',    # CHOP验证
    'safety':   'nvidia/nemotron-3-ultra-550b-a55b:free',    # 门控
    'default':  'nvidia/nemotron-3-ultra-550b-a55b:free',    # 永久兜底
}

# fallback链：主模型失败时的备选顺序 [9.27重修]
FALLBACK_MODELS = [
    'nvidia/nemotron-3-super-120b-a12b:free',   # 可用但思考链泄漏（finish=length拒收）
    'qwen/qwen3.8-27b:free',                    # [9.27新增] 中文可用备选
    'inclusionai/ling-3.0-flash-fin:free',      # 上游日配额波动，重置窗口可能恢复
    'google/gemma-4-31b-it:free',               # 同上
]

# ── 梵天宪法 System Prompt（所有LLM调用自动注入）────────────────────────
BRAHMA_CONSTITUTION = """你是梵天量化系统的专项AI分析员。
梵天宪法铁律（绝对不可违反）：
1. BEAR_TREND体制做多WR=45% → 必须输出AVOID，不论score多高
2. BULL_TREND体制做空WR=38% → 必须输出AVOID，不论score多高
3. SL距离必须≥1.5×ATR1H → 不满足则输出WAIT
4. 无FVG+OB+清算三因子共振 → 输出WAIT，不给具体入场价
5. 回答必须简洁精准，不超过规定字数，禁止废话和重复
你的任何判断都不得违反以上铁律。"""

# ── 冷却管理：防429，同一模型3秒内不重复调用 ─────────────────────────
_model_last_called: dict = {}   # {model_id: last_call_timestamp}
_global_backoff_until = 0.0     # [V3] 全局429退避：全池共享配额，一模型429=全池退避
_backoff_until_iso = ''         # [P0#1] 跨进程退避可读时间戳
llm_last_error = ''            # [V3] 最近一次失败原因（消费方可查）
_COOLDOWN_S = 3                  # 同一模型最小间隔秒数

def _pick_model(preferred: str) -> str:
    """选择可用模型：优先用preferred，冷却中则轮换fallback"""
    now = time.time()
    candidates = [preferred] + [m for m in FALLBACK_MODELS if m != preferred]
    for m in candidates:
        if now - _model_last_called.get(m, 0) >= _COOLDOWN_S:
            _model_last_called[m] = now
            return m
    # 全部冷却中：强行用preferred（等待最短）
    _model_last_called[preferred] = now
    return preferred

_ctx = ssl.create_default_context()
_ctx.check_hostname = True
_ctx.verify_mode = ssl.CERT_REQUIRED


# ── 主AI通道failover（2026-09-28 苏摩111）──────────────────────────────
# 免费池429/退避/全灭时自动切网关自带litellm主AI，免费池重置后自动切回。
# 免费优先（省成本），主AI只做兜底；key运行时从openclaw.json读取，不落日志不写盘。
_master_cache = {'ts': 0.0, 'cfg': None}
_MASTER_CACHE_TTL = 60.0


def _master_config() -> dict:
    """读取OpenClaw网关主AI配置（60s缓存），不可用返回{}"""
    now = time.time()
    if _master_cache['cfg'] is not None and now - _master_cache['ts'] < _MASTER_CACHE_TTL:
        return _master_cache['cfg']
    cfg = {}
    try:
        oc_f = Path(os.path.expanduser('~/.openclaw/openclaw.json'))
        if oc_f.exists():
            d = json.loads(oc_f.read_text())
            prov = ((d.get('models') or {}).get('providers') or {}).get('litellm') or {}
            base = (prov.get('baseUrl') or '').rstrip('/')
            key = prov.get('apiKey') or ''
            if base and key:
                cfg = {
                    'base': base,
                    'key': key,
                    'models': ['advanced', 'Qwen3.5-397B-A17B-SGLang'],
                }
    except Exception:
        cfg = {}
    _master_cache['ts'] = now
    _master_cache['cfg'] = cfg
    return cfg


def _master_chat(messages: list, max_tokens: int, timeout: int) -> str:
    """主AI通道调用。成功返回content，失败返回''。免费池退避期内也走此函数。
    [2026-09-30 苏摩111 挂死根修] 总预算硬顶deadline：
    事故=deep-post对6589字真prompt，Qwen3.5慢流生成>115s无响应且逐次recv重置socket timeout，
    defeat了原有90s超时→进程挂死>295s被cron timeout杀（exit 1，无traceback）。
    修复：函数级deadline=max(timeout,45)总预算硬顶；每模型socket timeout=min(剩余预算,45)；
    advanced(reasoning)对长prompt把token全花在思考上→空content快速降级到下一模型。
    """
    cfg = _master_config()
    if not cfg:
        return ''
    # reasoning主模型思考消耗预算：max_tokens过小→content为空，给足下限
    mt = max(max_tokens, 600)
    total_budget = max(timeout, 45)
    deadline = time.time() + total_budget
    last = ''
    for model in cfg['models']:
        remain = deadline - time.time()
        if remain < 10:
            last = f'master/{model}: deadline({total_budget}s) exceeded'
            break
        try:
            payload = json.dumps({
                'model': model, 'messages': messages,
                'max_tokens': mt, 'temperature': 0.2,
            }).encode()
            req = urllib.request.Request(
                cfg['base'] + '/v1/chat/completions', data=payload,
                headers={
                    'Authorization': f"Bearer {cfg['key']}",
                    'Content-Type': 'application/json',
                },
            )
            # [挂死根修 v3] 慢流trickle（每0.5s送1字节）会defeat两层防御：
            # ①socket timeout只防单次recv空闲（有数据就不超时）；
            # ②read(65536)内部循环不返回，deadline检查放在read后永远轮不到。
            # 正解：逐字节读+每字节查墙钟deadline，总耗时硬顶在total_budget内。
            # 快速响应代价可接受：≤100KB ≈ 十万次recv ≈ <1s开销。
            conn = urllib.request.urlopen(req, timeout=max(1, min(remain, 45)), context=_ctx)
            raw = b''
            while True:
                chunk = conn.read(1)
                if not chunk:
                    break
                raw += chunk
                if time.time() > deadline:
                    conn.close()
                    raise TimeoutError(f'read deadline {total_budget}s exceeded')
            resp = json.loads(raw)
            choice = (resp.get('choices') or [{}])[0]
            content = (choice.get('message') or {}).get('content', '').strip()
            if content:
                return content
            last = f'master/{model}: empty content'
        except urllib.error.HTTPError as e:
            last = f'master/{model}: HTTP {e.code}'
        except Exception as e:
            last = f'master/{model}: {type(e).__name__}: {str(e)[:60]}'
    return ''


def _mark_master_success(caller: str = '', transit: bool = False) -> None:
    """主AI接管成功后留证：保留免费池退避（等重置自愈），追加master字段。
    [P2盲区1修复 2026-09-28 苏摩111] 加caller审计+transit区分：
    - caller=调用方task标签（council/vip/oi/regime/wr_audit/review/chop/safety/default）
    - transit=True=退避期内转发（非真实新调用，计数膨胀根因）；False=免费池全灭后真接管
    事件写入data/master_failover_events.jsonl（追加+留证，供审计定位调用源）
    """
    try:
        state_f = Path(__file__).parent.parent / 'data' / 'llm_channel_state.json'
        st = {}
        if state_f.exists():
            try:
                st = json.loads(state_f.read_text())
            except Exception:
                st = {}
        st['master_failover_at'] = _dt.datetime.now(_dt.timezone.utc).isoformat()
        st['master_failover_count'] = int(st.get('master_failover_count') or 0) + 1
        state_f.write_text(json.dumps(st, ensure_ascii=False, indent=2))
        ev_f = Path(__file__).parent.parent / 'data' / 'master_failover_events.jsonl'
        ev = {'ts': time.time(), 'caller': caller or 'unknown',
              'kind': 'transit' if transit else 'real_failover',
              'count': st['master_failover_count']}
        with open(ev_f, 'a') as f:
            f.write(json.dumps(ev, ensure_ascii=False) + '\n')
    except Exception:
        pass


def _load_backoff_state(now: float = None) -> None:
    """[P0#1 跨进程退避 2026-09-28 苏摩111] 从state文件同步全池退避状态。
    接入位置: free_llm_client.chat()单点 — 11+消费方经此函数自动生效（单点修复=全线接线）。
    只在文件退避晚于内存值且仍在未来时采纳，防旧文件覆盖新退避。"""
    global _global_backoff_until, _backoff_until_iso
    try:
        state_f = Path(__file__).parent.parent / 'data' / 'llm_channel_state.json'
        if not state_f.exists():
            return
        st = json.loads(state_f.read_text())
        bu = float(st.get('backoff_until') or 0)
        if bu > _global_backoff_until and (now is None or now < bu):
            _global_backoff_until = bu
            _backoff_until_iso = st.get('backoff_until_iso', '') or _dt.datetime.fromtimestamp(bu, _dt.timezone.utc).isoformat()
    except Exception:
        pass


def _clear_backoff_state() -> None:
    """[P0#1] 成功调用后清除state文件退避，让其他进程立即恢复（自愈加速）。"""
    try:
        state_f = Path(__file__).parent.parent / 'data' / 'llm_channel_state.json'
        if state_f.exists():
            st = json.loads(state_f.read_text())
            if float(st.get('backoff_until') or 0) > 0:
                st['backoff_until'] = 0
                st['backoff_until_iso'] = ''
                st['cleared_at'] = _dt.datetime.now(_dt.timezone.utc).isoformat()
                state_f.write_text(json.dumps(st, ensure_ascii=False, indent=2))
    except Exception:
        pass


def chat(prompt: str, system: str = '', max_tokens: int = 200,
         timeout: int = 20, task: str = 'default') -> str:
    """
    调用OpenRouter免费模型，自动路由到最适合的专项模型。
    task: 任务类型 (council/vip/oi/regime/wr_audit/review/hcme/chop/safety/default)
    system: 额外system内容（深度封印版梵天宪法已自动注入）
    返回模型回复文本，失败时返回空字符串。
    """
    # 梵天宪法全局注入：合并外部system + 梵天宪法
    merged_system = BRAHMA_CONSTITUTION
    if system:
        merged_system = BRAHMA_CONSTITUTION + '\n\n' + system

    messages = [
        {'role': 'system', 'content': merged_system},
        {'role': 'user',   'content': prompt},
    ]

    # [主AI failover] 免费池key缺失 → 主AI直接接管
    if not API_KEY:
        return _master_chat(messages, max_tokens, timeout)

    preferred = TASK_MODEL_MAP.get(task, TASK_MODEL_MAP['default'])
    model = _pick_model(preferred)


    # [V3 2026-09-27 苏摩111] 429感知退避 + 失败留证 + finish=length拒收
    global _global_backoff_until, _backoff_until_iso, llm_last_error
    now = time.time()
    # [P0#1 跨进程退避 2026-09-28 苏摩111] 调用前读state文件：退避期内直接降级返回''，
    # 消费方走本地fallback（不烧配额不盲打）。接入位置=chat()单点，全线生效。
    _load_backoff_state(now)
    if time.time() < _global_backoff_until:
        # [主AI failover] 退避期内不盲打免费池 → 主AI接管（免费池重置后自动切回）
        mc = _master_chat(messages, max_tokens, timeout)
        if mc:
            # [P2盲区1修复] transit=True=退避转发（非真实failover），caller=task语义
            _mark_master_success(caller=task, transit=True)
            print('[llm] 免费池退避中，主AI接管成功', file=sys.stderr)
            return mc
        llm_last_error = f'backoff until {_backoff_until_iso} (cross-process state)'
        print(f'[llm] 跨进程退避生效至{_backoff_until_iso}，本地降级', file=sys.stderr)
        return ''
    last_err = ''
    for attempt_model in ([model] + [m for m in FALLBACK_MODELS if m != model]):
        if attempt_model not in _model_last_called:
            _model_last_called[attempt_model] = 0
        # 全局429退避：冷却未到直接跳过网络调用（省时间不烧配额）
        if now < _global_backoff_until:
            continue
        try:
            payload = json.dumps({
                'model':       attempt_model,
                'messages':    messages,
                'max_tokens':  max_tokens,
                'temperature': 0.2,
            }).encode()
            req = urllib.request.Request(
                BASE_URL, data=payload,
                headers={
                    'Authorization': f'Bearer {API_KEY}',
                    'Content-Type':  'application/json',
                    'HTTP-Referer':  'https://brahma-quant.ai',
                    'X-Title':       'BrahmaQuantAI',
                },
            )
            resp = json.loads(urllib.request.urlopen(req, timeout=timeout, context=_ctx).read())
            _model_last_called[attempt_model] = time.time()
            _global_backoff_until = 0
            _clear_backoff_state()
            choice = (resp.get('choices') or [{}])[0]
            content = (choice.get('message') or {}).get('content', '').strip()
            if content and choice.get('finish_reason') != 'length':
                return content
            if choice.get('finish_reason') == 'length':
                last_err = f'{attempt_model}: finish=length(思考链泄漏/截断)'; continue
        except urllib.error.HTTPError as e:
            body = ''
            try:
                body = e.read().decode()[:200]
            except Exception:
                pass
            _model_last_called[attempt_model] = time.time()
            if e.code == 429:
                # 上游配额耗尽：解析reset时间（epoch_ms），精确退避到重置点
                reset_ms = e.headers.get('x-ratelimit-reset') if e.headers else None
                if reset_ms:
                    try:
                        wait = max(60, int(int(reset_ms) / 1000 - time.time()) + 30)
                    except Exception:
                        wait = 600
                else:
                    wait = 600
                _global_backoff_until = time.time() + wait
                last_err = f'429 quota exhausted, backoff {wait}s'
                # 429=全池共享配额，同源限额，轮换无意义 → 立即放弃
                break
            else:
                last_err = f'{attempt_model}: HTTP {e.code} {body[:100]}'
        except Exception as e:
            _model_last_called[attempt_model] = time.time()
            last_err = f'{attempt_model}: {type(e).__name__}: {str(e)[:100]}'

    # [主AI failover] 免费池全灭（429/网络/fallback全失败）→ 主AI接管
    mc = _master_chat(messages, max_tokens, timeout)
    if mc:
        # [P2盲区1修复] 真实failover：免费池全灭后接管
        _mark_master_success(caller=task, transit=False)
        print(f'[llm] 免费池全灭({last_err[:60]})，主AI接管成功', file=sys.stderr)
        llm_last_error = f'{last_err} (master failover used)' if last_err else ''
        return mc

    # [9.27修复] 失败留证：不再静默空返回，写诊断文件供消费方/哨兵读取
    try:
        state_f = Path(__file__).parent.parent / 'data' / 'llm_channel_state.json'
        state_f.write_text(json.dumps({
            'last_failure_at': _dt.datetime.now(_dt.timezone.utc).isoformat(),
            'last_error': last_err,
            'backoff_until': _global_backoff_until,
            'backoff_until_iso': _dt.datetime.fromtimestamp(_global_backoff_until, _dt.timezone.utc).isoformat() if _global_backoff_until else '',
        }, ensure_ascii=False, indent=2))
    except Exception:
        pass
    llm_last_error = last_err
    print(f'[llm] all models failed: {last_err}', file=sys.stderr)
    return ''


def council_llm(
    regime: str,
    bias: str,
    fvg_dir: str,
    oi_signal: str,
    sm_signal: str,
    hurst: float,
    kappa: float,
    score: float,
    entry_lo: float,
    entry_hi: float,
    price: float,
    liq_up: float,
    liq_dn: float,
    sym: str = 'BTC',
) -> dict:
    """
    AI议会真实LLM裁决。
    返回与规则引擎相同的结构: {bias, reason, action, confidence}
    """
    if not API_KEY:
        return {}

    prompt = f"""你是梵天量化交易系统的AI裁判。根据以下实时信号，给出一句话裁决。

标的: {sym}/USDT
现价: ${price:,.0f}
体制: {regime} (score={score:.0f})
梵天bias: {bias}
FVG方向: {fvg_dir}（磁铁方向）
OI信号: {oi_signal}
聪明钱: {sm_signal}
Hurst: {hurst:.3f}（>0.65=趋势，<0.45=均值回归）
κ(kappa): {kappa:.3f}（负=期权偏多，正=期权偏空）
入场区: ${entry_lo:,.0f}~${entry_hi:,.0f}（{'现价上方' if entry_lo > price else '现价下方'}）
上方清算目标: ${liq_up:,.0f}
下方清算目标: ${liq_dn:,.0f}

请输出严格JSON格式（不要markdown代码块，直接输出JSON）：
{{"bias":"偏多或偏空或中性","reason":"核心逻辑一句话（20字内）","action":"ENTER或WAIT或AVOID","confidence":"HIGH或MED或LOW"}}"""

    raw = chat(prompt, max_tokens=120, timeout=15, task='council')
    if not raw:
        return {}

    # 解析JSON
    try:
        # 去掉可能的markdown包裹
        clean = raw.strip()
        if '```' in clean:
            clean = clean.split('```')[1] if '```json' not in clean else clean.split('```json')[1].split('```')[0]
        # 找第一个{...}
        start = clean.find('{')
        end   = clean.rfind('}') + 1
        if start >= 0 and end > start:
            d = json.loads(clean[start:end])
            # 验证字段
            if all(k in d for k in ('bias', 'reason', 'action', 'confidence')):
                return d
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    return {}


if __name__ == '__main__':
    print('测试OpenRouter连通性...')
    if not API_KEY:
        print('❌ 未找到OPENROUTER_API_KEY，请检查.env文件')
    else:
        # 连通性测试
        resp = chat('BTC现在CHOP_MID体制，大户65%多，FVG向下，一句话：做多还是做空？', max_tokens=60)
        print(f'✅ 连通: {resp[:100]}' if resp else '❌ 所有模型均失败')

        # AI议会测试
        result = council_llm(
            regime='CHOP_MID', bias='SHORT', fvg_dir='BEAR',
            oi_signal='MIXED', sm_signal='STRONG_BULL',
            hurst=0.683, kappa=-0.110, score=60,
            entry_lo=80958, entry_hi=81927, price=80716,
            liq_up=82825, liq_dn=79577, sym='BTC',
        )
        print(f'AI议会: {json.dumps(result, ensure_ascii=False)}')


def vip_entry_reason(
    sym: str, price: float, regime: str,
    fvg_dir: str, fvg_magnet: float,
    oi_signal: str, sm_signal: str,
    hurst: float, kappa: float,
    entry_lo: float, entry_hi: float,
    bias: str, liq_up: float, liq_dn: float,
) -> str:
    """
    A: VIP入场理由一句话（LLM生成）
    返回: 20字内的精准入场逻辑句，失败返回空字符串
    """
    direction = '做多' if bias == 'LONG' else '做空'
    entry_side = '下方回调' if entry_lo < price else '上方反弹'
    prompt = (
        f"梵天系统 {sym}/USDT ${price:,.0f} {regime}体制\n"
        f"方向:{direction} 入场区:${entry_lo:,.0f}~${entry_hi:,.0f}({entry_side})\n"
        f"FVG:{fvg_dir}方向 磁铁${fvg_magnet:,.0f} OI:{oi_signal} 聪明钱:{sm_signal}\n"
        f"Hurst:{hurst:.2f} kappa:{kappa:.3f} 上方清算:${liq_up:,.0f} 下方清算:${liq_dn:,.0f}\n"
        f"必须用中文回答。输出一句话入场逻辑（15字内，直接说结构原因，禁止用英文）："
    )
    result = chat(prompt, max_tokens=40, timeout=12, task='vip')
    if not result:
        return ''
    # 取第一句，截断
    first = result.split('\n')[0].strip().rstrip('。').strip()
    return first[:25]


def signal_conflict_resolve(
    sym: str, price: float, regime: str,
    oi_signal: str, oi_desc: str,
    sm_signal: str, big_long: float, retail_long: float,
    fvg_dir: str,
) -> str:
    """
    B: 信号矛盾自动LLM裁决
    当OI与大户方向矛盾时，LLM分析哪方更可信
    返回: 一句话裁决，失败返回空字符串
    """
    prompt = (
        f"{sym} ${price:,.0f} {regime}体制 信号矛盾分析：\n"
        f"OI信号:{oi_signal}（{oi_desc[:30]}）\n"
        f"大户多仓:{big_long:.0f}% 散户多仓:{retail_long:.0f}% 分歧:{abs(big_long-retail_long):.0f}%\n"
        f"FVG方向:{fvg_dir}\n"
        f"OI和大户方向矛盾，请裁决：哪方信号更可信，倾向做多还是做空？\n"
        f"输出格式（JSON）: {{\"winner\":\"OI或大户\",\"bias\":\"做多或做空\",\"reason\":\"原因一句话15字内\"}}"
    )
    raw = chat(prompt, max_tokens=80, timeout=12, task='council')
    if not raw:
        return ''
    try:
        import json as _j
        start = raw.find('{'); end = raw.rfind('}') + 1
        if start >= 0 and end > start:
            d = _j.loads(raw[start:end])
            winner = d.get('winner', '?')
            bias   = d.get('bias', '?')
            reason = d.get('reason', '')
            return f"{winner}信号更可信 → {bias}（{reason}）"
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    # fallback: 直接返回原始文本首句
    return raw.split('\n')[0].strip()[:50]


def council_three_way(
    sym: str, price: float, regime: str, score: float,
    fvg_dir: str, fvg_magnet: float,
    oi_signal: str, sm_signal: str, big_long: float,
    hurst: float, kappa: float, harv: float,
    entry_lo: float, entry_hi: float,
    liq_up: float, liq_dn: float,
    macro_bias: str, fear_greed: int,
) -> dict:
    """
    C: AI议会三方独立LLM投票
    宏观裁判 / 结构裁判 / 量化裁判 各自独立调用
    投票结果 → 综合置信度 HIGH/MED/LOW
    """
    import concurrent.futures as _cf

    base = f"{sym}/USDT ${price:,.0f} {regime}体制(score={score:.0f})"

    prompts = {
        '宏观': (
            f"{base}\n"
            f"宏观信号: 恐贪={fear_greed} 宏观偏向={macro_bias} FVG磁铁={fvg_dir}(${fvg_magnet:,.0f})\n"
            f"作为宏观裁判，仅从体制+宏观角度裁决，必须用中文。\n"
            f"JSON: {{\"vote\":\"做多或做空或中性\",\"reason\":\"10字内\",\"conf\":\"HIGH或MED或LOW\"}}"
        ),
        '结构': (
            f"{base}\n"
            f"结构信号: OI={oi_signal} 大户多{big_long:.0f}% FVG={fvg_dir} 上方清算=${liq_up:,.0f} 下方=${liq_dn:,.0f}\n"
            f"入场区: ${entry_lo:,.0f}~${entry_hi:,.0f}({'上方' if entry_lo > price else '下方'})\n"
            f"作为SMC结构裁判，仅从入场区+清算+OI角度裁决，必须用中文。\n"
            f"JSON: {{\"vote\":\"做多或做空或中性\",\"reason\":\"10字内\",\"conf\":\"HIGH或MED或LOW\"}}"
        ),
        '量化': (
            f"{base}\n"
            f"量化信号: Hurst={hurst:.3f} kappa={kappa:.3f} HAR-RV={harv:.4f} 聪明钱={sm_signal}\n"
            f"作为量化裁判，仅从Hurst趋势+期权偏向+波动率角度裁决，必须用中文。\n"
            f"JSON: {{\"vote\":\"做多或做空或中性\",\"reason\":\"10字内\",\"conf\":\"HIGH或MED或LOW\"}}"
        ),
    }

    results = {}
    # 三方各用专项模型：宏观用regime，结构用council，量化用council
    _role_tasks = {'宏观': 'regime', '结构': 'council', '量化': 'council'}
    def _call(role, prompt):
        raw = chat(prompt, max_tokens=60, timeout=15, task=_role_tasks.get(role, 'council'))
        if not raw:
            return role, {}  
        try:
            import json as _j
            s = raw.find('{'); e = raw.rfind('}') + 1
            if s >= 0 and e > s:
                d = _j.loads(raw[s:e])
                if 'vote' in d:
                    return role, d
        except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
        return role, {}

    # 三方并行调用
    with _cf.ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_call, role, prompt): role for role, prompt in prompts.items()}
        for fut in _cf.as_completed(futures, timeout=20):
            try:
                role, d = fut.result()
                if d:
                    results[role] = d
            except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    if not results:
        return {}

    # 投票统计
    votes = [d.get('vote', '中性') for d in results.values()]
    long_v  = sum(1 for v in votes if '多' in v)
    short_v = sum(1 for v in votes if '空' in v)
    total   = len(votes)

    if long_v > short_v:
        final_bias = '偏多'
        final_action = 'ENTER' if long_v == total else 'WAIT'
    elif short_v > long_v:
        final_bias = '偏空'
        final_action = 'ENTER' if short_v == total else 'WAIT'
    else:
        final_bias = '中性'
        final_action = 'WAIT'

    # 置信度：全票=HIGH，2:1=MED，全中性=LOW
    if long_v == total or short_v == total:
        conf = 'HIGH'
    elif long_v > 0 or short_v > 0:
        conf = 'MED'
    else:
        conf = 'LOW'

    # 汇总理由
    reasons = [f"{role}:{d.get('reason','')}" for role, d in results.items() if d.get('reason')]
    reason_str = ' | '.join(reasons[:3])

    return {
        'bias':       final_bias,
        'reason':     reason_str[:60],
        'action':     final_action,
        'confidence': conf,
        'votes':      {role: d.get('vote', '?') for role, d in results.items()},
        'source':     'LLM3',
    }
