#!/usr/bin/env python3
"""Public feeds -> timestamped Chinese reports, with optional offline translation."""
import argparse
import email.utils
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib import error, parse, request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

UTC = timezone.utc
ATOM = "{http://www.w3.org/2005/Atom}"
MAX_BODY = 3_000_000
STATE_VERSION = 1
HISTORY_DAYS = 7
HISTORY_LIMIT = 30
STATUS_LABELS = {"ok": "可用", "empty": "源返回空列表", "no_recent": "无可确认的近期匹配",
                 "blocked": "访问受限", "rate_limited": "限流", "error": "失败", "cooldown": "退避中"}


def iso(value):
    return value.astimezone(UTC).isoformat(timespec="seconds")


def parse_date(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = email.utils.parsedate_to_datetime(str(value))
        except (ValueError, TypeError, OverflowError):
            return None
    return dt.astimezone(UTC) if dt.tzinfo else None


def clean_text(value, limit=320):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", str(value or "")))).strip()[:limit]


def safe_url(value):
    value = str(value or "").strip()
    try:
        parts = parse.urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            return ""
        if any(ord(c) < 32 for c in value):
            return ""
        return parse.urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
    except ValueError:
        return ""


def canonical_url(url):
    p = parse.urlsplit(url)
    host = (p.hostname or "").lower().removeprefix("www.")
    if host in {"old.reddit.com", "new.reddit.com"}:
        host = "reddit.com"
    if host == "twitter.com":
        host = "x.com"
    query = [(k, v) for k, v in parse.parse_qsl(p.query) if not k.lower().startswith("utm_") and k not in {"ref", "source"}]
    return parse.urlunsplit(("https", host, p.path.rstrip("/"), parse.urlencode(sorted(query)), ""))


def item_keys(item):
    values = ["url:" + canonical_url(item["url"])]
    # Google wraps the same post in different article URLs for different searches.
    # Direct community posts stay URL-based: identical titles can be distinct posts.
    if item["access"] == "indexed" and item["platform"] == "X":
        values.append("x-title:" + re.sub(r"\W+", " ", item["title"].casefold()).strip())
    return [hashlib.sha256(v.encode()).hexdigest() for v in values]


def matches(text, keyword):
    keyword = keyword.casefold()
    pattern = re.escape(keyword)
    if keyword.isascii():
        pattern = r"(?<![a-z0-9])" + pattern + r"(?![a-z0-9])"
    return re.search(pattern, text.casefold()) is not None


def matches_topic(text, keyword, cfg):
    context = cfg.get("keyword_context", {}).get(keyword.casefold())
    return matches(text, keyword) and (not context or any(matches(text, word) for word in context))


def validate_config(cfg):
    ZoneInfo(cfg["timezone"])
    for name, low, high in [("lookback_hours", 1, 168), ("max_items", 1, 200),
                             ("max_items_per_source", 1, 50), ("request_timeout_seconds", 1, 60),
                             ("source_interval_seconds", 1, 30)]:
        if type(cfg.get(name)) is not int or not low <= cfg[name] <= high:
            raise ValueError(f"{name} must be an integer between {low} and {high}")
    if not isinstance(cfg.get("topics"), dict) or not cfg["topics"]:
        raise ValueError("topics must be a nonempty object")
    for keywords in cfg["topics"].values():
        if not isinstance(keywords, list) or not all(isinstance(k, str) and k.strip() for k in keywords):
            raise ValueError("topic keywords must be nonempty strings")
    sources = cfg.get("sources", [])
    if not 1 <= len(sources) <= 20:
        raise ValueError("configure 1 to 20 sources")
    ids = set()
    for s in sources:
        if not re.fullmatch(r"[a-z0-9-]{1,64}", s["id"]) or s["id"] in ids:
            raise ValueError("source IDs must be unique lowercase slugs")
        ids.add(s["id"])
        if s["kind"] not in {"rss", "google_news", "github", "hackernews"}:
            raise ValueError("unknown source kind")
        if s["access"] not in {"direct", "indexed"}:
            raise ValueError("unknown source access type")
        if s["kind"] in {"google_news", "hackernews"} and s["access"] != "indexed":
            raise ValueError("search sources must be labelled indexed")
        if not set(s["topics"]).issubset(cfg["topics"]):
            raise ValueError("source references an unknown topic")
        if s["kind"] == "rss":
            if not safe_url(s["url"]) or parse.urlsplit(s["url"]).scheme != "https":
                raise ValueError("RSS endpoints must use HTTPS without embedded credentials")
        elif not isinstance(s.get("query"), str) or not s["query"].strip():
            raise ValueError("search source requires a query")
    return cfg


class FetchFailure(Exception):
    def __init__(self, status, message, retry_at=None):
        super().__init__(message)
        self.status, self.retry_at = status, retry_at


class HTTPSRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if parse.urlsplit(newurl).scheme != "https":
            raise FetchFailure("blocked", "Rejected redirect to a non-HTTPS endpoint")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def retry_time(headers, now):
    value = headers.get("Retry-After", "") if headers else ""
    if value.isdigit():
        wait = timedelta(seconds=int(value))
    else:
        parsed = parse_date(value)
        wait = parsed - now if parsed else timedelta(hours=6)
    return now + max(timedelta(hours=1), wait)


def fetch_bytes(url, cfg, now):
    req = request.Request(url, headers={"User-Agent": cfg["user_agent"],
                                        "Accept": "application/atom+xml,application/rss+xml,application/json,text/xml;q=0.9"})
    opener = request.build_opener(HTTPSRedirect())
    for attempt in range(2):
        try:
            with opener.open(req, timeout=cfg["request_timeout_seconds"]) as response:
                body = response.read(MAX_BODY + 1)
                if len(body) > MAX_BODY:
                    raise FetchFailure("error", "Response exceeds 3 MB")
                return body
        except error.HTTPError as exc:
            if exc.code == 429:
                raise FetchFailure("rate_limited", "HTTP 429; no immediate retry", retry_time(exc.headers, now))
            if exc.code in {401, 403, 410}:
                raise FetchFailure("blocked", f"HTTP {exc.code}; access unavailable", now + timedelta(hours=6))
            if exc.code >= 500 and attempt == 0:
                time.sleep(2)
                continue
            raise FetchFailure("error", f"HTTP {exc.code}")
        except (error.URLError, TimeoutError, OSError) as exc:
            if attempt == 0:
                time.sleep(2)
                continue
            raise FetchFailure("error", f"Network failure: {type(exc).__name__}")


def source_url(source, now, cfg):
    kind = source["kind"]
    if kind == "rss":
        return source["url"]
    if kind == "google_news":
        return "https://news.google.com/rss/search?" + parse.urlencode({"q": source["query"], "hl": "en-US", "gl": "US", "ceid": "US:en"})
    if kind == "github":
        since = (now - timedelta(days=source.get("created_within_days", 14))).date()
        return "https://api.github.com/search/repositories?" + parse.urlencode({"q": source["query"] + f" created:>={since}", "sort": "stars", "order": "desc", "per_page": 30})
    cutoff = int((now - timedelta(hours=cfg["lookback_hours"])).timestamp())
    return "https://hn.algolia.com/api/v1/search_by_date?" + parse.urlencode({"query": source["query"], "tags": "story", "restrictSearchableAttributes": "title", "numericFilters": f"created_at_i>{cutoff}", "hitsPerPage": 30})


def parse_feed(body):
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise ValueError("DTD/entity declarations are not supported")
    root = ET.fromstring(body)
    if root.tag == ATOM + "feed":
        result = []
        for e in root.findall(ATOM + "entry"):
            links = e.findall(ATOM + "link")
            url = next((link.get("href", "") for link in links if link.get("rel", "alternate") == "alternate"), "")
            title = "".join(e.find(ATOM + "title").itertext()) if e.find(ATOM + "title") is not None else ""
            result.append({"title": title, "url": url, "published_at": e.findtext(ATOM + "published") or e.findtext(ATOM + "updated")})
        return result
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("Expected RSS/Atom, received another document")
    return [{"title": e.findtext("title", ""), "url": e.findtext("link", ""),
             "published_at": e.findtext("pubDate"), "publisher_url": e.find("source").get("url", "") if e.find("source") is not None else ""}
            for e in root.findall("./channel/item")]


def parse_source(body, source):
    if source["kind"] in {"rss", "google_news"}:
        items = parse_feed(body)
        if source["kind"] == "google_news" and source["platform"] == "X":
            # A search hit is X content only if its publisher metadata says so.
            items = [i for i in items if (parse.urlsplit(i["publisher_url"]).hostname or "").removeprefix("www.") in {"x.com", "twitter.com"}]
            for i in items:
                i["title"] = re.sub(r" - (X|Twitter)$", "", i["title"])
        return items
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    if source["kind"] == "github":
        if not isinstance(data.get("items"), list):
            raise ValueError("GitHub response has no items list")
        return [{"title": i["full_name"] + " — " + (i.get("description") or ""), "url": i["html_url"],
                 "published_at": i["created_at"], "stars": i.get("stargazers_count", 0)} for i in data["items"]]
    if not isinstance(data.get("hits"), list):
        raise ValueError("HN response has no hits list")
    return [{"title": i.get("title", ""), "url": "https://news.ycombinator.com/item?id=" + str(i["objectID"]),
             "published_at": i.get("created_at"), "external_url": safe_url(i.get("url"))} for i in data["hits"]]


def prepare_items(raw, source, cfg, now):
    result, rejected = [], {"invalid": 0, "undated": 0, "outside_window": 0, "off_topic": 0}
    hours = source.get("created_within_days", 0) * 24 if source["kind"] == "github" else cfg["lookback_hours"]
    cutoff = now - timedelta(hours=hours)
    for value in raw:
        title, url = clean_text(value.get("title")), safe_url(value.get("url"))
        dt = parse_date(value.get("published_at"))
        if not title or not url:
            rejected["invalid"] += 1
            continue
        if not dt:
            rejected["undated"] += 1
            continue
        if dt < cutoff or dt > now + timedelta(minutes=10):
            rejected["outside_window"] += 1
            continue
        topics = [topic for topic, keywords in cfg["topics"].items() if any(matches_topic(title, k, cfg) for k in keywords)]
        if source.get("require_keyword") and not topics:
            rejected["off_topic"] += 1
            continue
        item = {"title": title, "url": url, "published_at": iso(dt), "source_id": source["id"],
                "source_name": source["name"], "platform": source["platform"], "access": source["access"],
                "topics": topics or source["topics"], "date_basis": "索引时间，非原帖时间" if source["kind"] == "google_news" else "仓库创建时间" if source["kind"] == "github" else "原帖发布时间"}
        if "stars" in value:
            item["stars"] = value["stars"]
        if value.get("external_url"):
            item["external_url"] = value["external_url"]
        result.append(item)
    return result, rejected


def load_state(path):
    if not path.exists():
        return {"version": STATE_VERSION, "seen": {}, "cooldowns": {}}
    state = json.loads(path.read_text())
    if state.get("version") != STATE_VERSION or not isinstance(state.get("seen"), dict) or not isinstance(state.get("cooldowns"), dict):
        raise ValueError("Unrecognized state; refusing to silently reset deduplication")
    return state


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_history(path, import_dir=None):
    reports = []
    paths = [path] if path.exists() else []
    if import_dir and import_dir.exists():
        # Prior run artifacts contain either the full archive or a legacy latest.json.
        for directory in sorted(p for p in import_dir.iterdir() if p.is_dir()):
            candidate = directory / "history.json"
            paths.append(candidate if candidate.exists() else directory / "latest.json")
    for source in paths:
        if not source.exists():
            continue
        if source.stat().st_size > 15_000_000:
            raise ValueError("History exceeds the 15 MB safety limit")
        data = json.loads(source.read_text(encoding="utf-8"))
        values = data.get("reports", [data]) if isinstance(data, dict) else []
        for report in values:
            if (isinstance(report, dict) and parse_date(report.get("generated_at")) and
                    isinstance(report.get("items"), list) and isinstance(report.get("sources"), list)):
                reports.append(report)
    return reports


def merge_history(previous, report, now):
    cutoff = now - timedelta(days=HISTORY_DAYS)
    unique = {}
    for edition in previous + [report]:
        generated = parse_date(edition.get("generated_at"))
        if generated and cutoff <= generated <= now + timedelta(minutes=10):
            unique[iso(generated)] = edition
    return [unique[key] for key in sorted(unique, reverse=True)[:HISTORY_LIMIT]]


def write_reports(output, report, editions):
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output / "latest.json", report)
    atomic_json(output / "history.json", {"version": 1, "retention_days": HISTORY_DAYS, "reports": editions})
    (output / "latest.md").write_text(render_markdown(report), encoding="utf-8")
    (output / "index.html").write_text(render_html(report, editions), encoding="utf-8")
    (output / ".nojekyll").write_text("")


