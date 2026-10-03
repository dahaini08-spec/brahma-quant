#!/usr/bin/env python3
"""
square_template.py — 姓赵不宣广场统一品牌模板引擎 v1.0
[设计院封印 2026-09-11 苏摩111]

替代旧版LLM重写（rewrite_as_trader），改纯Python模板填充：
  - 0秒延迟（vs LLM 45秒超时）
  - 0风格漂移（vs LLM每次不同语气）
  - 0内部术语泄漏（模板不产生禁用词）
  - 100%品牌统一（每帖🌿前缀+📊后缀）

5套模板：
  1. 旗舰帖 — 战场报告（80维全输出）
  2. 深度分析帖 — "X美元的Y"四段式+方向表态
  3. 信号帖 — 异动捕捉（20维SMC+OI+FR+清算）
  4. 教育帖 — 实盘案例教学（1个SMC概念+当日实盘）
  5. 宏观帖 — 事件前瞻（宏观+体制+影响标的）
  6. 热度帖 — 争议钩子+数据锤+互动引爆
  7. 交易闭环帖 — 入场/持仓/平仓三段式

v2.0变更 2026-09-14 苏摩111：
  - 新增模板B/D/E
  - 所有模板加互动钩子
  - 审计函数放行互动引导
  - 标题签名化统一品牌识别
"""

from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
UTC = timezone.utc

# ═══════════════════════════════════════════════════════════════
# 统一品牌锚点
# ═══════════════════════════════════════════════════════════════

BRAND_PREFIX = ''  # 顶端不出现IP，放在后缀
BRAND_SUFFIX = '🔗 www.bsmkweb.cc/register?ref=XZBX666\n🌿 姓赵不宣 | 不是建议'
LIVE_CTA = '关注我，每晚21:00直播+SMC教学'  # [2026-09-30 苏摩111] 发帖清单#2：签名前必须带CTA，全库原缺失
REFERRAL_CTA = '🔗 开通币安：www.bsmkweb.cc/register?ref=XZBX666'  # [2026-10-03 苏摩111] 苏摩那件码，全库自动植入
AI_WATERMARK = '🤖 内容含AI生成分析，非实时人工观点'  # [L5水印 2026-09-28 苏摩111] ai_truth_audit L4

# 互动钩子库
INTERACTION_HOOKS = {
    'battlefield': '你觉得这周破支撑还是冲止损墙？评论投票',
    'deep_analysis': '你选A还是B？评论区聊',
    'signal': '这个异动你会跟吗？评论说你的判断',
    'education': '你犯过这个错误吗？评论告诉我',
    'macro': 'CPI前你减仓还是硬扛？评论投票',
    'heat': '你站哪边？评论区投票',
    'trade_open': '这笔你跟还是观望？评论说理由',
    'trade_close': '这笔该止盈还是继续拿？评论投票',
}

# ═══════════════════════════════════════════════════════════════
# 1. 旗舰帖模板 — 战场报告
# ═══════════════════════════════════════════════════════════════

def build_battlefield_report(sym, analysis_data):
    """
    旗舰帖：战场报告，观点先行+数据支撑
    核心改变：不罗列数据，先给判断，再用数据解释为什么
    """
    date_str = datetime.now(CST).strftime('%m/%d')

    regime = analysis_data.get('regime', 'CHOP_MID')
    score = analysis_data.get('score', 0)
    fvg_dir = analysis_data.get('fvg_dir', 'NEUTRAL')
    fvg_magnet = analysis_data.get('fvg_magnet', 0)
    fvg_pct = analysis_data.get('fvg_pct', 0)
    liq_wall = analysis_data.get('liq_wall', 0)
    liq_pool = analysis_data.get('liq_pool', 0)
    liq_wall_pct = analysis_data.get('liq_wall_pct', 0)
    liq_pool_pct = analysis_data.get('liq_pool_pct', 0)
    oi_signal = analysis_data.get('oi_signal', 'NO_DATA')
    cvd_1h = analysis_data.get('cvd_1h', 0)
    big_long = analysis_data.get('big_long', 50)
    retail_long = analysis_data.get('retail_long', 50)
    hurst = analysis_data.get('hurst', 0.5)
    kappa = analysis_data.get('kappa', 0)
    fr = analysis_data.get('fr', 0)
    atr_1h = analysis_data.get('atr_1h', 0)
    atr_4h = analysis_data.get('atr_4h', 0)
    price = analysis_data.get('price', 0)
    failure_state = analysis_data.get('failure_state', 'GREEN')
    entry_lo = analysis_data.get('entry_lo', 0)
    entry_hi = analysis_data.get('entry_hi', 0)
    sl = analysis_data.get('sl', 0)
    tp1 = analysis_data.get('tp1', 0)
    tp2 = analysis_data.get('tp2', 0)
    bias = analysis_data.get('bias', 'NONE')
    vip_status = analysis_data.get('vip_status', 'WAIT')

    regime_cn = _translate_regime(regime)

    # ── 核心观点生成（观点先行）──
    view_point = _generate_viewpoint(
        sym, regime, score, fvg_dir, fvg_magnet, price,
        liq_wall, liq_pool, oi_signal, cvd_1h,
        big_long, retail_long, hurst, kappa, bias, vip_status
    )

    # ── 操作建议（可执行）──
    action_line = _generate_action(
        fvg_dir, fvg_magnet, price, liq_wall, liq_pool,
        oi_signal, entry_lo, entry_hi, sl, tp1, bias, vip_status
    )

    # ── 矛盾/风险点 ──
    contradiction = _generate_contradiction(
        fvg_dir, oi_signal, big_long, retail_long, hurst, kappa
    )

    # 失效期
    failure_desc = ''
    if failure_state == 'RED':
        failure_desc = '\n⚠️ 失效期RED → 仓位减半'
    elif failure_state == 'YELLOW':
        failure_desc = '\n⚠️ 失效期YELLOW → 仓位×0.75'

    lines = [
        f'早间战场报告 {date_str}',
        f'',
        f'{view_point}',
        f'',
        f'操作建议：',
        f'{action_line}',
        f'',
    ]
    if contradiction:
        lines.append(f'风险提示：{contradiction}')
        lines.append(f'')
    if failure_state == 'RED':
        lines.append('当前市场处于失效期，信号不可靠，仓位减半。')
        lines.append(f'')
    lines.extend([
        AI_WATERMARK,
        f'{BRAND_SUFFIX}',
        f'#{sym} #合约交易',
        f'',
        f'{INTERACTION_HOOKS["battlefield"]}',
    ])
    return '\n'.join(lines)


