#!/usr/bin/env python3
"""
brahma_knowledge_graph.py — 梵天系统知识图谱生成器
设计院自主 · 2026-08-02

输入: MEMORY.md + git commit log
输出: brahma_knowledge_graph.html (交互式知识图谱)

三元组格式: (主语) --[谓语]--> (宾语)
节点类型: 体制/模块/BUG/修复/宪法/信号/工具
"""
import sys, os, json, re, textwrap
from pathlib import Path

BASE = Path(__file__).parent.parent
MEMORY_PATH = BASE.parent / 'MEMORY.md'
OUTPUT_PATH = BASE / 'data' / 'brahma_knowledge_graph.html'

# ── 1. 读取源文本 ───────────────────────────────────────────────
def load_source() -> str:
    parts = []
    
    # MEMORY.md 主体
    if MEMORY_PATH.exists():
        parts.append(MEMORY_PATH.read_text(encoding='utf-8'))
    
    # 近期git commits
    import subprocess
    r = subprocess.run(
        ['git', 'log', '--oneline', '-50'],
        capture_output=True, text=True, cwd=str(BASE)
    )
    if r.returncode == 0:
        parts.append('\n\n## Git Commits (最近50条)\n' + r.stdout)
    
    return '\n\n'.join(parts)


# ── 2. LLM提取三元组 ────────────────────────────────────────────
def extract_triples_with_llm(text_chunk: str, chunk_idx: int) -> list[dict]:
    """调用本地LLM提取SPO三元组"""
    try:
        import openai
        client = openai.OpenAI(
            base_url='http://localhost:11434/v1',
            api_key='ollama'
        )
        prompt = f"""从以下梵天量化交易系统的技术文档中，提取知识三元组。

要求：
- 格式：JSON数组，每项包含 subject/predicate/object 三个字段
- subject/object：系统组件、体制名、BUG编号、宪法规则、技术模块、指标名
- predicate：关系动词（如"修复了"、"封禁"、"依赖"、"证明"、"根因是"、"触发"、"包含"、"优先级高于"）
- 只提取有意义的技术关系，忽略日期、人名
- 每个chunk提取10-20个三元组，宁少勿滥
- 直接返回JSON数组，不要其他文字

文本：
{text_chunk[:2000]}

返回格式示例：
[
  {{"subject": "BEAR_TREND体制", "predicate": "封禁", "object": "LONG方向信号"}},
  {{"subject": "signal_dir参数", "predicate": "根因是", "object": "SHORT信号缺失BUG"}}
]"""

        resp = client.chat.completions.create(
            model='qwen2.5:7b',
            messages=[{'role':'user','content':prompt}],
            temperature=0.1,
            max_tokens=1000,
        )
        raw = resp.choices[0].message.content.strip()
        # 提取JSON
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        if m:
            triples = json.loads(m.group())
            print(f'  chunk{chunk_idx}: 提取{len(triples)}个三元组')
            return triples
    except Exception as e:
        print(f'  chunk{chunk_idx} LLM失败({e}), 用规则提取')
    
    return extract_triples_rules(text_chunk)