def collect(cfg, state, now, fetcher=fetch_bytes, sleeper=time.sleep):
    candidates, statuses, cooldowns = [], [], {}
    host_cooldowns = {host: until for host, until in state.get("host_cooldowns", {}).items()
                      if parse_date(until) and parse_date(until) > now}
    for n, source in enumerate(cfg["sources"]):
        if n:
            sleeper(cfg["source_interval_seconds"])
        status = {"id": source["id"], "name": source["name"], "access": source["access"],
                  "platform": source["platform"], "checked_at": iso(now), "fetched": 0, "eligible": 0, "shown": 0}
        url = source_url(source, now, cfg)
        host = parse.urlsplit(url).hostname
        waits = [parse_date(state["cooldowns"].get(source["id"])), parse_date(host_cooldowns.get(host))]
        until = max((wait for wait in waits if wait), default=None)
        if until and until > now:
            status.update(status="cooldown", detail="未请求；等待限流或访问限制退避结束", retry_at=iso(until))
            cooldowns[source["id"]] = iso(until)
        else:
            try:
                body = fetcher(url, cfg, now)
                raw = parse_source(body, source)
                items, rejected = prepare_items(raw, source, cfg, now)
                status.update(status="ok" if items else "no_recent" if raw else "empty", fetched=len(raw), eligible=len(items), rejected=rejected)
                candidates.extend(items)
            except FetchFailure as exc:
                status.update(status=exc.status, detail=str(exc))
                if exc.retry_at:
                    status["retry_at"] = cooldowns[source["id"]] = iso(exc.retry_at)
                    if exc.status == "rate_limited":
                        # Rate limits often apply to the origin/IP, not one feed.
                        host_cooldowns[host] = iso(exc.retry_at)
            except (ValueError, KeyError, TypeError, ET.ParseError) as exc:
                status.update(status="error", detail=f"Invalid source response: {type(exc).__name__}")
        statuses.append(status)

    retention = now - timedelta(days=30)
    seen = {k: v for k, v in state["seen"].items() if parse_date(v) and retention <= parse_date(v) <= now}
    for item in candidates:
        item["is_new"] = not any(key in seen for key in item_keys(item))
    candidates.sort(key=lambda i: (i["is_new"], i["published_at"]), reverse=True)
    items, run_keys, per_source = [], set(), {}
    for item in candidates:
        keys = item_keys(item)
        if any(key in run_keys for key in keys):
            continue
        sid = item["source_id"]
        if per_source.get(sid, 0) >= cfg["max_items_per_source"]:
            continue
        if len(items) >= cfg["max_items"]:
            break
        items.append(item)
        run_keys.update(keys)
        per_source[sid] = per_source.get(sid, 0) + 1
        for key in keys:
            seen[key] = iso(now)
    for status in statuses:
        status["shown"] = per_source.get(status["id"], 0)
    successful = sum(s["status"] in {"ok", "empty", "no_recent"} for s in statuses)
    report = {"title": cfg["title"], "generated_at": iso(now), "timezone": cfg["timezone"],
              "lookback_hours": cfg["lookback_hours"], "items": items, "sources": statuses,
              "health": "ok" if successful == len(statuses) else "partial" if successful else "unavailable",
              "new_count": sum(i["is_new"] for i in items), "available_sources": successful,
              "limitations": ["X 来自 Google News 公开搜索索引，不是 X 官方接口；可能延迟、漏收录，未读取全文。",
                              "Reddit 仅尝试公开 RSS；限流、拒绝访问和失败不代表没有新帖。",
                              "GitHub 补充源按近 14 天创建、至少 10 星筛选；不代表增长速度或质量排名。",
                              "NEW 表示本雷达近 30 天内未展示；缺失缓存会重置标记。筛选使用规则，不调用付费模型。",
                              "日期、标题和链接由上游提供；索引时间不等于原帖发布时间，内容未经独立事实核验。"]}
    new_state = {"version": STATE_VERSION, "seen": dict(sorted(seen.items(), key=lambda x: x[1], reverse=True)[:10000]), "cooldowns": cooldowns, "host_cooldowns": host_cooldowns}
    return report, new_state


