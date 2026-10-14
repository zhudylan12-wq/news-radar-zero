"""Local relevance rules: a generic AI mention is not evidence of a tool story."""
import re

FILTER_VERSION = 2


def has(text, words):
    return any(re.search(r'(?<![a-z0-9])' + re.escape(w) + r'(?![a-z0-9])', text, re.I)
               if w.isascii() else w in text for w in words)


AI = ('AI', 'artificial intelligence', 'LLM', 'LLMs', 'ChatGPT', 'Claude', 'Gemini', 'Codex',
      'Cursor', 'llama.cpp', 'Koboldcpp', 'Qwen', 'LoRA', 'GGUF', '人工智能', '大模型')
TOOLS = ('tool', 'tools', 'app', 'apps', 'application', 'applications', 'API', 'APIs', 'CLI', 'SDK',
         'plugin', 'plugins', 'extension', 'assistant', 'assistants', 'coding', 'code', 'codebase',
         'workflow', 'workflows', 'automation', 'automate', 'automated', 'inference', 'LoRA', 'GGUF',
         'quantized', 'quantization', 'fine-tuning', 'finetuning', 'benchmark', 'benchmarks',
         'eval', 'evals', 'terminal', 'server', 'servers', 'dashboard', 'database', 'browser',
         'editor', 'library', 'framework', 'software', 'platform', 'development', 'worktree', 'skills', 'model release', 'model releases',
         '工具', '应用', '插件', '编程', '推理', '工作流', '模型发布', '训练', '自动化')
ENGINEERING = TOOLS + ('MCP', 'developer', 'developers', 'prompt', 'prompts', 'tokens', 'context',
                       'Docker', 'Kubernetes', 'repository', 'repositories', 'GitHub', '开发', '代码')
BUSINESS = ('SaaS', 'software as a service', 'micro-saas', 'indiehacker', 'indiehackers', 'indie hacker',
            'indie hackers', 'Product Hunt', 'SEO', 'ASO', 'GTM', 'go-to-market', '出海', '跨境', '海外获客')
BUSINESS_DETAIL = TOOLS + ('customer', 'customers', 'user', 'users', 'founder', 'founders', 'team', 'teams',
                          'product', 'products', 'pricing', 'revenue', 'profit', 'MRR', 'ARR', 'billing',
                          'subscription', 'subscriptions', 'launch', 'growth', 'conversion', 'retention',
                          'churn', 'marketing', 'acquisition', 'sales', 'landing page', '获客', '定价', '收入')
NOISE = (r'\bkyunki\b', r'\bsaas\s+bhi\b', r'\btulsi\b.*\bmihir\b',
         r'\bfree\s+agent\b.*\b(NFL|NBA|MLB|WR|QB)\b', r'\b(sext|porn|uncensored chatbot|uncensored AI|NSFW)\b')
VAGUE = (r"^(and\s+)?it['’]?s?\s+open.source[!.\s]*$", r'^oh look\b', r'^prompt busted\b')


def classify(title, source):
    text = re.sub(r'https?://\S+|www\.\S+|#[\w]+', ' ', title)
    if any(re.search(p, text, re.I) for p in NOISE + VAGUE):
        return [], 0, '同名或无关内容'
    ai, tools = has(text, AI), has(text, TOOLS)
    agent = (has(text, ('AI agent', 'AI agents', 'agentic', 'MCP', '智能体', 'computer-use agents')) or
             (has(text, ('agent', 'agents', 'subagent', 'subagents')) and (ai or has(text, ENGINEERING))))
    open_source = has(text, ('open source', 'open-source', 'self-hosted', '开源', 'Apache-2.0', 'MIT license'))
    repo = source.get('kind') == 'github' or source.get('platform') == 'GitHub'
    substantive = len(re.findall(r'[a-zA-Z][\w.-]*', text)) >= 5 or len(re.findall(r'[\u4e00-\u9fff]', text)) >= 10
    open_source = (open_source and substantive) or (repo and (tools or agent))
    business = has(text, BUSINESS) and has(text, BUSINESS_DETAIL)
    topics = []
    if ai and (tools or agent or has(text, ('release', 'released', 'launch', 'launched', '发布'))): topics.append('AI 工具')
    if agent: topics.append('Agent')
    if open_source: topics.append('开源')
    if business: topics.append('出海')
    if not topics:
        return [], 0, '缺少工具、工程、开源项目或出海业务信息'
    score = 2 * len(topics) + int(tools) + int(repo)
    score += 2 * int(has(text, ('launch', 'launched', 'release', 'released', 'introducing', 'Show HN', '新增', '发布')))
    return topics, score, ' / '.join(topics).replace('Agent', '智能体')


def refine_reports(reports, cfg):
    """Reapply current rules to historical editions, keeping collection times unchanged."""
    sources = {s['id']: s for s in cfg['sources']}
    for report in reports:
        before, kept = len(report['items']), []
        for item in report['items']:
            topics, score, reason = classify(item['title'], sources.get(item['source_id'], item))
            if topics:
                item.update(topics=topics, relevance_score=score, relevance_reason=reason)
                kept.append(item)
        report['items'] = kept
        report['new_count'] = sum(i['is_new'] for i in kept)
        report['filtering'] = {'version': FILTER_VERSION,
                               'removed': report.get('filtering', {}).get('removed', 0) + before - len(kept)}
        for source in report['sources']:
            source['shown'] = sum(i['source_id'] == source['id'] for i in kept)
    return reports