def build_battlefield_report_combined(analysis_by_sym: dict) -> str:
    """
    BTC/ETH合并战场报告（单帖）：主题钩子→两币判断→两币操作→风险→互动
    [2026-09-27 苏摩111 P0-2] 接入位置：square_auto_post.py run()（日发1:30/9:30两轮心cron）
    复盘实锤：早间战场报告周发14帇0爆款，数据开头无钩子；合并后频率减半，
    主题钩子开头（事件反直觉公式），两币并重对比引导读者关注差异。
    数据缺失时任何币种降级为观察句，不酷刑、不崩溃（守卫：audit len>=100）。
    """
    data_btc = analysis_by_sym.get('BTC', {})
    data_eth = analysis_by_sym.get('ETH', {})
    date_str = datetime.now(CST).strftime('%m/%d')

    def _num(sym, field, default=0.0):
        v = analysis_by_sym.get(sym, {}).get(field, default)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def _short(sym, field, fmt='{:,.0f}', default=0.0):
        v = _num(sym, field, default)
        if v == default:
            return ''
        return fmt.format(v)

    btc_wall = _short('BTC', 'liq_wall')
    btc_pool = _short('BTC', 'liq_pool')
    eth_wall = _short('ETH', 'liq_wall')
    eth_pool = _short('ETH', 'liq_pool')

    bias_map = {'BTC': data_btc.get('bias', 'NONE'), 'ETH': data_eth.get('bias', 'NONE')}
    n_bear = sum(1 for b in bias_map.values() if b == 'SHORT')
    n_bull = sum(1 for b in bias_map.values() if b == 'LONG')

    # ── 主题钩子：事件反直觉公式（数据支撑）——三选一，缩小重复风险 ──
    if n_bear >= 1 and n_bull >= 1:
        # 分歧反共振：一多一空
        if bias_map['BTC'] == 'SHORT':
            hook = (f'BTC系统判空，ETH反而看多——同一个盘面，两个币种却走向两个方向。'
                    f'这不是矛盾，是主力在换仓位。')
        else:
            hook = (f'ETH系统判空，BTC反而看多——同一个盘面，两个币种却走向两个方向。'
                    f'这不是矛盾，是主力在换仓位。')
    elif n_bear == 2:
        hook = (f'BTC和ETH系统判空，但大户持仓偶然偏多。'
                f'聊天室里吼抄底的人不少——聊天室从来不是仓位，只是情绪。')
    elif n_bull == 2:
        hook = (f'BTC和ETH系统同时看多，但散户多空比已经挤向同一边。'
                f'方向一致的时候我反而要问：谁在对面开单？')
    else:
        beijing_hour2 = datetime.now(CST).hour
        period2 = '早盘' if beijing_hour2 < 11 else ('午间' if beijing_hour2 < 13 else ('午后' if beijing_hour2 < 18 else '晚间'))
        hook = (f'{period2}最值得注意的不是方向，是BTC和ETH的多空分歧在变大。'
                f'分歧不可怕，怕的是一边倒带另一边被动。')

    # ── 两币并重判断（每币1~2句，数据缺失降级观察句） ──
    segments = []
    for sym in ('BTC', 'ETH'):
        d = analysis_by_sym.get(sym, {})
        b = d.get('bias', 'NONE')
        wall = d.get('liq_wall')
        pool = d.get('liq_pool')
        wall_s = _short(sym, 'liq_wall')
        pool_s = _short(sym, 'liq_pool')
        if b == 'SHORT':
            parts = []
            if wall_s:
                parts.append(f'上方止损墙${wall_s}是反弹阻力')
            if pool_s:
                parts.append(f'下方支撑池${pool_s}是目标')
            if parts:
                seg = f'{sym}：系统判空。' + '，'.join(parts) + '。'
            else:
                seg = f'{sym}：系统判空。等反弹到阻力位再评估空。'
        elif b == 'LONG':
            parts = []
            if pool_s:
                parts.append(f'下方支撑池${pool_s}是回踩买点')
            if wall_s:
                parts.append(f'上方止损墙${wall_s}是目标')
            if parts:
                seg = f'{sym}：系统看多。' + '，'.join(parts) + '。'
            else:
                seg = f'{sym}：系统看多。等回调到支撑位再评估多。'
        else:
            seg = f'{sym}：今天不给方向，两侧触发再动手。'
            if wall_s and pool_s:
                seg += f'破${wall_s}看多一步，破${pool_s}看空一步。'
        segments.append(seg)

    # ── 操作建议：逐币调单行（ENTER才出挂单区，WAIT出等待触发条件） ──
    action_lines = []
    for sym in ('BTC', 'ETH'):
        d = analysis_by_sym.get(sym, {})
        vip = d.get('vip_status', 'WAIT')
        b = d.get('bias', 'NONE')
        lo = d.get('entry_lo')
        hi = d.get('entry_hi')
        sl = d.get('sl')
        tp1 = d.get('tp1')
        def _fmt(v, spec=',.0f'):
            try:
                return format(float(v), spec)
            except (TypeError, ValueError):
                return ''
        if vip == 'ENTER' and lo and hi and sl:
            emoji = '🔴' if b == 'SHORT' else '🟢'
            side = '空单' if b == 'SHORT' else '多单'
            line = f'{emoji} {sym}{side}挂单区${_fmt(lo)}~${_fmt(hi)}'
            line += f'｜止损${_fmt(sl)}'
            if tp1:
                line += f'｜目标${_fmt(tp1)}'
            action_lines.append(line)
        else:
            wall_s2 = _short(sym, 'liq_wall')
            pool_s2 = _short(sym, 'liq_pool')
            line = f'{sym}：暂无单，等结构触发。'
            if wall_s2 and pool_s2:
                line += f'反弹到${wall_s2}+收阴看空确认；回踩${pool_s2}+收阳看多确认。'
            action_lines.append(line)

    # ── 共性推理层：双币同向→跨市场推理，反向→轮动信号 ──
    # [2026-09-28 苏摩111] 接入位置：build_battlefield_report_combined（快照帖升级，差距分析修复B线）
    # 数据条件→叙事映射：同向=杠杆情绪一致放大下破/上破概率；反向=板块轮动
    cross_inference = ''
    if n_bear == 2:
        cross_inference = ('两个盘子同判空——BTC和ETH的杠杆情绪是一致的，'
                          '全市场都在防下破。这种一致性本身就是信号：'
                          '当所有人都看空时，下破的阻力反而变小，但假跌破的概率也变大。'
                          '我倾向把它当警报而不是入场券：等反弹到止损墙收阴再动手，不提前抢跑。')
    elif n_bull == 2:
        cross_inference = ('两个盘子同看多——杠杆情绪一致偏多，全市场都在押上破。'
                          '一致看多意味着对面空头的燃料在减少，逼空行情可能加速，'
                          '但追高位置最容易被收割。等回踩支撑池收阳再跟，不追第一波。')
    elif n_bear == 1 and n_bull == 1:
        cross_inference = ('BTC和ETH方向分裂——这不是噪音，是板块轮动的第一步。'
                          '大资金在两个盘子间腾挪仓位，通常弱币先跌、强币后跌（或反之）。'
                          '这种时候只做方向明确的那一边，另一边看戏。')
    else:
        cross_inference = ('BTC和ETH今天都没给方向——两个盘子同时沉默时，'
                          '最大的风险不是亏钱，是手痒。区间里硬做，盈亏比天然吃亏，'
                          '不如等两边任一边走出来再跟。')

    # ── 组装 ──
    # [2026-09-30 苏摩111] 标题时段跟北京时间走：<6点凌晨 / 6-11早盘 / 11-13午间 / 13-18午后 / 18-24晚间
    # （cron 01:30/09:30 UTC = 北京09:30/17:30，过去都叫"早盘"是时区错位）
    beijing_hour = datetime.now(CST).hour
    if beijing_hour < 6:
        period = '凌晨'
    elif beijing_hour < 11:
        period = '早盘'
    elif beijing_hour < 13:
        period = '午间'
    elif beijing_hour < 18:
        period = '午后'
    else:
        period = '晚间'
    lines = [f'{period}战场报告 {date_str}｜BTC+ETH双币联读', '']
    lines.append(hook)
    lines.append('')
    lines.append('先看两个盘子：')
    for seg in segments:
        lines.append(seg)
    lines.append('')
    lines.append(cross_inference)
    lines.append('')
    lines.append('操作上：')
    for a in action_lines:
        lines.append(a)
    lines.append('')

    # 失效期（取两币更差）
    failure_states = [d.get('failure_state', 'GREEN') for d in (data_btc, data_eth)]
    if 'RED' in failure_states:
        lines.append('当前市场处于失效期，信号不可靠，仓位减半。')
        lines.append('')
    elif 'YELLOW' in failure_states:
        lines.append('失效期YELLOW，仓位打折。')
        lines.append('')

    lines.extend([
        AI_WATERMARK,
        LIVE_CTA,
        f'{BRAND_SUFFIX}',
        f'#BTC #ETH #合约交易',
        f'',
        f'{INTERACTION_HOOKS["battlefield"]}',
    ])
    return '\n'.join(lines)