def md_text(text):
    return re.sub(r"([\\`*{}\[\]()#+.!_|<>])", r"\\\1", str(text)).replace("\n", " ")


def display_time(value, tz):
    return parse_date(value).astimezone(ZoneInfo(tz)).strftime("%m-%d %H:%M")


def edition_time(value, tz):
    return parse_date(value).astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M:%S")


def chinese_label(value):
    return str(value).replace("Agent", "智能体")


def source_detail(source):
    detail = source.get("detail", "")
    if source["status"] == "rate_limited":
        return "请求过于频繁，已暂停同域名采集"
    if source["status"] == "blocked":
        code = re.search(r"HTTP \d+", detail)
        return "来源限制访问" + (f"（{code.group()}）" if code else "")
    if source["status"] == "error":
        return "读取或解析失败，本轮未获取内容"
    return detail or "—"


def platform_order(report):
    found = list(dict.fromkeys(i["platform"] for i in report["items"]))
    preferred = [p for p in ("X", "Reddit", "GitHub", "Hacker News") if p in found]
    return preferred + [p for p in found if p not in preferred]


def render_markdown(report):
    tz = report["timezone"]
    lines = ["# " + report["title"], "", f"更新时间：{display_time(report['generated_at'], tz)}（{tz}） · {len(report['items'])} 条 · {report['new_count']} 条首次展示", "",
             "> X 为搜索索引线索，可能延迟或遗漏。来源访问失败不代表没有新资讯。", "", "## 来源状态", "",
             "| 来源 | 状态 | 获取 / 匹配 / 展示 | 说明 |", "|---|---|---:|---|"]
    for s in report["sources"]:
        lines.append(f"| {md_text(s['name'])} | {STATUS_LABELS[s['status']]} | {s['fetched']} / {s['eligible']} / {s['shown']} | {md_text(s.get('detail', ''))} |")
    for platform in platform_order(report):
        lines += ["", "## " + platform, ""]
        for i in report["items"]:
            if i["platform"] != platform:
                continue
            url = parse.quote(i["url"], safe=":/?&=%+#@,;~!$'*")
            lines.append(f"- {'**首次展示** ' if i['is_new'] else ''}[{md_text(i.get('title_zh') or i['title'])}]({url})  \n  {md_text(chinese_label(i['source_name']))} · {display_time(i['published_at'], tz)} · {i['date_basis']} · {chinese_label(' / '.join(i['topics']))}")
            if i.get("title_zh") and i["title_zh"] != i["title"]:
                lines.append("  原文：" + md_text(i["title"]))
    if not report["items"]:
        lines += ["", "本轮没有可展示的近期条目。请先查看来源状态，不能据此判断平台没有更新。"]
    lines += ["", "## 证据边界", ""] + ["- " + x for x in report["limitations"]]
    return "\n".join(lines) + "\n"