def extract_triples_rules(text: str) -> list[dict]:
    """规则兜底：从MEMORY.md的格式化内容提取三元组"""
    triples = []
    
    # 体制封禁规则
    bans = re.findall(r'(BEAR_TREND_LONG|CHOP_LONG|BULL_TREND_SHORT)', text)
    for b in set(bans):
        triples.append({'subject': b, 'predicate': '列入死穴', 'object': '永久封禁列表'})
    
    # BUG修复
    bugs = re.findall(r'(P\d+-\d+|BUG-\d+)[：:](.*?)(?:\n|$)', text)
    for bug_id, desc in bugs[:5]:
        desc = desc.strip()[:40]
        if desc:
            triples.append({'subject': bug_id, 'predicate': '描述', 'object': desc})
    
    # commit封印
    commits = re.findall(r'commit[:：]\s*([a-f0-9]{7,})', text)
    for c in commits[:5]:
        triples.append({'subject': c, 'predicate': '类型', 'object': 'git封印提交'})
    
    # 体制→策略
    regime_map = [
        ('BEAR_TREND', '空为主', 'BTC/ETH'),
        ('BULL_TREND', '多为主', 'BTC/ETH'),
        ('CHOP_MID', '不发策略', 'score<110'),
    ]
    for reg, strat, scope in regime_map:
        if reg in text:
            triples.append({'subject': reg, 'predicate': '策略是', 'object': strat})
    
    # 核心模块依赖
    modules = [
        ('brahma_engine', '调用', 'dharma_data_bridge'),
        ('dharma_data_bridge', '写入', 'live_signal_log'),
        ('auto_executor', '读取', 'live_signal_log'),
        ('brahma_analysis_runner', '包装', 'brahma_engine'),
        ('rsi_structure_watcher', '触发', 'brahma_scan_all'),
        ('signal_settler', '计算', 'WR胜率'),
        ('Kronos模型', '输入到', 's23维度'),
    ]
    for s, p, o in modules:
        if s.lower() in text.lower() or o.lower() in text.lower():
            triples.append({'subject': s, 'predicate': p, 'object': o})
    
    return triples


# ── 3. 实体标准化 ───────────────────────────────────────────────
ENTITY_ALIASES = {
    'brahma_engine.py': 'brahma_engine',
    'brahma_analysis_runner.py': 'brahma_analysis_runner',
    'brahma_analyze.py': 'brahma_analyze',
    'dharma_data_bridge.py': 'dharma_data_bridge',
    'auto_executor.py': 'auto_executor',
    'signal_settler.py': 'signal_settler',
    'live_signal_log.jsonl': 'live_signal_log',
    'BEAR TREND': 'BEAR_TREND体制',
    'BULL TREND': 'BULL_TREND体制',
    'BEAR_TREND': 'BEAR_TREND体制',
    'BULL_TREND': 'BULL_TREND体制',
    'CHOP_MID': 'CHOP_MID体制',
    'BEAR_RECOVERY': 'BEAR_RECOVERY体制',
    'signal_dir': 'signal_dir参数',
    'signal_direction': 'signal_dir参数',
    'WR': 'WR胜率',
    'win_rate': 'WR胜率',
}

def standardize(entity: str) -> str:
    entity = entity.strip()
    return ENTITY_ALIASES.get(entity, entity)


# ── 4. 节点分类（用于着色）──────────────────────────────────────
def classify_node(name: str) -> str:
    name_l = name.lower()
    if any(x in name_l for x in ['bear','bull','chop','recovery','early']):
        return 'regime'
    if any(x in name_l for x in ['bug','p0','p1','error','fail','缺失','断路']):
        return 'bug'
    if any(x in name_l for x in ['修复','fix','封印','seal','恢复']):
        return 'fix'
    if any(x in name_l for x in ['宪法','规则','封禁','死穴','禁止']):
        return 'rule'
    if any(x in name_l for x in ['engine','runner','bridge','settler','watcher','executor','hub','bus']):
        return 'module'
    if any(x in name_l for x in ['score','wr','grade','nav','signal','信号','分数','胜率']):
        return 'metric'
    if any(x in name_l for x in ['kronos','lgbm','torch','venv','model']):
        return 'ml'
    return 'other'


