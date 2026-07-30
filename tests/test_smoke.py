import unittest

from daily_github_star_radar import clean_markdown, hour_range, md_escape_cell


class StarRadarSmokeTests(unittest.TestCase):
    def test_hour_range_uses_requested_length(self):
        from datetime import datetime, timezone

        end = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
        hours = hour_range(end, 3)
        self.assertEqual(len(hours), 3)
        self.assertEqual(hours[0].hour, 9)
        self.assertEqual(hours[-1].hour, 11)

    def test_clean_markdown_strips_common_markup(self):
        self.assertEqual(clean_markdown("`hello` [world](https://example.com) ![x](img.png)"), "hello world")

    def test_md_escape_cell_replaces_pipes_and_newlines(self):
        self.assertEqual(md_escape_cell("a|b\nc"), "a/b c")


if __name__ == "__main__":
    unittest.main()