def render_edition(report):
    esc = html.escape
    tz = report["timezone"]
    groups = []
    for platform in platform_order(report):
        cards = []
        for i in report["items"]:
            if i["platform"] != platform:
                continue
            label = "打开索引链接 ↗" if i["platform"] == "X" else "查看来源 ↗"
            title = i.get("title_zh") or i["title"]
            translated = bool(i.get("title_zh") and i["title_zh"] != i["title"])
            original = f'<details class="original"><summary>查看原文 · 机器翻译供参考</summary><p lang="en">{esc(i["title"])}</p></details>' if translated else ''
            fallback = ' · 中文翻译暂不可用，保留原文' if i.get('translation_status') == 'unavailable' else ''
            cards.append(f'''<article><div class="meta">{'<b>首次展示</b> · ' if i['is_new'] else ''}{esc(chinese_label(' / '.join(i['topics'])))}</div><h3><a href="{esc(i['url'], quote=True)}" target="_blank" rel="noopener noreferrer">{esc(title)}</a></h3>{original}<p class="meta">{esc(chinese_label(i['source_name']))} · {display_time(i['published_at'], tz)}<br>{esc(i['date_basis'])}{fallback}</p><a class="out" href="{esc(i['url'], quote=True)}" target="_blank" rel="noopener noreferrer">{label}</a></article>''')
        groups.append(f'<section><h2>{esc(platform)} <small>{len(cards)}</small></h2><div class="grid">{"".join(cards)}</div></section>')
    rows = "".join(f"<tr><td>{esc(chinese_label(s['name']))}</td><td>{STATUS_LABELS[s['status']]}</td><td>{s['fetched']} / {s['eligible']} / {s['shown']}</td><td>{esc(source_detail(s))}{' · 下次尝试 ' + display_time(s['retry_at'], tz) if s.get('retry_at') else ''}</td></tr>" for s in report["sources"])
    empty = '<p class="notice">本轮没有可展示的近期条目。请查看来源状态；这不代表平台没有更新。</p>' if not report["items"] else ""
    translation = report.get("translation", {})
    return f'''<div class="edition-body"><p class="meta">本期 {len(report['items'])} 条 · 首次展示 {report['new_count']} 条 · 中文标题 {translation.get('ready', 0)}/{len(report['items'])} · 可访问来源 {report['available_sources']}/{len(report['sources'])}</p>{empty}{''.join(groups)}
<details class="sources"><summary>查看本期来源状态</summary><div class="table-wrap"><table><thead><tr><th>来源</th><th>状态</th><th>获取 / 匹配 / 展示</th><th>说明</th></tr></thead><tbody>{rows}</tbody></table></div></details></div>'''