def _generate_viewpoint(sym, regime, score, fvg_dir, fvg_magnet, price,
                       liq_wall, liq_pool, oi_signal, cvd_1h,
                       big_long, retail_long, hurst, kappa, bias, vip_status):
    """生成核心观点：用交易员语言解读数据，不是罗列数据
    [2026-09-17 设计院修复] 方向跟随交易员大脑bias，不跟随FVG单维度"""
    # 交易员大脑方向优先 = 系统真实方向
    tb_dir = bias if bias and bias != 'NONE' else None

    # === 交易员大脑=SHORT ===
    if tb_dir == 'SHORT':
        _wall_pct = (liq_wall - price) / price * 100 if liq_wall and price else 0
        if oi_signal == 'SHORT_BUILD':
            return (f'{sym}在${price:,.0f}，空头在加码。'
                    f'上方止损墙${liq_wall:,.0f}({_wall_pct:+.1f}%)是反弹阻力，'
                    f'反弹到那附近空单集中，容易被猎杀。'
                    f'剧本很清楚：反弹到止损墙受阻→回落，不追多。')
        else:
            return (f'{sym}在${price:,.0f}，系统判空。'
                    f'上方止损墙${liq_wall:,.0f}是阻力，下方支撑池${liq_pool:,.0f}是目标。'
                    f'中间位置不追多，等反弹到阻力位再评估做空。')

    # === 交易员大脑=LONG ===
    if tb_dir == 'LONG':
        if oi_signal == 'SHORT_BUILD':
            return (f'{sym}在${price:,.0f}，空头在堆仓位，但系统看多。'
                    f'下方支撑池${liq_pool:,.0f}有多头止损堆积，到了可能被猎杀后反弹。'
                    f'等支撑池企稳+1H收阳再评估试多。')
        else:
            return (f'{sym}在${price:,.0f}，系统看多。'
                    f'下方支撑池${liq_pool:,.0f}是回踩买点，上方止损墙${liq_wall:,.0f}是目标。'
                    f'回调到支撑池附近+1H收阳可以试多。')

    # === 交易员大脑=NONE/WATCH → 双向观察 ===
    if vip_status == 'WAIT':
        return (f'{sym}在${price:,.0f}，系统判断为观察。'
                f'上方止损墙${liq_wall:,.0f}，下方支撑池${liq_pool:,.0f}。'
                f'没有明确方向，等走出来再跟——不赌方向。')

    # FVG辅助描述（不决定方向）
    if fvg_dir == 'BULL' and fvg_magnet > price:
        gap_pct = (fvg_magnet - price) / price * 100
        return (f'{sym}在${price:,.0f}，FVG磁铁在上方${fvg_magnet:,.0f}（{gap_pct:+.1f}%）。'
                f'等价格到磁铁位或止损墙/支撑池附近再看结构确认，中间不动。')
    elif fvg_dir == 'BEAR' and fvg_magnet < price:
        gap_pct = (price - fvg_magnet) / price * 100
        return (f'{sym}在${price:,.0f}，FVG磁铁在下方${fvg_magnet:,.0f}（{gap_pct:+.1f}%）。'
                f'等价格到磁铁位或支撑池附近再看结构确认，中间不动。')
    else:
        return (f'{sym}在${price:,.0f}，上方止损墙${liq_wall:,.0f}，下方支撑池${liq_pool:,.0f}。'
                f'等方向走出来再跟，中间不动。')


def _generate_action(fvg_dir, fvg_magnet, price, liq_wall, liq_pool,
                     oi_signal, entry_lo, entry_hi, sl, tp1, bias, vip_status):
    """生成可操作的建议
    [2026-09-17 设计院修复] 方向跟随交易员大脑bias，不跟随FVG单维度"""
    tb_dir = bias if bias and bias != 'NONE' else None

    if vip_status == 'ENTER' and entry_lo > 0:
        if tb_dir == 'SHORT' or bias == 'SHORT':
            return f'反弹到${entry_lo:,.0f}-${entry_hi:,.0f}+1H收阴 → 做空 | 止损${sl:,.0f} | 目标${tp1:,.0f}'
        elif tb_dir == 'LONG' or bias == 'LONG':
            return f'回调到${entry_lo:,.0f}-${entry_hi:,.0f}+1H收阳 → 做多 | 止损${sl:,.0f} | 目标${tp1:,.0f}'

    # WAIT状态：跟随交易员大脑方向给监控条件
    if tb_dir == 'SHORT':
        return f'系统判空。等价格反弹到止损墙${liq_wall:,.0f}+1H收阴=做空确认；跌破支撑池${liq_pool:,.0f}+放量=追空确认。中间不追多。'
    elif tb_dir == 'LONG':
        return f'系统判多。等价格回调到支撑池${liq_pool:,.0f}+1H收阳=做多确认；突破止损墙${liq_wall:,.0f}+放量=追多确认。中间不追空。'
    else:
        # 交易员大脑=NONE → 双向观察
        return f'等方向选择。破止损墙${liq_wall:,.0f}+放量=追多确认；破支撑池${liq_pool:,.0f}+放量=追空确认。横盘就不动。'


