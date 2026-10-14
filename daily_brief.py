"""A source-linked daily digest, assembled locally without generative claims."""
from datetime import timedelta
import html
import json
from pathlib import Path
from zoneinfo import ZoneInfo


def load_briefs(path, import_dir=None):
    paths = [path]
    if import_dir and import_dir.exists():
        paths += [d / 'briefs.json' for d in import_dir.iterdir() if d.is_dir()]
    found = {}
    for p in paths:
        if p.exists() and p.stat().st_size < 5_000_000:
            for brief in json.loads(p.read_text()).get('briefs', []):
                if brief.get('kind') == 'daily':
                    found[brief['date']] = brief
    return sorted(found.values(), key=lambda b: b['date'], reverse=True)


def build_brief(reports, now, kind='daily'):
    from radar import parse_date, iso, item_keys
    tz = reports[0]['timezone'] if reports else 'Asia/Taipei'
    cutoff = now - timedelta(hours=24)
    candidates, first_seen = {}, {}
    for report in sorted(reports, key=lambda r: r['generated_at']):
        collected = parse_date(report['generated_at'])
        if collected > now:
            continue
        for item in report['items']:
            key = item_keys(item)[0]
            first_seen.setdefault(key, collected)
            relevant_time = first_seen[key] if item['platform'] == 'GitHub' else parse_date(item['published_at'])
            if relevant_time and cutoff <= relevant_time <= now:
                candidates[key] = dict(item, discovered_at=iso(first_seen[key]))
    items = sorted(candidates.values(), key=lambda i: (i.get('relevance_score', 0), i['published_at']), reverse=True)
    counts, source_counts, selected = {}, {}, []
    for item in items:
        group = next((t for t in ('Agent', '开源', '出海', 'AI 工具') if t in item['topics']), 'AI 工具')
        if counts.get(group, 0) >= 3 or source_counts.get(item['source_id'], 0) >= 3:
            continue
        selected.append(dict(item, brief_group=group))
        counts[group] = counts.get(group, 0) + 1
        source_counts[item['source_id']] = source_counts.get(item['source_id'], 0) + 1
        if len(selected) == 10:
            break
    latest = max((r['generated_at'] for r in reports if parse_date(r['generated_at']) <= now), default=None)
    return {'date': now.astimezone(ZoneInfo(tz)).date().isoformat(), 'generated_at': iso(now),
            'timezone': tz, 'kind': kind, 'window_start': iso(cutoff), 'window_end': iso(now),
            'latest_collection_at': latest, 'items': selected, 'candidate_count': len(candidates),
            'sources': reports[0]['sources'] if reports else [],
            'stale': not latest or now - parse_date(latest) > timedelta(hours=12)}


def update_briefs(reports, archive, now, publish=False):
    from radar import parse_date
    archive = [b for b in archive if parse_date(b['generated_at']) >= now - timedelta(days=7)]
    if publish:
        brief = build_brief(reports, now)
        archive = [brief] + [b for b in archive if b['date'] != brief['date']]
    # Regular collection must not rewrite a previously delivered daily digest.
    return sorted(archive, key=lambda b: b['date'], reverse=True)[:7]


