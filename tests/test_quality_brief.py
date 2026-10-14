import copy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import radar
from relevance import classify, refine_reports
from translate_titles import localize_reports, translation_parts, valid_translation, polish_translation, TRANSLATION_VERSION
from daily_brief import build_brief, update_briefs, write_briefs, load_briefs
from test_radar import NOW, ROOT, rss, state


class QualityBriefTests(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((ROOT / 'config.json').read_text())
        self.cfg['sources'] = [self.cfg['sources'][4]]
        self.report, _ = radar.collect(self.cfg, state(), NOW, lambda *args: rss(), lambda _: None)

    def test_noise_examples_are_rejected_even_inside_known_communities(self):
        titles = [
            'Kyunki Saas Bhi cast reunites ahead of season finale',
            'IMF says AI investment keeps global growth around 3%',
            'AI is less regulated than milk, Microsoft president says',
            'Free agent NFL player signs a new contract',
            'Local grocery launches marketing campaign for growth',
            "And it's open-source! https://t.co/example",
            'ParkMundo raises seed funding #AI #Travel #TravelTech',
            'Infosys boss describes AI job fears as chicken',
        ]
        for title in titles:
            with self.subTest(title=title):
                self.assertEqual(classify(title, {'kind': 'rss', 'platform': 'Reddit'})[0], [])

    def test_relevant_examples_remain(self):
        examples = {
            'Show HN: Open-source desktop app for local AI agents': '开源',
            'MCP server for automating browser tests': 'Agent',
            'Koboldcpp v1.123 released': 'AI 工具',
            'Train LoRA directly on quantized GGUF models': 'AI 工具',
            'How our SaaS improved customer retention and pricing': '出海',
            'Indie hacker launches a subscription app': '出海',
        }
        for title, topic in examples.items():
            with self.subTest(title=title): self.assertIn(topic, classify(title, {})[0])

    def test_history_is_refiltered_without_changing_timestamp_or_raw_title(self):
        keep = copy.deepcopy(self.report['items'][0])
        noise = dict(keep, title='Kyunki Saas Bhi TV finale', url='https://example.com/tv')
        self.report['items'].append(noise)
        before = self.report['generated_at']
        refine_reports([self.report], self.cfg)
        self.assertEqual(self.report['items'][0]['title'], keep['title'])
        self.assertEqual(len(self.report['items']), 1)
        self.assertEqual(self.report['new_count'], 1)
        self.assertEqual(self.report['generated_at'], before)
        self.assertEqual(self.report['sources'][0]['shown'], 1)

    def test_translation_removes_noise_and_preserves_repo_prefix(self):
        prefix, parts = translation_parts('owner/repo_name — AI tool runs locally. Use the CLI. https://t.co/abc #AI #Tools 🚀')
        self.assertEqual(prefix, 'owner/repo_name — ')
        self.assertEqual(len(parts), 2)
        self.assertNotIn('https', ' '.join(parts))
        self.assertNotIn('#', ' '.join(parts))
        self.assertNotIn('🚀', ' '.join(parts))

    def test_repetition_and_decoder_collapse_are_rejected(self):
        self.assertFalse(valid_translation('道' * 30, 'A new AI tool for travel'))
        self.assertFalse(valid_translation('上市 ' * 20, 'New startup listed on a SaaS marketplace'))
        self.assertFalse(valid_translation('IMF指出', 'IMF says AI investment is keeping global growth near 3%'))
        self.assertTrue(valid_translation('新的 AI 工具支持本地部署', 'New AI tool runs locally'))

    def test_glossary_is_context_bound_and_preserves_numbers(self):
        translated = polish_translation('双子座 4 的人工智能特工使用 256k 代币', 'Gemini 4 AI agent supports 256k tokens')
        self.assertEqual(translated, 'Gemini 4 的AI 智能体使用 256k Token')
        self.assertEqual(polish_translation('双子座', 'Gemini constellation'), 'Gemini')
        self.assertEqual(polish_translation('代币', 'crypto coin'), '代币')

    def test_old_translations_are_regenerated(self):
        self.report['items'][0]['title_zh'] = '老版本错误译文'
        model = Mock(return_value=['新的 AI 智能体'])
        localize_reports([self.report], model)
        model.assert_called_once()
        self.assertEqual(self.report['items'][0]['translation_version'], TRANSLATION_VERSION)

    def test_digest_deduplicates_and_preserves_sources(self):
        older = copy.deepcopy(self.report)
        older['generated_at'] = radar.iso(NOW - timedelta(hours=1))
        brief = build_brief([self.report, older], NOW)
        self.assertEqual(len(brief['items']), 1)
        self.assertEqual(brief['items'][0]['url'], self.report['items'][0]['url'])
        self.assertEqual(brief['date'], '2026-10-14')
        self.assertFalse(brief['stale'])

    def test_digest_excludes_old_posts_but_uses_discovery_time_for_repos(self):
        old = copy.deepcopy(self.report['items'][0])
        old['published_at'] = radar.iso(NOW - timedelta(days=5))
        self.report['items'] = [old]
        self.assertFalse(build_brief([self.report], NOW)['items'])
        old['platform'] = 'GitHub'
        self.assertEqual(len(build_brief([self.report], NOW)['items']), 1)

    def test_regular_collection_does_not_rewrite_daily_digest(self):
        archive = update_briefs([self.report], [], NOW, publish=True)
        later = update_briefs([self.report], archive, NOW + timedelta(hours=6))
        self.assertEqual(later, archive)
        tomorrow = update_briefs([self.report], archive, NOW + timedelta(days=1), publish=True)
        self.assertEqual(len(tomorrow), 2)
        self.assertTrue(tomorrow[0]['stale'])

    def test_preview_and_archive_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            preview = write_briefs(p, [self.report], [], NOW)
            self.assertEqual(preview['kind'], 'preview')
            self.assertIn('即时简报预览', (p / 'brief.html').read_text())
            archive = update_briefs([self.report], [], NOW, publish=True)
            write_briefs(p, [self.report], archive, NOW)
            self.assertTrue((p / 'brief-2026-10-14.html').exists())
            self.assertEqual(len(load_briefs(p / 'briefs.json')), 1)


if __name__ == '__main__': unittest.main()