def _generate_contradiction(fvg_dir, oi_signal, big_long, retail_long, hurst, kappa):
    """生成矛盾/风险点：用人话解释数据矛盾"""
    contradictions = []
    if fvg_dir == 'BULL' and oi_signal in ('SHORT_BUILD', 'LONG_UNWIND'):
        contradictions.append('结构看多但资金在做空/撤资——磁铁拉上方，但有人在高位挂空单对冲')
    if fvg_dir == 'BEAR' and big_long > 58:
        contradictions.append(f'结构看空但大户{big_long:.0f}%偏多——主力可能知道什么，别太激进追空')
    if hurst < 0.45:
        contradictions.append('Hurst低于0.5，当前是随机游走，趋势信号随时可能翻转')
    if kappa < -0.1 and fvg_dir == 'BEAR':
        contradictions.append('期权市场在买Call（看多保险），但结构看空——大资金在押反弹')
    if kappa > 0.1 and fvg_dir == 'BULL':
        contradictions.append('期权市场在买Put（看空保险），但结构看多——大资金在防跌')
    if not contradictions:
        return ''
    return '\n'.join(contradictions)


def _fvg_direction(fvg_dir, fvg_magnet, price):
    """FVG磁铁方向简述"""
    if not fvg_magnet or not fvg_dir or fvg_dir == 'NEUTRAL':
        return '无方向'
    pct = (fvg_magnet - price) / price * 100 if price > 0 else 0
    if pct > 0.5:
        return f'拉上方{pct:+.1f}%'
    elif pct < -0.5:
        return f'拉下方{pct:+.1f}%'
    return '已在当前价'


# ═══════════════════════════════════════════════════════════════
# 2. 信号帖模板 — 异动捕捉（个案深度拆解版）

# [2026-09-27 苏摩111 P0-1] 「涨了N%」疲劳模板停用
# 复盘实锤：7天13连发0爆款（425-1823浏览），固定句式被算法判重复内容降权
# 改为单币个案深度拆解：事件钩子（轮换）→ 机制拆解 → 交易员判断 → 盯盘清单
# 接入位置：square_extreme_alert.py build_signal_post（唯一调用方，签名不变）
_EXTREME_HOOKS = (
    lambda c: f'${c["sym"]} 单日振幅{c["range_pct"]:.0f}%，成交额只有{c["vol_wan"]:.0f}万U。这个涨幅的含金量，值得算清楚。',
    lambda c: f'一天{c["chg"]:+.0f}%，${c["sym"]}这根K线你敢接吗？先别急着回答。',
    lambda c: f'${c["sym"]} {c["chg"]:+.0f}%的盘面里，有个细节大多数人不看。',
    lambda c: f'又是单日{c["chg"]:+.0f}%这种级别的波动。${c["sym"]}这波，和之前见过的剧本哪里像，哪里不像？',
    lambda c: f'${c["sym"]} 今天{c["chg"]:+.0f}%。追之前，先回答一个问题：这笔钱是谁赚走的？',
)

_EXTREME_CLOSINGS = (
    '这种单边暴动的行情，见过太多次了。结论从来不变：不追。',
    '行情越热闹，越要回到基本盘。流动性、费率、结构，三样看完再说。',
    '暴动之后必有清算。我的做法从来是等它落下来，再看谁在接。',
)


def _pick_hook(sym: str, pool) -> int:
    doy = datetime.now(CST).timetuple().tm_yday
    h = sum(ord(ch) for ch in sym)
    return (doy + h) % len(pool)
# ═══════════════════════════════════════════════════════════════

def build_signal_alert(sym, chg, price, high, low, vol, fr, ls,
                       fvg_dir='', fvg_magnet=0, fvg_mid=0,
                       ob_test='', oi_signal='', cvd=0,
                       liq_above=0, liq_below=0,
                       entry_cond='', sl_price=0, tp_price=0,
                       monitor_fr='', monitor_oi=''):
    """
    信号帖 v2.0：姓赵不宣KOL叙事风格，非数据堆砌
    [设计院封印 2026-09-15 苏摩111]
    改造点：去━━━分隔符 → 自然段落 | 数据→交易员语言 | 明确方向≠等回踩确认
    """
    now_str = datetime.now(CST).strftime('%m/%d %H:%M')

    # ── 构建叙事段落 ──
    lines = []

    # [P0-1 2026-09-27] 个案钩子（轮换库），替代「大多数人看到+X%心动」疲劳句式
    pullback = (high - price) / high * 100 if high > 0 else 0
    rebound = (price - low) / low * 100 if low > 0 else 0
    range_pct = (high - low) / low * 100 if low > 0 else 0
    _ctx = {'sym': sym, 'chg': chg, 'range_pct': range_pct,
            'vol_wan': vol / 1e6 if vol else 0}
    lines.append(_EXTREME_HOOKS[_pick_hook(sym, _EXTREME_HOOKS)](_ctx))
    lines.append('')

    # 第二段：事件机制拆解（数据背书，随涨跌变体）
    if chg > 0:
        lines.append(f'今天最低{low:.4f}拉到{high:.4f}，振幅{range_pct:.0f}%，成交额只有{vol/1e6:.0f}万U。池子浅，少量资金就能打出这个涨幅，出的时候未必有人接。')
    else:
        lines.append(f'今天最高{high:.4f}砸到{low:.4f}，振幅{range_pct:.0f}%，成交额{vol/1e6:.0f}万U。杀得凶不代表有人接货，先看谁在接。')
    lines.append('')

    # 第三段：结构读（自然段落，非清单）
    structure_parts = []
    if fvg_dir and fvg_magnet:
        if fvg_dir == 'BEAR':
            structure_parts.append(f'Bear FVG中点{fvg_mid:.4f}在上方，磁铁向下拉')
        elif fvg_dir == 'BULL':
            structure_parts.append(f'Bull FVG中点{fvg_mid:.4f}在下方，磁铁向上拉')
    if ob_test:
        structure_parts.append(ob_test)
    if oi_signal:
        structure_parts.append(f'OI {_oi_short(oi_signal)}')
    if liq_above and liq_below:
        structure_parts.append(f'上方空头止损{liq_above:.4f}，下方多头止损{liq_below:.4f}')

    if structure_parts:
        lines.append('结构上：' + '，'.join(structure_parts) + '。')
        lines.append('')

    # FR解读（交易员语言）
    if fr > 0.01:
        lines.append(f'FR {fr:.4f}%，多头每8小时在付费。这笔钱不是白付的，是市场在定价「这个方向拥挤了」。')
    elif fr < -0.01:
        lines.append(f'FR {fr:.4f}%，空头在付费。轧空的火药桶，一点火星就炸。')
    else:
        lines.append(f'FR {fr:.4f}%，多空均衡，没有拥挤。')
    lines.append('')

    # 第四段：我的判断（明确方向，非"等回踩确认"）
    if chg > 0:
        if fr < -0.1:
            lines.append(f'我的判断：不追。已从高点回落{pullback:.0f}%，轧空信号确认但肉已吃完了。')
        else:
            lines.append(f'我的判断：不追。+{chg:.0f}%追进去，你在给前面的人接盘。')
    else:
        if fr < -0.05:
            lines.append(f'我的判断：不抄底。跌了{abs(chg):.0f}%不代表便宜，FR告诉我们空头在加码。')
        else:
            lines.append(f'我的判断：观望。跌{abs(chg):.0f}%后反弹{rebound:.0f}%，但没到我的入场区。')

    # 入场条件（如果有）
    if entry_cond and sl_price and tp_price:
        lines.append(f'如果真要参与：{entry_cond}，止损{sl_price:.4f}，目标{tp_price:.4f}。但说实话，这种小标的的波动，不值得用大仓位去赌。')
    elif entry_cond:
        lines.append(f'如果真要参与：{entry_cond}。但说实话，这种波动不值得用大仓位去赌。')

    lines.append('')
    lines.append(_EXTREME_CLOSINGS[_pick_hook(sym, _EXTREME_CLOSINGS)])
    lines.append('')

    # 监控信号（如果有，自然段落）
    monitor_parts = []
    if monitor_fr:
        monitor_parts.append(monitor_fr)
    if monitor_oi:
        monitor_parts.append(monitor_oi)
    if monitor_parts:
        lines.append('要盯的话：' + '；'.join(monitor_parts) + '。')
        lines.append('')

    # 品牌后缀
    lines.append(AI_WATERMARK)
    lines.append(f'{BRAND_SUFFIX}')
    lines.append(f'#{sym} #合约交易')
    lines.append('')
    lines.append(f'{INTERACTION_HOOKS["signal"]}')
    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# 3. 教育帖模板 — 实盘案例教学
