import copy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from urllib.error import HTTPError

import radar

NOW = datetime(2026, 10, 14, 6, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def rss(titles=("New AI agent",), date="Wed, 14 Oct 2026 04:00:00 GMT", publisher="https://x.com"):
    return ("<rss><channel>" + "".join(f'<item><title>{title}</title><link>https://news.google.com/rss/articles/{n}</link><pubDate>{date}</pubDate><source url="{publisher}">X</source></item>' for n, title in enumerate(titles)) + "</channel></rss>").encode()


def state():
    return {"version": 1, "seen": {}, "cooldowns": {}}


class RadarTests(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((ROOT / "config.json").read_text())
        self.cfg["sources"] = [self.cfg["sources"][4]]

    def collect(self, fetcher=None, old=None):
        return radar.collect(self.cfg, old or state(), NOW, fetcher or (lambda *args: rss()), lambda _: None)

    def test_real_config_valid(self):
        radar.validate_config(json.loads((ROOT / "config.json").read_text()))

    def test_config_rejects_http_and_duplicate_ids(self):
        cfg = json.loads((ROOT / "config.json").read_text())
        cfg["sources"][0]["url"] = "http://example.com/feed"
        with self.assertRaises(ValueError):
            radar.validate_config(cfg)
        cfg = copy.deepcopy(self.cfg)
        cfg["sources"] *= 2
        with self.assertRaises(ValueError):
            radar.validate_config(cfg)

    def test_index_cannot_be_mislabelled_direct(self):
        self.cfg["sources"][0]["access"] = "direct"
        with self.assertRaises(ValueError):
            radar.validate_config(self.cfg)

    def test_atom_prefers_alternate_link_and_published_time(self):
        body = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Agent launch</title><link rel="self" href="https://example.com/api"/><link href="https://reddit.com/r/SaaS/comments/a"/><published>2026-10-14T01:00:00Z</published><updated>2026-10-14T02:00:00Z</updated></entry></feed>'''
        items = radar.parse_feed(body)
        self.assertEqual(items[0]["url"], "https://reddit.com/r/SaaS/comments/a")
        self.assertEqual(items[0]["published_at"], "2026-10-14T01:00:00Z")

    def test_html_or_entity_response_is_an_error_not_empty(self):
        for body in [b'<html><body>Sign in</body></html>', b'<!DOCTYPE rss [<!ENTITY x "unsafe">]><rss/>']:
            report, _ = self.collect(lambda *args: body)
            self.assertEqual(report["health"], "unavailable")
            self.assertEqual(report["sources"][0]["status"], "error")

    def test_empty_feed_is_not_a_network_failure(self):
        report, _ = self.collect(lambda *args: rss(()))
        self.assertEqual(report["health"], "ok")
        self.assertEqual(report["sources"][0]["status"], "empty")

    def test_non_x_publisher_cannot_be_labelled_an_x_post(self):
        report, _ = self.collect(lambda *args: rss(publisher="https://example.com"))
        self.assertEqual(report["items"], [])

    def test_undated_old_and_future_entries_are_not_fresh(self):
        for date in ["", "Tue, 01 Sep 2026 00:00:00 GMT", "Thu, 15 Oct 2026 00:00:00 GMT"]:
            report, _ = self.collect(lambda *args: rss(date=date))
            self.assertEqual(report["items"], [])
            self.assertEqual(report["sources"][0]["status"], "no_recent")

    def test_keyword_boundaries_prevent_ai_in_paid(self):
        self.assertFalse(radar.matches("paid ads", "AI"))
        self.assertTrue(radar.matches("AI-powered tool", "AI"))
        self.assertFalse(radar.matches("agency", "agent"))

    def test_free_agent_sports_news_does_not_match_ai_agents(self):
        self.assertFalse(radar.matches_topic("Free agent WR Tyreek Hill signed a contract", "agent", self.cfg))
        self.assertTrue(radar.matches_topic("Describe an agent to automate code reviews", "agent", self.cfg))

    def test_index_results_without_matching_keywords_are_excluded(self):
        report, _ = self.collect(lambda *args: rss(("Ukraine Starlink access restricted", "New AI tool")))
        self.assertEqual(len(report["items"]), 1)
        self.assertEqual(report["items"][0]["title"], "New AI tool")

    def test_duplicate_search_results_merge_in_one_run(self):
        report, _ = self.collect(lambda *args: rss(("New AI agent", "New AI agent")))
        self.assertEqual(len(report["items"]), 1)

    def test_repeat_run_has_no_new_items_but_keeps_current_report_readable(self):
        first, saved = self.collect()
        second, _ = self.collect(old=saved)
        self.assertEqual(first["new_count"], 1)
        self.assertEqual(second["new_count"], 0)
        self.assertEqual(len(second["items"]), 1)

    def test_truncated_items_are_not_marked_as_delivered(self):
        self.cfg["max_items"] = 1
        fetcher = lambda *args: rss(("AI tool one", "AI tool two"))
        first, saved = self.collect(fetcher)
        second, _ = self.collect(fetcher, saved)
        self.assertEqual(second["new_count"], 1)
        self.assertNotEqual(first["items"][0]["title"], second["items"][0]["title"])

    def test_tracking_links_deduplicate(self):
        self.assertEqual(radar.canonical_url("https://www.reddit.com/r/test/comments/abc/?utm_source=share#x"), radar.canonical_url("https://old.reddit.com/r/test/comments/abc"))

    def test_all_failure_retains_diagnostic_report(self):
        def fail(*args):
            raise radar.FetchFailure("blocked", "HTTP 403", NOW + timedelta(hours=6))
        report, saved = self.collect(fail)
        self.assertEqual(report["health"], "unavailable")
        self.assertEqual(report["sources"][0]["status"], "blocked")
        report, _ = self.collect(lambda *args: self.fail("cooldown must not issue requests"), saved)
        self.assertEqual(report["sources"][0]["status"], "cooldown")

    def test_one_failure_does_not_stop_another_source(self):
        second = copy.deepcopy(self.cfg["sources"][0])
        second.update(id="second", query="second")
        self.cfg["sources"].append(second)
        def fetch(url, *_):
            if "second" in url:
                return rss()
            raise radar.FetchFailure("rate_limited", "HTTP 429")
        report, _ = self.collect(fetch)
        self.assertEqual(report["health"], "partial")
        self.assertEqual(len(report["items"]), 1)

    def test_retry_after_429_is_honored_without_retry(self):
        opener = Mock()
        opener.open.side_effect = HTTPError("https://example.com", 429, "Limited", {"Retry-After": "7200"}, io.BytesIO())
        with patch("radar.request.build_opener", return_value=opener), patch("radar.time.sleep") as sleep:
            with self.assertRaises(radar.FetchFailure) as caught:
                radar.fetch_bytes("https://example.com", self.cfg, NOW)
        self.assertEqual(caught.exception.retry_at, NOW + timedelta(hours=2))
        self.assertEqual(opener.open.call_count, 1)
        sleep.assert_not_called()

    def test_rate_limit_stops_other_feeds_on_the_same_host(self):
        second = copy.deepcopy(self.cfg["sources"][0])
        second["id"] = "same-host"
        self.cfg["sources"].append(second)
        fetcher = Mock(side_effect=radar.FetchFailure("rate_limited", "HTTP 429", NOW + timedelta(hours=6)))
        report, saved = self.collect(fetcher)
        self.assertEqual(fetcher.call_count, 1)
        self.assertEqual(report["sources"][1]["status"], "cooldown")
        self.assertIn("news.google.com", saved["host_cooldowns"])

    def test_transient_server_error_retries_only_once(self):
        opener = Mock()
        opener.open.side_effect = HTTPError("https://example.com", 503, "Unavailable", {}, io.BytesIO())
        with patch("radar.request.build_opener", return_value=opener), patch("radar.time.sleep"):
            with self.assertRaises(radar.FetchFailure):
                radar.fetch_bytes("https://example.com", self.cfg, NOW)
        self.assertEqual(opener.open.call_count, 2)

    def test_long_retry_after_is_not_shortened(self):
        self.assertEqual(radar.retry_time({"Retry-After": "259200"}, NOW), NOW + timedelta(days=3))

    def test_unexpected_json_shape_is_an_invalid_source(self):
        source = {"kind": "github"}
        with self.assertRaises(ValueError):
            radar.parse_source(b'[]', source)

    def test_html_escapes_titles_and_rejects_script_urls(self):
        report, _ = self.collect(lambda *args: rss(("&lt;script&gt;alert(1)&lt;/script&gt; &amp; AI tool",)))
        page = radar.render_html(report)
        self.assertNotIn("<script>", page)
        self.assertIn("&amp; AI tool", page)
        self.assertEqual(radar.safe_url("javascript:alert(1)"), "")
        self.assertEqual(radar.safe_url("https://user:pass@example.com"), "")

    def test_index_time_is_labelled(self):
        report, _ = self.collect()
        self.assertEqual(report["items"][0]["date_basis"], "索引时间，非原帖时间")
        self.assertIn("打开索引链接", radar.render_html(report))

    def test_state_contains_hashes_not_source_content(self):
        report, saved = self.collect()
        value = json.dumps(saved)
        self.assertNotIn(report["items"][0]["title"], value)
        self.assertNotIn("https://", value)
        self.assertTrue(all(len(k) == 64 for k in saved["seen"]))

    def test_expired_state_is_pruned(self):
        old = state()
        old["seen"]["expired"] = "2026-01-01T00:00:00Z"
        _, saved = self.collect(old=old)
        self.assertNotIn("expired", saved["seen"])

    def test_corrupt_state_does_not_silently_reset(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            p.write_text('{"version":2,"seen":{}}')
            with self.assertRaises(ValueError):
                radar.load_state(p)

    def test_github_window_is_creation_not_last_push(self):
        source = json.loads((ROOT / "config.json").read_text())["sources"][7]
        body = json.dumps({"items": [{"full_name": "example/agent", "description": "Agent tool", "html_url": "https://github.com/example/agent", "created_at": "2026-10-04T00:00:00Z", "pushed_at": "2026-10-14T00:00:00Z", "stargazers_count": 10}]}).encode()
        parsed = radar.parse_source(body, source)
        items, _ = radar.prepare_items(parsed, source, self.cfg, NOW)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["date_basis"], "仓库创建时间")


if __name__ == "__main__":
    unittest.main()
