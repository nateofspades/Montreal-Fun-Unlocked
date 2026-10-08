import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StaticSiteContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        cls.css = (ROOT / "docs" / "styles.css").read_text(encoding="utf-8")

    def test_visible_source_label_and_machine_readable_exact_attribution(self):
        self.assertIn('content="Data: Has Things (hasthings.com)"', self.html)
        self.assertIn('>Data source: Has Things</a>', self.html)
        self.assertNotIn('>Data Source: Has Things</a>', self.html)
        self.assertNotIn('aria-label="Data: Has Things (hasthings.com)"', self.html)
        self.assertIn('href="./data/LICENSE.txt"', self.html)

    def test_removed_timezone_footer_sentence(self):
        self.assertNotIn("Times shown in America/Toronto.", self.html)

    def test_desktop_summary_and_collection_timestamp_stay_on_one_line(self):
        self.assertIn('class="hero-description"', self.html)
        self.assertRegex(self.css, r"\.hero-copy>\.hero-description\{[^}]*white-space:nowrap")
        self.assertRegex(self.css, r"\.updated\{[^}]*white-space:nowrap")

    def test_hero_uses_selected_wording(self):
        self.assertIn('<p class="eyebrow">WHAT’S HAPPENING IN MONTRÉAL</p>', self.html)
        self.assertIn('<h1>Find your next<br><em class="moment">Montréal outing.</em></h1>', self.html)
        self.assertRegex(self.css, r"\.moment\{[^}]*white-space:nowrap")

    def test_mtl_decoration_is_not_bottom_clipped_and_hides_before_overlap(self):
        self.assertRegex(self.css, r"\.hero:after\{[^}]*bottom:96px")
        self.assertIn("@media(max-width:1100px){.hero:after{display:none}", self.css)

    def test_static_ui_uses_event_wording(self):
        self.assertIn("Loading events…", self.html)
        self.assertIn("Load more events", self.html)


if __name__ == "__main__":
    unittest.main()
