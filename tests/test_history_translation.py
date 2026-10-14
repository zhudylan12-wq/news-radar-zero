import copy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import radar
from translate_titles import localize_reports, prepare_model
from test_radar import NOW, ROOT, rss, state


class HistoryTranslationTests(unittest.TestCase):
    def setUp(self):
        cfg = json.loads((ROOT / "config.json").read_text())
        cfg["sources"] = [cfg["sources"][4]]
        self.report, _ = radar.collect(cfg, state(), NOW, lambda *args: rss(), lambda _: None)

    def test_archive_keeps_distinct_times_and_deduplicates_one_run(self):
        older = copy.deepcopy(self.report)
        older["generated_at"] = radar.iso(NOW - timedelta(hours=6))
        editions = radar.merge_history([older, self.report], self.report, NOW)
        self.assertEqual(len(editions), 2)
        self.assertEqual(editions[0]["generated_at"], radar.iso(NOW))
        page = radar.render_html(self.report, editions)
        self.assertEqual(page.count('<option value="edition-'), 2)
        self.assertIn("2026-10-14 14:00:00", page)
        self.assertIn("2026-10-14 08:00:00", page)
        self.assertEqual(page.count('class="edition" id='), 2)

    def test_history_expiry_and_count_limit(self):
        reports = []
        for hours in range(200):
            r = copy.deepcopy(self.report)
            r["generated_at"] = radar.iso(NOW - timedelta(hours=hours))
            reports.append(r)
        recent = radar.merge_history(reports, self.report, NOW)
        self.assertEqual(len(recent), 30)
        self.assertTrue(all(radar.parse_date(r['generated_at']) >= NOW - timedelta(days=7) for r in recent))

    def test_legacy_download_is_imported_without_changing_collection_time(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "run123"
            folder.mkdir()
            radar.atomic_json(folder / "latest.json", self.report)
            loaded = radar.load_history(Path(d) / "missing.json", Path(d))
            self.assertEqual(loaded[0]["generated_at"], self.report["generated_at"])

    def test_nested_history_artifact_is_recovered_once(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "run123"
            folder.mkdir()
            radar.atomic_json(folder / "latest.json", self.report)
            radar.atomic_json(folder / "history.json", {"reports": [self.report]})
            self.assertEqual(len(radar.load_history(Path(d) / "missing.json", Path(d))), 1)

    def test_same_title_in_two_editions_translates_once_and_retains_original(self):
        original = self.report["items"][0]["title"]
        backend = Mock(return_value=["新的 AI 智能体"])
        older = copy.deepcopy(self.report)
        localize_reports([self.report, older], backend)
        backend.assert_called_once_with([original])
        self.assertEqual(self.report["items"][0]["title"], original)
        self.assertEqual(older["items"][0]["title_zh"], "新的 AI 智能体")
        page = radar.render_html(self.report)
        self.assertIn("新的 AI 智能体", page)
        self.assertIn("查看原文", page)
        self.assertIn(original, page)

    def test_previous_translation_is_reused_without_model_call(self):
        older = copy.deepcopy(self.report)
        older["items"][0]["title_zh"] = "旧缓存译文"
        backend = Mock(side_effect=AssertionError("must not call model"))
        localize_reports([self.report, older], backend)
        backend.assert_not_called()
        self.assertEqual(self.report["items"][0]["title_zh"], "旧缓存译文")

    def test_model_failure_preserves_original_and_reports_unavailable(self):
        localize_reports([self.report], Mock(side_effect=RuntimeError("missing model")))
        self.assertEqual(self.report["translation"]["status"], "unavailable")
        self.assertIn("中文翻译暂不可用", radar.render_html(self.report))

    def test_chinese_original_is_not_sent_for_translation(self):
        self.report["items"][0]["title"] = "开源智能体的新版本正式发布"
        backend = Mock()
        localize_reports([self.report], backend)
        backend.assert_not_called()
        self.assertEqual(self.report["items"][0]["translation_status"], "original_zh")

    def test_translated_html_is_escaped(self):
        localize_reports([self.report], lambda titles: ['<img src=x onerror=alert(1)>新的智能体'])
        page = radar.render_html(self.report)
        self.assertNotIn('<img src=x', page)
        self.assertIn('&lt;img', page)

    def test_model_with_wrong_checksum_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "en-zh.argosmodel").write_bytes(b"not a real model")
            with self.assertRaisesRegex(ValueError, "checksum"):
                prepare_model(Path(d))

    def test_complete_output_is_self_contained_and_has_history_data(self):
        localize_reports([self.report], lambda titles: ["新的智能体"])
        with tempfile.TemporaryDirectory() as d:
            radar.write_reports(Path(d), self.report, [self.report])
            history = json.loads((Path(d) / "history.json").read_text())
            self.assertEqual(len(history["reports"]), 1)
            page = (Path(d) / "index.html").read_text()
            self.assertNotIn("fetch(", page)
            self.assertIn('id="edition-select"', page)


if __name__ == "__main__":
    unittest.main()