# ═══════════════════════════════════════════════════════════════

def build_education_post(edu_id, concept, definition_lines, live_example, how_to_use, historical_case=''):
    """
    教育帖：实盘案例教学
    edu_id: 编号 #001
    concept: 'Bear FVG填补'
    definition_lines: ['价格急跌时留下的空白区间...', 'Bear FVG = 下跌中形成，回填时是阻力']
    live_example: 'ETH 4H Bear FVG $2,439-$2,478\n当前$2,458 = FVG中点\n磁铁向下，目标$2,403支撑池'
    how_to_use: 'Bear FVG中点+1H收阴 → 可空\n止损放FVG上沿$2,478之上'
    historical_case: '上周BTC也走了同样的FVG填补：8/28 BULL FVG $77,200-$78,400，9/2回填到$78,193后回落3.2%'
    """
    lines = [
        f'实盘教学 #{edu_id:03d}',
        f'',
        f'今日{live_example.split(chr(10))[0]}',
        f'',
        f'什么是{concept}？',
    ]
    for d in definition_lines:
        lines.append(d)
    lines.append(f'')
    lines.append(f'实盘：{live_example}')
    lines.append(f'')
    lines.append(f'怎么用？')
    lines.append(how_to_use)
    if historical_case:
        lines.append(f'')
        lines.append(historical_case)
    lines.append(f'')
    lines.append(AI_WATERMARK)
    lines.append(f'{BRAND_SUFFIX}')
    lines.append(f'')
    lines.append(f'{INTERACTION_HOOKS["education"]}')
    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# 4. 宏观帖模板 — 事件前瞻
# ═══════════════════════════════════════════════════════════════

def build_macro_outlook(event_name, event_time, expectations,
                         hot_script, cool_script,
                         btc_regime, btc_score, eth_hurst, oi_trend,
                         action_advice, hashtag='#CPIWatch'):
    """
    宏观帖：事件前瞻
    """
    lines = [
        f'宏观前瞻',
        f'',
        f'明日{event_name} | {event_time}',
        f'',
        f'市场预期：',
    ]
    for e in expectations:
        lines.append(e)
    lines.append(f'')
    lines.append(f'两种剧本：')
    lines.append(f'Hot（偏热）：{hot_script}')
    lines.append(f'Cool（偏冷）：{cool_script}')
    lines.append(f'')
    lines.append(f'盘面读数：')
    lines.append(f'BTC体制{_translate_regime(btc_regime)} score={btc_score} → 宏观决定方向')
    lines.append(f'ETH Hurst={eth_hurst:.2f} → 波动率即将放大')
    lines.append(f'OI={oi_trend} → 资金在撤资=事件前避险')
    lines.append(f'')
    lines.append(f'操作建议：{action_advice}')
    lines.append(f'')
    lines.append(AI_WATERMARK)
    lines.append(LIVE_CTA)
    lines.append(f'{BRAND_SUFFIX}')
    lines.append(f'{hashtag} #BTC')
    lines.append(f'')
    lines.append(f'{INTERACTION_HOOKS["macro"]}')
    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# 5. 深度分析帖模板 — "X美元的Y"四段式+方向表态
# ═══════════════════════════════════════════════════════════════

def build_deep_analysis(sym, price, surface_text, event1_data, event2_data, event3_data,
                        scenario_a, scenario_b, my_choice, risk_note=''):
    """
    深度分析帖：小羊四段式+姓赵不宣方向表态
    sym: 'BTC' / 'ETH'
    price: 77069
    surface_text: 'BTC在$77,069，横盘第3天，成交量萎缩'
    event1_data: '灰度GBTC昨日净流入$1.2亿，连续5日净流入'
    event2_data: 'CryptoQuant：大户地址净增持+12,400 BTC，散户减持-3,800'
    event3_data: '4H图：RSI=42，价格在EMA200下方，FVG $76,200-$77,800未填补'
    scenario_a: '震荡：$75,500-$78,500区间，高拋低吸'
    scenario_b: '利空：跌破$75,500支撑池→追空，目标$72,000'
    my_choice: 'A' or 'B'
    risk_note: 'Hurst=0.43<0.5，随机游走，信号可能翻转'
    """
    price_str = f'${price:,.0f}' if price > 100 else f'${price:.4f}'
    lines = [
        f'{price_str}的{sym}，你敢动手吗？',
        f'',
        f'先看表面：{surface_text}',
        f'',
        f'第一件事：',
        event1_data,
        f'',
        f'第二件事：',
        event2_data,
        f'',
        f'第三件事：',
        event3_data,
        f'',
        f'操作策略：',
        f'情景A：{scenario_a}',
        f'情景B：{scenario_b}',
        f'',
        f'我选{my_choice}。',
    ]
    if risk_note:
        lines.append(f'⚠️ {risk_note}')
    lines.append(f'')
    lines.append(AI_WATERMARK)
    lines.append(f'{BRAND_SUFFIX}')
    lines.append(f'#{sym}')
    lines.append(f'')
    lines.append(f'{INTERACTION_HOOKS["deep_analysis"]}')
    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# 6. 热度帖模板 — 争议钩子+数据锤+互动引爆