def render_brief(brief, archive):
    from radar import edition_time
    esc, tz = html.escape, brief['timezone']
    name = '每日简报' if brief['kind'] == 'daily' else '即时简报预览'
    blocks, markdown = [], [f"# {brief['date']} {name}", '', '以下为来源标题摘编，不是全文总结或独立事实核验。', '']
    for topic in ('AI 工具', 'Agent', '开源', '出海'):
        label = topic.replace('Agent', '智能体')
        entries = [i for i in brief['items'] if i['brief_group'] == topic]
        if not entries:
            continue
        cards = []
        markdown += [f'## {label}', '']
        for item in entries:
            title = item.get('title_zh') or item['title']
            date_label = '首次采集发现' if item['platform'] == 'GitHub' else item['date_basis']
            date = item.get('discovered_at') if item['platform'] == 'GitHub' else item['published_at']
            cards.append(f'<article><h3><a href="{esc(item["url"], quote=True)}" target="_blank" rel="noopener noreferrer">{esc(title)}</a></h3><p class="meta">{esc(item["platform"])} · {date_label} {edition_time(date, tz)}</p><details><summary>核对原文</summary><p>{esc(item["title"])}</p></details></article>')
            markdown += [f'- {title}', f'  来源：{item["url"]}', '']
        blocks.append(f'<section><h2>{label}</h2>{"".join(cards)}</section>')
    latest = edition_time(brief['latest_collection_at'], tz) if brief['latest_collection_at'] else '暂无材料'
    unavailable = sum(s['status'] not in {'ok', 'empty', 'no_recent'} for s in brief['sources'])
    notice = '材料已超过 12 小时未更新，请先查看采集状态。' if brief['stale'] else ''
    if not brief['items']:
        notice += '本期没有符合筛选条件的条目，不代表平台没有新资讯。'
    history = ' · '.join(f'<a href="brief-{b["date"]}.html">{b["date"]}</a>' for b in archive)
    page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><meta name="referrer" content="no-referrer"><title>{brief['date']} {name} · 资讯雷达</title><style>
body{{font-family:system-ui,-apple-system,"PingFang SC",sans-serif;background:#f4f5f0;color:#20302c;line-height:1.7;margin:0}}main{{max-width:860px;margin:auto;padding:36px 22px}}h1{{font-size:32px}}h2{{border-bottom:1px solid #ccd5cb;padding-bottom:8px}}h3{{font-size:18px;margin:0 0 12px}}a{{color:#23644c;text-underline-offset:4px}}article{{background:white;border:1px solid #dbe2da;border-radius:8px;padding:20px;margin:14px 0;overflow-wrap:anywhere}}.meta,details,footer{{font-size:13px;color:#56675f}}.notice{{border-left:3px solid #9c762a;background:#fbf6e8;padding:12px 16px}}summary{{cursor:pointer}}nav{{display:flex;gap:18px;flex-wrap:wrap}}footer{{margin-top:30px}}@media(max-width:600px){{h1{{font-size:27px}}main{{padding:24px 16px}}}}</style></head><body><main>
<nav><a href="index.html">全部资讯与采集历史</a><a href="brief.md">本期文字版</a></nav><h1>{brief['date']} · {name}</h1>
<p>AI 工具、智能体、开源与出海 · 精选 {len(brief['items'])} 条</p><p class="meta">生成：{edition_time(brief['generated_at'], tz)}（北京时间）<br>材料最新采集：{latest} · 过去 24 小时内的资讯 / 新发现项目</p>
<p class="notice">每天 10:30 在对话中提醒。以下为来源标题摘编，中文为机器翻译，可展开原文核对；不添加未经原文支持的结论。{esc(notice)}</p>
{''.join(blocks)}<footer><p>当前 {unavailable} 个来源不可用或处于退避；X 为搜索索引覆盖。没有条目不等于没有更新。</p><p>历史简报：{history or '正式日报尚未生成'}</p><p>由免费 GitHub Actions 与离线模型生成，无付费 API。</p></footer></main></body></html>'''
    return page, '\n'.join(markdown)


def write_briefs(output, reports, archive, now):
    current = archive[0] if archive else build_brief(reports, now, 'preview')
    page, markdown = render_brief(current, archive)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'brief.html').write_text(page, encoding='utf-8')
    (output / 'brief.md').write_text(markdown, encoding='utf-8')
    (output / 'brief.json').write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'briefs.json').write_text(json.dumps({'briefs': archive}, ensure_ascii=False), encoding='utf-8')
    for brief in archive:
        dated_page, _ = render_brief(brief, archive)
        (output / f'brief-{brief["date"]}.html').write_text(dated_page, encoding='utf-8')
    return current
