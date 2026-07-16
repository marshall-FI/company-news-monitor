from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


MODULE_PATH = Path(__file__).with_name("generate.py")
SPEC = importlib.util.spec_from_file_location("feed_generate", MODULE_PATH)
generate = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = generate
SPEC.loader.exec_module(generate)


class FeedGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = {
            "id": "example-news",
            "name": "Example News",
            "company": "Example",
            "category": "Fintech Blogs",
            "kind": "html",
            "source_url": "https://old.example.com/news/",
            "feed_url": "https://old.example.com/news/",
            "include_patterns": ["example.com/news/"],
            "exclude_patterns": ["/category/"],
        }

    def test_missing_date_is_not_replaced_with_generation_time(self) -> None:
        self.assertEqual("", generate.iso_date(""))
        article = generate.article_from_parts(self.source, "A useful company announcement", "/news/useful", "", "", "html")
        self.assertIsNotNone(article)
        self.assertEqual("", article.publishedAt)

    def test_html_uses_redirected_base_and_extracts_metadata(self) -> None:
        html = """
        <article class="news-card">
          <time datetime="2025-10-09">October 9, 2025</time>
          <a href="launch">Example launches a better payments product</a>
          <p>The company introduced a payments product designed to reduce checkout friction for growing merchants.</p>
        </article>
        """
        articles = generate.parse_html(self.source, html, "https://www.example.com/news/")
        self.assertEqual(1, len(articles))
        self.assertEqual("https://www.example.com/news/launch", articles[0].link)
        self.assertEqual("2025-10-09T00:00:00+00:00", articles[0].publishedAt)
        self.assertIn("reduce checkout friction", articles[0].summary)

    def test_dates_embedded_in_titles_are_removed_and_used(self) -> None:
        html = '<a href="/news/update">November 13, 2024 Example announces a major company update</a>'
        articles = generate.parse_html(self.source, html, "https://www.example.com/news/")
        self.assertEqual(1, len(articles))
        self.assertEqual("Example announces a major company update", articles[0].title)
        self.assertEqual("2024-11-13T00:00:00+00:00", articles[0].publishedAt)

    def test_source_slug_repairs_remove_duplicate_and_invalid_paths(self) -> None:
        source = {**self.source, "repair_slug_links": True}
        duplicate = "https://example.com/news/company-update/company-update"
        self.assertEqual("https://example.com/news/company-update", generate.repair_article_url(source, "Company update", duplicate))
        malformed = "https://example.com/news/europe'supdate:markets"
        self.assertEqual(
            "https://example.com/news/europe-investment-gap",
            generate.repair_article_url(source, "Europe's Investment Gap", malformed),
        )

    def test_duplicate_hostname_is_removed_from_article_path(self) -> None:
        malformed = "https://www.example.com/www.example.com/news/company-update/"
        self.assertEqual(
            "https://www.example.com/news/company-update",
            generate.repair_article_url(self.source, "Company update", malformed),
        )

    def test_event_date_in_title_is_not_used_as_publication_date(self) -> None:
        html = '<a href="/news/results">Company to announce results on August 5, 2099</a>'
        articles = generate.parse_html(self.source, html, "https://www.example.com/news/")
        self.assertEqual(1, len(articles))
        self.assertEqual("", articles[0].publishedAt)

    def test_source_items_are_sorted_newest_first(self) -> None:
        older = generate.article_from_parts(self.source, "Older company announcement", "/news/older", "", "2025-01-02", "html")
        newer = generate.article_from_parts(self.source, "Newer company announcement", "/news/newer", "", "2026-01-02", "html")
        assert older and newer
        self.assertEqual([newer.id, older.id], [article.id for article in generate.sort_source_articles([older, newer])])

    def test_official_link_wins_duplicate_title_over_google_news(self) -> None:
        official = generate.article_from_parts(
            self.source, "Company announces quarterly results", "/news/results", "", "2026-07-16T20:00:00Z", "rss"
        )
        google = generate.article_from_parts(
            self.source, "Company announces quarterly results", "https://news.google.com/articles/results", "", "2026-07-16T21:00:00Z", "google_news", apply_patterns=False
        )
        assert official and google
        selected = generate.sort_source_articles([google, official])
        self.assertEqual(1, len(selected))
        self.assertEqual(official.link, selected[0].link)

    def test_reader_metadata_extracts_date_and_summary(self) -> None:
        text = """Title: Company update | June 25, 2026

URL Source: https://example.com/news/update

Markdown Content:
The company introduced a new payments product designed to reduce checkout friction for growing merchants.
"""
        date, summary = generate.reader_metadata(text)
        self.assertEqual("2026-06-25T00:00:00+00:00", date)
        self.assertIn("reduce checkout friction", summary)

    def test_google_news_publisher_suffix_is_removed(self) -> None:
        article = generate.article_from_parts(
            self.source,
            "Example launches a better product - Example Financial, Inc.",
            "https://news.google.com/articles/example",
            "",
            "2026-07-16",
            "google_news",
            apply_patterns=False,
        )
        self.assertIsNotNone(article)
        self.assertEqual("Example launches a better product", article.title)

    def test_page_metadata_prefers_article_date_and_description(self) -> None:
        html = """
        <html><head>
          <meta property="article:published_time" content="2026-07-13T13:57:32Z">
          <meta property="og:description" content="A detailed description explaining the company announcement and why it matters to readers.">
          <link rel="canonical" href="https://example.com/news/announcement">
        </head></html>
        """
        date, summary, link = generate.page_metadata(html, "https://example.com/news/announcement")
        self.assertEqual("2026-07-13T13:57:32Z", date)
        self.assertIn("why it matters", summary)
        self.assertEqual("https://example.com/news/announcement", link)


if __name__ == "__main__":
    unittest.main()