# ═══════════════════════════════════════════════════════════════

def build_heat_post(title, hook_text, data_hammer, controversy, heat_type='controversy'):
    """
    热度帖：小波总式情绪化但升级为数据驱动
    title: '48K粉丝博主喊单翻车率65%，数据比情绪更冷'
    hook_text: '有人喊“大概率亏不了”，结果亏了14%。'
    data_hammer: '采样40条帖子，喊多翻车率61%，喊空翻车率75%'
    controversy: '你被广场博主喊单坑过吗？'
    heat_type: 'controversy' / 'education' / 'takedown'
    """
    lines = [
        f'🔥 {title}',
        f'',
        hook_text,
        f'',
        f'数据锤：{data_hammer}',
        f'',
    ]
    if heat_type == 'education':
        prefix = '📚'
        lines[0] = f'📚 {title}'
    lines.append(controversy)
    lines.append(f'')
    lines.append(AI_WATERMARK)
    lines.append(f'{BRAND_SUFFIX}')
    lines.append(f'')
    lines.append(f'{INTERACTION_HOOKS["heat"]}')
    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# 7. 交易闭环帖模板 — 入场/持仓/平仓
# ═══════════════════════════════════════════════════════════════

def build_trade_open(sym, direction, entry_price, sl_price, tp_price, leverage, position_pct,
                     logic_line=''):
    """交易闭环-入场帖"""
    emoji = '🔴' if direction == 'SHORT' else '🟢'
    dir_cn = '空单' if direction == 'SHORT' else '多单'
    lines = [
        f'{emoji} {sym} {dir_cn} 入场',
        f'',
        f'入场 ${entry_price:,.0f}' if entry_price > 100 else f'入场 ${entry_price:.4f}',
        f'止损 ${sl_price:,.0f}' if sl_price > 100 else f'止损 ${sl_price:.4f}',
        f'目标 ${tp_price:,.0f}' if tp_price > 100 else f'目标 ${tp_price:.4f}',
        f'杠杆 {leverage}x | 仓位 {position_pct}%NAV',
    ]
    if logic_line:
        lines.append(f'')
        lines.append(f'逻辑：{logic_line}')
    lines.append(f'')
    lines.append(AI_WATERMARK)
    lines.append(f'{BRAND_SUFFIX}')
    lines.append(f'#{sym}')
    lines.append(f'')
    lines.append(f'{INTERACTION_HOOKS["trade_open"]}')
    return '\n'.join(lines)


def build_trade_hold(sym, direction, entry_price, current_price, pnl_pct, adjust_note=''):
    """交易闭环-持仓帖"""
    emoji = '🔴' if direction == 'SHORT' else '🟢'
    dir_cn = '空单' if direction == 'SHORT' else '多单'
    lines = [
        f'{emoji} {sym} {dir_cn} 持仓中',
        f'',
        f'入场 ${entry_price:,.0f}' if entry_price > 100 else f'入场 ${entry_price:.4f}',
        f'现价 ${current_price:,.0f}' if current_price > 100 else f'现价 ${current_price:.4f}',
        f'浮盈 {pnl_pct:+.0f}%',
    ]
    if adjust_note:
        lines.append(f'')
        lines.append(f'调整：{adjust_note}')
    lines.append(f'')
    lines.append(AI_WATERMARK)
    lines.append(f'{BRAND_SUFFIX}')
    return '\n'.join(lines)


def build_trade_close(sym, direction, entry_price, exit_price, pnl_pct, review_line=''):
    """交易闭环-平仓帖"""
    emoji = '✅' if pnl_pct > 0 else '❌'
    dir_cn = '空单' if direction == 'SHORT' else '多单'
    lines = [
        f'{emoji} {sym} {dir_cn} 平仓',
        f'',
        f'入场 ${entry_price:,.0f}' if entry_price > 100 else f'入场 ${entry_price:.4f}',
        f'平仓 ${exit_price:,.0f}' if exit_price > 100 else f'平仓 ${exit_price:.4f}',
        f'盈亏 {pnl_pct:+.0f}%',
    ]
    if review_line:
        lines.append(f'')
        lines.append(f'复盘：{review_line}')
    lines.append(f'')
    lines.append(AI_WATERMARK)
    lines.append(f'{BRAND_SUFFIX}')
    lines.append(f'#{sym}')
    lines.append(f'')
    lines.append(f'{INTERACTION_HOOKS["trade_close"]}')
    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# 辅助翻译函数（人话输出，不露内部代码）
# ═══════════════════════════════════════════════════════════════

def _translate_regime(regime):
    """体制代码翻译成人话"""
    mapping = {
        'BULL_TREND': '牛市趋势',
        'BEAR_TREND': '熊市趋势',
        'CHOP_MID': '震荡中段',
        'CHOP_HIGH': '震荡高位',
        'CHOP_LOW': '震荡低位',
        'BEAR_EARLY': '熊市初期',
        'BEAR_RECOVERY': '熊市恢复',
        'BULL_EARLY': '牛市初期',
    }
    return mapping.get(regime, regime)


def _fvg_description(fvg_dir, fvg_magnet, price):
    """FVG磁铁描述"""
    if not fvg_magnet or not fvg_dir or fvg_dir == 'NEUTRAL':
        return '无明确FVG方向'
    pct = (fvg_magnet - price) / price * 100 if price > 0 else 0
    direction = '拉上方' if pct > 0 else '已在当前价' if abs(pct) < 0.5 else '拉下方'
    return f'{fvg_dir}@${fvg_magnet:,.0f}（{direction}{pct:+.1f}%）'


def _oi_description(oi_signal):
    """OI趋势描述"""
    mapping = {
        'SHORT_BUILD': '空头增仓（SHORT_BUILD）',
        'LONG_BUILD': '多头增仓（LONG_BUILD）',
        'SHORT_SQUEEZE': '空头被挤（SHORT_SQUEEZE）',
        'LONG_UNWIND': '多头减仓离场（LONG_UNWIND）',
        'NO_DATA': '数据不足',
    }
    return mapping.get(oi_signal, oi_signal)


def _oi_short(oi_signal):
    """OI简短描述"""
    mapping = {
        'SHORT_BUILD': '空头建仓',
        'LONG_BUILD': '多头建仓',
        'SHORT_SQUEEZE': '空头被挤',
        'LONG_UNWIND': '多头离场',
    }
    return mapping.get(oi_signal, oi_signal)


def _smart_money_description(big_long, retail_long):
    """聪明钱描述"""
    diff = big_long - retail_long
    if diff > 10:
        return f'大户{big_long:.0f}%多（偏多，散户{retail_long:.0f}%）'
    elif diff < -10:
        return f'大户{big_long:.0f}%多（偏空，散户{retail_long:.0f}%）'
    else:
        return f'大户{big_long:.0f}%多（中性，散户{retail_long:.0f}%）'