def render_html(report, history=None):
    esc = html.escape
    editions = history or [report]
    tz = report["timezone"]
    parts, options = [], []
    for index, edition in enumerate(editions):
        timestamp = edition_time(edition["generated_at"], tz)
        identifier = parse_date(edition["generated_at"]).strftime("edition-%Y%m%dT%H%M%SZ")
        options.append(f'<option value="{identifier}">{timestamp}{" · 最新" if index == 0 else ""} · {len(edition["items"])} 条</option>')
        parts.append(f'''<details class="edition" id="{identifier}" {'open' if index == 0 else ''}><summary><span class="edition-tag">{'最新一期' if index == 0 else '历史一期'}</span><time datetime="{esc(edition['generated_at'], quote=True)}">{timestamp}</time><span class="edition-count">{len(edition['items'])} 条资讯 · 首次展示 {edition['new_count']} 条</span></summary>{render_edition(edition)}</details>''')
    latest = edition_time(report["generated_at"], tz)
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><meta name="referrer" content="no-referrer"><title>{esc(report['title'])}</title>
<style> :root{{font-family:system-ui,-apple-system,"PingFang SC",sans-serif;color:#20302c;background:#f4f5f0;line-height:1.6}}*{{box-sizing:border-box}}body{{margin:0}}main{{max-width:1120px;margin:auto;padding:48px 28px}}header{{border-bottom:2px solid #244b40;padding-bottom:24px}}.eyebrow{{letter-spacing:.15em;font-size:12px;color:#527265}}h1{{font-size:38px;letter-spacing:-.04em;margin:10px 0}}h2{{font-size:23px;margin-top:38px}}h3{{font-size:18px;line-height:1.55;margin:10px 0 18px;overflow-wrap:anywhere}}a{{color:inherit;text-underline-offset:4px}}h3 a{{text-decoration:none}}h3 a:hover{{text-decoration:underline}}.meta{{font-size:13px;color:#56675f}}b{{color:#23644c}}.notice{{border-left:3px solid #9c762a;padding:12px 18px;background:#fbf6e8;font-size:14px}}.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}}article{{padding:22px;background:#fff;border:1px solid #dbe2da;border-radius:8px}}.out{{font-size:13px;color:#23644c}}small{{color:#6e7b73;font-size:14px;font-weight:400}}.table-wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{text-align:left;border-bottom:1px solid #dbe2da;padding:12px 10px}}th{{white-space:nowrap}}footer{{margin-top:40px;border-top:1px solid #ccd5cb;padding-top:20px;color:#56675f;font-size:13px}}li{{margin:6px 0}}a:focus-visible{{outline:3px solid #46886e;outline-offset:4px}}@media(max-width:650px){{main{{padding:28px 18px}}h1{{font-size:30px}}.grid{{grid-template-columns:1fr}}article{{padding:18px}}}}.schedule{{font-size:14px;color:#23644c}}.edition-picker{{display:flex;align-items:center;gap:12px;margin:20px 0;flex-wrap:wrap}}select{{font:inherit;color:inherit;background:#fff;border:1px solid #b6c9bd;border-radius:6px;padding:10px;max-width:100%}}.edition{{border:1px solid #cbd8cd;border-radius:8px;margin:14px 0;background:#fff;scroll-margin-top:20px}}.edition>summary{{padding:18px;cursor:pointer;list-style:none;display:flex;gap:14px;align-items:center;flex-wrap:wrap}}.edition>summary::before{{content:"＋";color:#23644c}}.edition[open]>summary::before{{content:"−"}}.edition[open]>summary{{border-bottom:1px solid #dbe2da;background:#eaf1e8}}.edition-tag{{font-size:12px;color:#23644c}}time{{font-weight:650;font-variant-numeric:tabular-nums}}.edition-count{{color:#56675f;font-size:13px}}.edition-body{{padding:6px 20px 22px}}.edition-body h2{{margin-top:24px}}.original{{font-size:13px;color:#56675f;margin:8px 0 16px}}.original summary,.sources summary{{cursor:pointer}}.original p{{overflow-wrap:anywhere}}.sources{{margin-top:24px}}@media(max-width:650px){{.edition-body{{padding:4px 12px 16px}}.edition>summary{{padding:14px;gap:8px}}select{{width:100%}}}}</style></head>
<body><main><header><div class="eyebrow">个人资讯 · 中文阅读版</div><h1>{esc(report['title'])}</h1><p>AI 工具、智能体、开源与出海</p><p class="meta">最新采集：{latest}（北京时间 / UTC+8）</p><p class="schedule">每天 08:17 / 14:17 / 20:17 · 最近 {HISTORY_DAYS} 天 · 已收录 {len(editions)} 期</p><a href="latest.md">本期文字版</a> · <a href="history.json">各期数据</a></header>
<p class="notice">中文标题由离线模型翻译，可展开原文核对。X 为搜索索引线索，可能延迟或遗漏；来源失败不代表没有更新。</p>
<section aria-label="按采集时间查看"><h2>按采集时间查看</h2><p class="meta">时间戳是本次采集时间，每条资讯另标原帖或索引时间。下载的文件不会自动更新。</p><div class="edition-picker"><label for="edition-select">选择采集时间</label><select id="edition-select">{''.join(options)}</select></div>{''.join(parts)}</section>
<footer><strong>阅读边界</strong><ul>{''.join('<li>'+esc(v.replace('NEW', '首次展示'))+'</li>' for v in report['limitations'])}</ul><p>历史保留最近 {HISTORY_DAYS} 天、最多 {HISTORY_LIMIT} 期；缓存丢失时可能不完整。定时调度可能延迟。</p><p>英译中模型：Argos / OPUS-MT 1.9，Jörg Tiedemann 与 Santhosh Thottingal，CC-BY 4.0。使用免费运行环境的 CPU，不调用付费 API。</p></footer></main>
<script type="module">document.getElementById('edition-select').addEventListener('change', function() {{ for (const edition of document.querySelectorAll('.edition')) {{ edition.open = edition.id === this.value; }} }});</script></body></html>'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=Path("config.json"))
    p.add_argument("--state", type=Path, default=Path(".state/state.json"))
    p.add_argument("--output", type=Path, default=Path("site"))
    p.add_argument("--history", type=Path, default=Path(".state/history.json"))
    p.add_argument("--import-history-dir", type=Path)
    p.add_argument("--check", action="store_true", help="validate configuration without network access")
    args = p.parse_args()
    cfg = validate_config(json.loads(args.config.read_text(encoding="utf-8")))
    if args.check:
        print(f"Configuration valid: {len(cfg['sources'])} sources")
        return 0
    now = datetime.now(UTC)
    report, state = collect(cfg, load_state(args.state), now)
    editions = merge_history(load_history(args.history, args.import_history_dir), report, now)
    from translate_titles import localize_reports
    localize_reports(editions, model_ready=os.environ.get("RADAR_MODEL_READY", "success") == "success")
    write_reports(args.output, report, editions)
    atomic_json(args.history, {"version": 1, "reports": editions})
    atomic_json(args.state, state)
    summary = f"Sources available: {report['available_sources']}/{len(report['sources'])}; items: {len(report['items'])}; new: {report['new_count']}; health: {report['health']}"
    print(summary)
    print(f"History editions: {len(editions)}; Chinese titles: {report['translation']['ready']}/{len(report['items'])}")
    for s in report["sources"]:
        print(f"{s['id']}: {s['status']} ({s['fetched']}/{s['eligible']}/{s['shown']})")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        # Summaries/logs outlive report artifacts. Keep source text out of both.
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("## News radar\n\n" + summary + "\n\nDownload the `news-radar` artifact to read index.html / latest.md / latest.json. X is search-index coverage, not a direct timeline.\n")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"health={report['health']}\n")
    # Complete the report even when sources fail, so the user can see evidence.
    # The workflow checks health after preserving the diagnostic artifact.
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError) as exc:
        print(f"Configuration/state/output error: {exc}", file=sys.stderr)
        sys.exit(1)