# ── 5. 生成HTML知识图谱 ─────────────────────────────────────────
def generate_html(triples: list[dict]) -> str:
    # 去重
    seen = set()
    unique = []
    for t in triples:
        key = (t.get('subject',''), t.get('predicate',''), t.get('object',''))
        if key not in seen and all(key):
            seen.add(key)
            unique.append(t)
    
    # 标准化
    for t in unique:
        t['subject'] = standardize(t['subject'])
        t['object']  = standardize(t['object'])
    
    # 收集节点
    nodes = {}
    for t in unique:
        for role in ('subject','object'):
            name = t[role]
            if name not in nodes:
                nodes[name] = {
                    'id': len(nodes),
                    'label': name,
                    'type': classify_node(name),
                    'degree': 0
                }
            nodes[name]['degree'] += 1
    
    # 颜色映射
    color_map = {
        'regime':  '#e74c3c',   # 红 — 体制
        'bug':     '#e67e22',   # 橙 — BUG
        'fix':     '#2ecc71',   # 绿 — 修复
        'rule':    '#9b59b6',   # 紫 — 宪法规则
        'module':  '#3498db',   # 蓝 — 模块
        'metric':  '#f1c40f',   # 黄 — 指标
        'ml':      '#1abc9c',   # 青 — ML模型
        'other':   '#95a5a6',   # 灰
    }
    
    nodes_js = []
    for name, n in nodes.items():
        color = color_map.get(n['type'], '#95a5a6')
        size = max(10, min(40, n['degree'] * 5))
        label = name if len(name) <= 20 else name[:18]+'…'
        nodes_js.append(f'{{"id":{n["id"]},"label":"{label}","title":"{name}","color":"{color}","size":{size}}}')
    
    edges_js = []
    for i, t in enumerate(unique):
        s_id = nodes[t['subject']]['id']
        o_id = nodes[t['object']]['id']
        pred = t['predicate'][:15]
        edges_js.append(f'{{"id":{i},"from":{s_id},"to":{o_id},"label":"{pred}","arrows":"to"}}')
    
    stats = f"{len(nodes)}个节点 · {len(unique)}条关系"
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>🏛️ 梵天系统知识图谱</title>
<script src="https://unpkg.com/vis-network@9.1.9/dist/vis-network.min.js"></script>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#0d1117; color:#c9d1d9; font-family:'SF Pro Display',-apple-system,monospace; }}
#header {{ padding:16px 24px; background:#161b22; border-bottom:1px solid #30363d; display:flex; justify-content:space-between; align-items:center; }}
#header h1 {{ font-size:18px; color:#f0f6fc; }}
#header .stats {{ font-size:13px; color:#8b949e; }}
#legend {{ padding:10px 24px; background:#161b22; border-bottom:1px solid #30363d; display:flex; gap:16px; flex-wrap:wrap; }}
.leg-item {{ display:flex; align-items:center; gap:6px; font-size:12px; }}
.leg-dot {{ width:12px; height:12px; border-radius:50%; }}
#network {{ width:100vw; height:calc(100vh - 110px); }}
#info {{ position:fixed; bottom:20px; right:20px; background:#161b22; border:1px solid #30363d; border-radius:8px; padding:12px 16px; max-width:320px; font-size:13px; display:none; }}
#info h3 {{ color:#f0f6fc; margin-bottom:8px; }}
</style>
</head>
<body>
<div id="header">
  <h1>🏛️ 梵天系统知识图谱</h1>
  <div class="stats">{stats} · 2026-08-02 设计院自主生成</div>
</div>
<div id="legend">
  <div class="leg-item"><div class="leg-dot" style="background:#e74c3c"></div>体制</div>
  <div class="leg-item"><div class="leg-dot" style="background:#e67e22"></div>BUG</div>
  <div class="leg-item"><div class="leg-dot" style="background:#2ecc71"></div>修复</div>
  <div class="leg-item"><div class="leg-dot" style="background:#9b59b6"></div>宪法规则</div>
  <div class="leg-item"><div class="leg-dot" style="background:#3498db"></div>模块</div>
  <div class="leg-item"><div class="leg-dot" style="background:#f1c40f"></div>指标/信号</div>
  <div class="leg-item"><div class="leg-dot" style="background:#1abc9c"></div>ML模型</div>
  <div class="leg-item"><div class="leg-dot" style="background:#95a5a6"></div>其他</div>