def _kappa_description(kappa):
    """kappa描述"""
    if kappa < -0.05:
        return 'Call需求强（期权市场偏多）'
    elif kappa > 0.05:
        return 'Put需求强（期权市场偏空）'
    else:
        return '期权中性'


def _hurst_desc(hurst):
    """Hurst描述"""
    if hurst > 0.6:
        return '强趋势持续'
    elif hurst > 0.5:
        return '弱趋势'
    else:
        return '随机游走'


def _fr_meaning(fr):
    """FR含义"""
    if fr > 0.01:
        return '多头过热，每8h付费'
    elif fr < -0.01:
        return '空头付费，轧空信号'
    else:
        return '正常'


def _build_trigger(bias, entry_lo, entry_hi, sl, tp1, tp2, vip_status):
    """触发条件描述"""
    if vip_status != 'ENTER':
        return '等待结构确认，暂无入场条件'
    if bias == 'SHORT':
        return (f'价格到${entry_lo:,.0f}-${entry_hi:,.0f}+1H收阴 → 可空 '
                f'| SL${sl:,.0f} | TP${tp1:,.0f}→${tp2:,.0f}')
    elif bias == 'LONG':
        return (f'价格到${entry_lo:,.0f}-${entry_hi:,.0f}+1H收阳 → 可多 '
                f'| SL${sl:,.0f} | TP${tp1:,.0f}→${tp2:,.0f}')
    else:
        return '无方向，等待'


# ═══════════════════════════════════════════════════════════════
# 帖子审计函数
# ═══════════════════════════════════════════════════════════════

def audit_post(content):
    """审计帖子是否符合品牌铁律，返回(ok, issues)
    [2026-09-30 苏摩111 统一审计入口接线] 接入位置：
    ①square_auto_post.py run()（快照帖cron唯一发帖面）
    ②square_deep_post.py audit_deep_post()（旗舰帖批准链路）
    ③square_trade_loop.py _pre_post_validate()（单帖面）
    ④square_macro_poster.py（宏观帖，如有产出）
    本体=铁律v1 + 内嵌四道门（zhao_bu_xuan_ip同源断言，防零调用孤岛）：
    Gate1 IP泄漏 / Gate2 结构规范 / Gate4 IP一致性 / AI水印与签名
    """
    issues = []

    # 铁律1: 必须有品牌前缀+后缀
    if '姓赵不宣' not in content:
        issues.append('缺少姓赵不宣签名')
    if '不是建议' not in content:
        issues.append('缺少「不是建议」后缀')

    # 铁律10: 必须>100字
    char_count = len(content)
    if char_count < 100:
        issues.append(f'字数{char_count}<100，不达Square流量boost门槛')

    # ── Gate1 内部术语泄漏（zhao_bu_xuan_ip PERSONA.forbidden 同源）──
    forbidden = ['BEAR_TREND', 'CHOP_MID', 'BULL_TREND', 'BEAR_EARLY',
                'BEAR_RECOVERY', 'CHOP_LOW', 'CHOP_HIGH', 'brahma', 'brahma_',
                '梵天', '设计院', '方仓', 'HCME', 'confluence_score', 'Kronos']
    for w in forbidden:
        if w in content:
            issues.append(f'内部术语泄漏：{w}')

    # ── Gate4 IP一致性（与zhao_bu_xuan_ip.gate4_ip_consistency同源断言）──
    # AI/系统类禁词
    for w in ['AI分析', 'AI模型', '多模型', '系统给出', '引擎输出',
              '35个维度', '量化体制']:
        if w in content:
            issues.append(f'IP违禁：含「{w}」')
    # 态度类禁词
    for w in ['大家好', '分享一下', '盯了很久', '保证盈利', '稳赚']:
        if w in content:
            issues.append(f'IP违禁：含「{w}」')
    # 格式类：全角感叹号（广场违规）
    if '！' in content:
        issues.append('IP违禁：含全角感叹号「！」')
    # AI水印必须存在（ai_truth_audit L4水印铁律）
    if '内容含AI生成分析' not in content:
        issues.append('缺少AI水印（ai_truth_audit L4）')

    return len(issues) == 0, issues


# ═══════════════════════════════════════════════════════════════
# 从brahma_manual_analysis输出解析关键字段
# ═══════════════════════════════════════════════════════════════

def parse_analysis_output(report_text):
    """从run_analysis()返回的文本中解析出模板需要的字段
    [2026-09-30 苏摩111 价格串币bug根修] 必须传【单币段】（_extract_sym_segment切出），
    本函数正则按优先级：严格格式（上方空头止损墙$X）优先，旧冒号格式仅fallback——
    因为段内跨币共振区会用冒号格式引用【其他币】的价位，先匹配会串币。
    """
    import re

    data = {}
    lines = report_text.split('\n')

    # 代价/体制：新格式段首【BTCUSDT $83,631 | 震荡市场】+ 体制=XXX行
    m = re.search(r'体制=(\w+)', report_text)
    if m:
        data['regime'] = m.group(1)
    m2 = re.search(r'score=([-\d.]+)', report_text)
    if m2:
        data['score'] = float(m2.group(1))

    for line in lines:
        # FVG磁铁（新格式：FVG共识=BULL，磁铁向上吸引价格到$2,682；旧格式：主磁铁: BULL@$X）
        m = re.search(r'FVG共识=(\w+)', line)
        if m:
            data['fvg_dir'] = m.group(1)
        m = re.search(r'主磁铁:\s*(\w+)@\$?([\d,.]+)', line)
        if m:
            data['fvg_dir'] = m.group(1)
            data['fvg_magnet'] = float(m.group(2).replace(',', ''))
        if 'fvg_magnet' not in data:
            m = re.search(r'磁铁[^，。]{0,8}到\$([\d,.]+)', line)
            if m:
                data['fvg_magnet'] = float(m.group(1).replace(',', ''))

        # FVG百分比
        m = re.search(r'中点\$?[\d,.]+\(([+-]?[\d.]+)%\)', line)
        if m:
            data['fvg_pct'] = float(m.group(1))

        # 清算（[2026-09-30] 严格格式优先：上方空头止损墙$85,304 / 下方多头支撑池$81,959；
        # 旧冒号格式仅fallback——段内跨币共振区的冒号行是【其他币】引用，先匹配会串币）
        m = re.search(r'上方空头止损墙\$?([\d,.]+)', line)
        if m and 'liq_wall' not in data:
            data['liq_wall'] = float(m.group(1).replace(',', ''))
        m = re.search(r'下方多头支撑池\$?([\d,.]+)', line)
        if m and 'liq_pool' not in data:
            data['liq_pool'] = float(m.group(1).replace(',', ''))
        # 旧格式fallback（仅当严格格式缺失）
        if 'liq_wall' not in data:
            m = re.search(r'止损墙:\s*\$?([\d,.]+)\s*\(([+-]?[\d.]+)%', line)
            if m:
                data['liq_wall'] = float(m.group(1).replace(',', ''))
                data['liq_wall_pct'] = float(m.group(2))
        if 'liq_pool' not in data:
            m = re.search(r'支撑池:\s*\$?([\d,.]+)\s*\(([+-]?[\d.]+)%', line)
            if m:
                data['liq_pool'] = float(m.group(1).replace(',', ''))
                data['liq_pool_pct'] = float(m.group(2))

        # OI（新格式：OI多头在撤退→SHORT_BUILD类；旧格式：主信号: XXX）
        m = re.search(r'主信号:\s*(\w+)', line)
        if m:
            data['oi_signal'] = m.group(1)
        if 'oi_signal' not in data:
            if 'OI多头在撤退' in line:
                data['oi_signal'] = 'SHORT_BUILD'
            elif 'OI空头在撤退' in line:
                data['oi_signal'] = 'LONG_BUILD'

        # CVD
        m = re.search(r'CVD\s+1H=([-\d]+)', line)
        if m:
            data['cvd_1h'] = int(m.group(1))

        # 大户
        m = re.search(r'大户(\d+)%多', line)
        if m:
            data['big_long'] = float(m.group(1))
        m = re.search(r'散户(\d+)%多', line)
        if m:
            data['retail_long'] = float(m.group(1))

        # Hurst
        m = re.search(r'H=([\d.]+)', line)
        if m:
            data['hurst'] = float(m.group(1))

        # kappa
        m = re.search(r'κ=([-\d.]+)', line)
        if m:
            data['kappa'] = float(m.group(1))

        # FR
        m = re.search(r'FR=([\d.]+)%', line)
        if m:
            data['fr'] = float(m.group(1))

        # ATR
        m = re.search(r'ATR1H=\$([\d,.]+)', line)
        if m:
            data['atr_1h'] = float(m.group(1).replace(',', ''))
        m = re.search(r'ATR4H=\$([\d,.]+)', line)
        if m:
            data['atr_4h'] = float(m.group(1).replace(',', ''))

        # 价格（新格式段首【BTCUSDT $83,631 | ...】优先，基准/现价fallback）
        m = re.search(r'【[A-Z]+USDT\s*\$([\d,.]+)', line)
        if m and 'price' not in data:
            data['price'] = float(m.group(1).replace(',', ''))
        if 'price' not in data:
            m = re.search(r'(?:基准|现价)\$?([\d,.]+)', line)
            if m:
                data['price'] = float(m.group(1).replace(',', ''))

        # 失效期
        m = re.search(r'失效期:\s*(\w+)', line)
        if m:
            data['failure_state'] = m.group(1)

        # 共振入场区
        m = re.search(r'入场区间:\s*\$?([\d,.]+)\s*~\s*\$?([\d,.]+)', line)
        if m:
            data['entry_lo'] = float(m.group(1).replace(',', ''))
            data['entry_hi'] = float(m.group(2).replace(',', ''))

        # VIP状态（新格式：⏳ WATCH——条件不足，等待。/ 🚀 ENTER...；旧格式：交易员大脑行）
        if 'WATCH' in line:
            data['vip_status'] = 'WAIT'
        if 'ENTER' in line and '交易员大脑' in line:
            data['vip_status'] = 'ENTER'
        if '🚀 ENTER' in line or '🚀ENTER' in line:
            data['vip_status'] = 'ENTER'

    # 从VIP卡片提取SL/TP（止损 $2,799.0｜目标 $2,650→...）
    m = re.search(r'止损\s*\$?([\d,.]+)', report_text)
    if m:
        data['sl'] = float(m.group(1).replace(',', ''))
    m = re.search(r'目标\s*\$?([\d,.]+)', report_text)
    if m:
        data['tp1'] = float(m.group(1).replace(',', ''))

    # VIP卡挂单区（新格式：🔴 空单｜挂单区 $2,735.9~$2,744.1）
    m = re.search(r'🔴\s*空单｜挂单区\s*\$?([\d,.]+)~\$?([\d,.]+)', report_text)
    if m:
        data['short_entry_lo'] = float(m.group(1).replace(',', ''))
        data['short_entry_hi'] = float(m.group(2).replace(',', ''))
        data['bias'] = 'SHORT'
    m = re.search(r'🟢\s*多单｜挂单区\s*\$?([\d,.]+)~\$?([\d,.]+)', report_text)
    if m:
        data['long_entry_lo'] = float(m.group(1).replace(',', ''))
        data['long_entry_hi'] = float(m.group(2).replace(',', ''))
        if 'bias' not in data:
            data['bias'] = 'LONG'
    if 'bias' not in data:
        if '方向=SHORT' in report_text:
            data['bias'] = 'SHORT'
        elif '方向=LONG' in report_text:
            data['bias'] = 'LONG'
        else:
            data['bias'] = 'NONE'

    return data


if __name__ == '__main__':
    # 测试模板
    print('=== 旗舰帖测试 ===')
    test_data = {
        'regime': 'CHOP_MID', 'score': 7, 'price': 77069,
        'fvg_dir': 'BULL', 'fvg_magnet': 78193, 'fvg_pct': 1.8,
        'liq_wall': 78458, 'liq_pool': 75382, 'liq_wall_pct': 2.0, 'liq_pool_pct': -2.2,
        'oi_signal': 'SHORT_BUILD', 'cvd_1h': -2,
        'big_long': 68, 'retail_long': 62,
        'hurst': 0.431, 'kappa': -0.099, 'fr': 0.003,
        'atr_1h': 304, 'atr_4h': 716,
        'failure_state': 'RED',
        'vip_status': 'WAIT', 'bias': 'NONE',
        'entry_lo': 0, 'entry_hi': 0, 'sl': 0, 'tp1': 0, 'tp2': 0,
    }
    post = build_battlefield_report('BTC', test_data)
    print(post)
    print()
    ok, issues = audit_post(post)
    print(f'审计: {"✅通过" if ok else "❌"+str(issues)}')
    print()
    print('=== 宏观帖测试 ===')
    macro = build_macro_outlook(
        'CPI公布', '09/12 20:30 CST',
        ['市场预期CPI同比3.4% / 核心CPI 2.5%', '当前加息概率70% / 恐贪指数68=Greed'],
        'BTC跌破$75,382支撑池 → 清算连环 → 测试$72,000',
        'BTC突破$78,458止损墙 → 轧空 → 测试$80,000',
        'CHOP_MID', 7, 0.426, 'LONG_UNWIND',
        'CPI前减仓50% | SL加宽1.5倍 | 不在数据公布瞬间挂单\nCPI后等1H收线确认方向再入场'
    )
    print(macro)
    print()
    ok, issues = audit_post(macro)
    print(f'审计: {"✅通过" if ok else "❌"+str(issues)}')