</div>
<div id="network"></div>
<div id="info"><h3 id="info-title"></h3><div id="info-body"></div></div>

<script>
const nodes = new vis.DataSet([{','.join(nodes_js)}]);
const edges = new vis.DataSet([{','.join(edges_js)}]);
const container = document.getElementById('network');
const options = {{
  nodes: {{ font: {{ color:'#c9d1d9', size:12 }}, borderWidth:1, borderWidthSelected:3 }},
  edges: {{ font: {{ color:'#8b949e', size:10, align:'middle' }}, color:{{ color:'#30363d', highlight:'#58a6ff' }}, smooth:{{ type:'dynamic' }} }},
  physics: {{ stabilization:{{ iterations:200 }}, barnesHut:{{ gravitationalConstant:-3000, springLength:120, springConstant:0.04 }} }},
  interaction: {{ hover:true, tooltipDelay:100 }},
  layout: {{ improvedLayout:true }}
}};
const network = new vis.Network(container, {{nodes,edges}}, options);
network.on('click', function(params) {{
  const info = document.getElementById('info');
  if (params.nodes.length) {{
    const n = nodes.get(params.nodes[0]);
    const connected = network.getConnectedEdges(params.nodes[0]);
    const rels = connected.map(eid => {{
      const e = edges.get(eid);
      const fromN = nodes.get(e.from);
      const toN = nodes.get(e.to);
      return `<div style="margin:4px 0;color:#8b949e">${{fromN.title}} <span style="color:#58a6ff">─${{e.label}}→</span> ${{toN.title}}</div>`;
    }});
    document.getElementById('info-title').textContent = n.title;
    document.getElementById('info-body').innerHTML = rels.slice(0,8).join('') + (rels.length>8?`<div style="color:#8b949e">...还有${{rels.length-8}}条</div>`:'');
    info.style.display = 'block';
  }} else {{
    info.style.display = 'none';
  }}
}});
</script>
</body>
</html>"""
    return html


# ── 主程序 ──────────────────────────────────────────────────────
def main():
    print('🏛️ 梵天知识图谱生成器')
    print('=' * 50)
    
    # 读取源文本
    print('① 读取 MEMORY.md + git log...')
    source = load_source()
    print(f'   源文本: {len(source)} 字符')
    
    # 分块
    chunk_size = 1500
    words = source.split('\n')
    chunks = []
    current = []
    for line in words:
        current.append(line)
        if len('\n'.join(current)) >= chunk_size:
            chunks.append('\n'.join(current))
            current = current[-5:]  # 5行重叠
    if current:
        chunks.append('\n'.join(current))
    print(f'   分为 {len(chunks)} 个chunk')
    
    # 提取三元组
    print('② 提取知识三元组...')
    all_triples = []
    
    # 检查是否有本地Ollama
    try:
        import urllib.request
        urllib.request.urlopen('http://localhost:11434/api/tags', timeout=2)
        use_llm = True
        print('   检测到本地Ollama，使用LLM提取')
    except:
        use_llm = False
        print('   未检测到Ollama，使用规则提取')
    
    for i, chunk in enumerate(chunks):
        if use_llm and i < 8:  # 最多前8个chunk用LLM
            triples = extract_triples_with_llm(chunk, i+1)
        else:
            triples = extract_triples_rules(chunk)
        all_triples.extend(triples)
    
    print(f'   提取三元组: {len(all_triples)} 个')
    
    # 生成HTML
    print('③ 生成交互式知识图谱...')
    html = generate_html(all_triples)
    OUTPUT_PATH.write_text(html, encoding='utf-8')
    print(f'   输出: {OUTPUT_PATH}')
    print(f'   节点/边: {html.count("id:")} 个元素')
    
    print()
    print('✅ 完成！用浏览器打开查看图谱')
    print(f'   file://{OUTPUT_PATH}')


if __name__ == '__main__':
    main()
