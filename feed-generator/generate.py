from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict, replace
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, parse_qs
from urllib.request import Request, urlopen
import hashlib
import json
import re
import time
import unicodedata
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
CONFIG_PATH = ROOT / "sources.json"
OUTPUT_DIR = REPO_ROOT / "public" / "generated"
FEEDS_DIR = OUTPUT_DIR / "feeds"
ARTICLES_PATH = OUTPUT_DIR / "articles.json"
SOURCES_PATH = OUTPUT_DIR / "sources.json"
MAX_ITEMS_PER_SOURCE = 18
MAX_TOTAL_ITEMS = 240
STALE_AFTER_HOURS = 72


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    link: str
    summary: str
    publishedAt: str
    sourceId: str
    sourceName: str
    company: str
    category: str
    sourceKind: str


@dataclass(frozen=True)
class SourceStatus:
    sourceId: str
    sourceName: str
    category: str
    kind: str
    ok: bool
    itemCount: int
    message: str


class HtmlNode:
    def __init__(self, tag: str, attrs: dict[str, str] | None = None, parent: HtmlNode | None = None) -> None:
        self.tag = tag
        self.attrs = attrs or {}
        self.parent = parent
        self.children: list[HtmlNode] = []
        self.contents: list[str | HtmlNode] = []

    def text(self) -> str:
        parts = [item.text() if isinstance(item, HtmlNode) else item for item in self.contents]
        return clean_text(" ".join(parts))

    def descendants(self, tags: set[str], limit: int = 24) -> list[HtmlNode]:
        matches: list[HtmlNode] = []
        pending = list(self.children)
        while pending and len(matches) < limit:
            node = pending.pop(0)
            if node.tag in tags:
                matches.append(node)
            pending[0:0] = node.children
        return matches


class HtmlTreeParser(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    SKIP_TEXT_TAGS = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = HtmlNode("document")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        node = HtmlNode(tag, {key.lower(): value or "" for key, value in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        self.stack[-1].contents.append(node)
        if tag not in self.VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag.lower():
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if not any(node.tag in self.SKIP_TEXT_TAGS for node in self.stack):
            self.stack[-1].contents.append(data)


def clean_text(value: str) -> str:
    decoded = unescape(unescape(value or ""))
    text = re.sub(r"<[^>]+>", " ", decoded)
    return re.sub(r"\s+", " ", text).strip()


def hash_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def fetch_document(url: str, timeout: int = 30) -> tuple[str, str]:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; CompanyNewsMonitor/1.0; +https://github.com)",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "application/rss+xml, application/atom+xml, text/xml, text/html;q=0.9, */*;q=0.8",
            "Cache-Control": "no-cache",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        text = response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
        return text, response.geturl()


def fetch_text(url: str, timeout: int = 30) -> str:
    return fetch_document(url, timeout)[0]


def iso_date(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
            for date_format in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%m/%d/%Y"):
                try:
                    parsed = datetime.strptime(value, date_format)
                    break
                except ValueError:
                    continue
            if parsed is None:
                return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def rss_date(value: str) -> str:
    parsed = iso_date(value)
    if not parsed:
        return ""
    return format_datetime(datetime.fromisoformat(parsed))


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.netloc.removeprefix('www.')}{parsed.path.rstrip('/')}"


def resolve_url(base_url: str, value: str) -> str:
    value = (value or "").strip()
    if not value or urlparse(value).scheme:
        return value
    return f"https://{value.lstrip('/')}" if "." in value.split("/", 1)[0] else urljoin(base_url, value)


def normalize_title(title: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        re.sub(
            r"\b(the|a|an|and|or|to|of|in|for|with|on|at|by|from|as|is|are|be|this|that|new|inc|ltd|llc|corp|company|holdings|technologies)\b",
            " ",
            re.sub(r"[^a-z0-9]+", " ", title.lower()),
        ),
    ).strip()


def unwrap_redirect(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc.endswith("bing.com") and "/news/apiclick" in parsed.path:
        target = parse_qs(parsed.query).get("url", [""])[0]
        return target or url
    return url


def repair_article_url(source: dict[str, Any], title: str, url: str) -> str:
    if not source.get("repair_slug_links"):
        return url
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[-1] == parts[-2]:
        parts.pop()
        return parsed._replace(path="/" + "/".join(parts), query="", fragment="").geturl()
    if re.search(r"['\":]", parsed.path):
        normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii").lower()
        normalized = re.sub(r"['’]s\b", "", normalized)
        slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
        base_path = parsed.path.split("/newsroom/", 1)[0] + "/newsroom/" if "/newsroom/" in parsed.path else parsed.path.rsplit("/", 1)[0] + "/"
        return parsed._replace(path=f"{base_path}{slug}", query="", fragment="").geturl()
    return url


NON_ENGLISH_WORDS = {
    "angeboten",
    "avec",
    "blir",
    "clientes",
    "genom",
    "handlare",
    "inicia",
    "inizia",
    "lanza",
    "norden",
    "para",
    "risparmiare",
    "startet",
    "tillgaengligt",
    "tillgangligt",
    "tusentals",
}


NAVIGATION_TITLES = {
    "annual meeting proxy statement",
    "analyst coverage",
    "blog",
    "blogs",
    "committee composition",
    "contact us",
    "end of day stock quote",
    "events presentations",
    "financials",
    "forms 8937",
    "governance documents",
    "investor contacts",
    "investor email alerts",
    "investor faqs",
    "leadership",
    "learn more",
    "main corporate site",
    "news",
    "overview",
    "press",
    "press releases",
    "privacy policy",
    "terms of service",
    "hire a partner",
    "free tools",
    "compare shopify",
    "affiliates",
    "partners",
    "sustainability",
    "shopify editions",
    "education",
    "newsletter",
    "nu videocast",
    "quarterly reports",
    "quick links",
    "read more",
    "resources",
    "search query",
    "sec filings",
    "site search",
    "stock chart",
    "stock information",
    "stock quote",
    "unsubscribe",
}


def is_english(title: str, summary: str) -> bool:
    text = clean_text(f"{title} {summary}")
    if not text or re.search(r"[\u0400-\u04ff\u0590-\u05ff\u0600-\u06ff\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", text):
        return False
    if len(re.findall(r"[A-Za-z]", text)) < 8:
        return False
    words = re.findall(r"[a-z]{2,}", text.lower())
    return not any(word in NON_ENGLISH_WORDS for word in words)


def link_matches(source: dict[str, Any], link: str, apply_patterns: bool = True) -> bool:
    if not apply_patterns:
        return True
    include_patterns = source.get("include_patterns") or []
    exclude_patterns = source.get("exclude_patterns") or []
    if include_patterns and not any(pattern in link for pattern in include_patterns):
        return False
    return not any(pattern in link for pattern in exclude_patterns)


def source_page_keys(source: dict[str, Any]) -> set[str]:
    urls = [source.get("source_url"), source.get("feed_url"), source.get("render_url")]
    return {normalize_url(url) for url in urls if url}


def title_allowed(source: dict[str, Any], title: str) -> bool:
    title_key = normalize_title(title)
    if title_key in NAVIGATION_TITLES:
        return False
    if re.match(r"^(news|press|blog|blogs|learn more|read more)$", title.strip(), re.I):
        return False

    exclude_title_patterns = source.get("exclude_title_patterns") or []
    if any(re.search(pattern, title, re.I) for pattern in exclude_title_patterns):
        return False

    include_title_patterns = source.get("include_title_patterns") or []
    return not include_title_patterns or any(re.search(pattern, title, re.I) for pattern in include_title_patterns)


def article_allowed(source: dict[str, Any], article: Article) -> bool:
    return title_allowed(source, article.title) and normalize_url(article.link) not in source_page_keys(source)


def make_summary(source: dict[str, Any], title: str, summary: str) -> str:
    cleaned = clean_text(summary)
    title_key = normalize_title(title)
    summary_key = normalize_title(cleaned)
    if len(cleaned) >= 45 and title_key and title_key not in summary_key:
        return cleaned[:320]
    return (
        f"{source['name']} published an update for {source['company']} relevant to "
        f"{source['category'].replace(' Blogs', '').lower()} news. Open the original item for full details."
    )


def article_from_parts(source: dict[str, Any], title: str, link: str, summary: str, published_at: str, source_kind: str, apply_patterns: bool = True) -> Article | None:
    clean_title = clean_text(title)
    clean_summary = clean_text(summary)
    if not clean_title or not link or not title_allowed(source, clean_title) or not is_english(clean_title, clean_summary):
        return None
    absolute_link = repair_article_url(source, clean_title, unwrap_redirect(urljoin(source["source_url"], link)))
    if normalize_url(absolute_link) in source_page_keys(source):
        return None
    if not link_matches(source, absolute_link, apply_patterns):
        return None
    return Article(
        id=f"{source['id']}-{hash_id(absolute_link or clean_title)}",
        title=clean_title,
        link=absolute_link,
        summary=make_summary(source, clean_title, clean_summary),
        publishedAt=iso_date(published_at),
        sourceId=source["id"],
        sourceName=source["name"],
        company=source["company"],
        category=source["category"],
        sourceKind=source_kind,
    )


def get_child_text(parent: ET.Element, name: str) -> str:
    child = parent.find(name)
    return child.text.strip() if child is not None and child.text else ""


def parse_rss(source: dict[str, Any], xml_text: str, source_kind: str, apply_patterns: bool = True) -> list[Article]:
    root = ET.fromstring(xml_text)
    items = root.findall(".//item")
    articles: list[Article] = []
    for item in items:
        article = article_from_parts(
            source,
            get_child_text(item, "title"),
            get_child_text(item, "link") or get_child_text(item, "guid"),
            get_child_text(item, "description"),
            get_child_text(item, "pubDate"),
            source_kind,
            apply_patterns,
        )
        if article:
            articles.append(article)
        if len(articles) >= MAX_ITEMS_PER_SOURCE:
            break
    return articles


DATE_PATTERNS = (
    re.compile(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b", re.I),
    re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},\s+\d{4}\b", re.I),
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:T[^\s\"'<]+)?\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b"),
)


def find_date(value: str) -> str:
    for pattern in DATE_PATTERNS:
        match = pattern.search(value or "")
        if match and iso_date(match.group(0)):
            return match.group(0)
    return ""


def strip_leading_date(value: str) -> str:
    cleaned = value.strip()
    for pattern in DATE_PATTERNS:
        match = pattern.match(cleaned)
        if match:
            return cleaned[match.end():].lstrip(" -|:")
    return cleaned


def article_container(anchor: HtmlNode) -> HtmlNode:
    parents: list[HtmlNode] = []
    parent = anchor.parent
    while parent is not None and len(parents) < 7:
        parents.append(parent)
        parent = parent.parent
    for parent in parents:
        classes = parent.attrs.get("class", "").lower()
        if parent.tag in {"article", "li"} or any(token in classes for token in ("article", "card", "post", "release", "story", "result", "item")):
            return parent
    for parent in parents:
        text = parent.text()
        if find_date(text) and len(text) <= 1200:
            return parent
    for parent in parents:
        if parent.descendants({"p"}, limit=1) and len(parent.text()) <= 1200:
            return parent
    return parents[0] if parents else anchor


def container_metadata(container: HtmlNode, raw_title: str) -> tuple[str, str]:
    date = ""
    time_elements = container.descendants({"time"}, limit=1)
    if time_elements:
        date = time_elements[0].attrs.get("datetime") or time_elements[0].text()
    if not iso_date(date):
        date = find_date(container.text()) or find_date(raw_title)

    summary = ""
    title_key = normalize_title(strip_leading_date(raw_title))
    for element in container.descendants({"p", "div", "span"}, limit=24):
        text = element.text()
        text_key = normalize_title(text)
        if 45 <= len(text) <= 600 and title_key not in text_key:
            summary = text
            break
    return summary, date


def parse_embedded_articles(source: dict[str, Any], html: str, base_url: str) -> list[Article]:
    articles: list[Article] = []
    seen: set[str] = set()

    def walk(value: Any) -> None:
        if len(articles) >= MAX_ITEMS_PER_SOURCE:
            return
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if not isinstance(value, dict):
            return

        fields = value.get("fields") if isinstance(value.get("fields"), dict) else {}
        seo = value.get("seo") if isinstance(value.get("seo"), dict) else {}
        seo_fields = seo.get("fields") if isinstance(seo.get("fields"), dict) else {}
        combined = {**value, **fields, **seo_fields}
        title = str(combined.get("headline") or combined.get("title") or combined.get("seoTitle") or "")
        published_at = str(
            combined.get("datePublished")
            or combined.get("publishDate")
            or combined.get("publishedAt")
            or combined.get("published_date")
            or ""
        )
        canonical = str(combined.get("canonicalUrl") or combined.get("url") or combined.get("href") or "")
        slug = str(combined.get("slug") or "")
        canonical_is_usable = bool(canonical) and not re.search(r"['\":]", urlparse(canonical).path)
        canonical_is_resolvable = bool(urlparse(canonical).scheme or canonical.startswith("/") or "." in canonical.split("/", 1)[0])
        link = canonical if canonical_is_usable and canonical_is_resolvable else slug
        if link and not urlparse(link).scheme:
            link = resolve_url(base_url.rstrip("/") + "/", link)
        summary = str(combined.get("seoDescription") or combined.get("description") or combined.get("summary") or combined.get("excerpt") or "")

        if title and published_at and link:
            article = article_from_parts(source, title, link, summary, published_at, "html", True)
            if article and normalize_url(article.link) not in seen:
                seen.add(normalize_url(article.link))
                articles.append(article)

        for item in value.values():
            if isinstance(item, (dict, list)):
                walk(item)

    script_pattern = re.compile(r"<script\b[^>]*(?:type=[\"']application/(?:ld\+json|json)[\"']|id=[\"']__NEXT_DATA__[\"'])[^>]*>(.*?)</script>", re.I | re.S)
    for match in script_pattern.finditer(html):
        try:
            walk(json.loads(unescape(match.group(1))))
        except json.JSONDecodeError:
            continue
    return articles


def parse_html(source: dict[str, Any], html: str, base_url: str | None = None) -> list[Article]:
    parser = HtmlTreeParser()
    parser.feed(html)
    effective_base = base_url or source["feed_url"]
    effective_source = {**source, "source_url": effective_base, "feed_url": effective_base}
    articles = parse_embedded_articles(effective_source, html, effective_base)
    seen = {normalize_url(article.link) for article in articles}
    for anchor in parser.root.descendants({"a"}, limit=10000):
        if len(articles) >= MAX_ITEMS_PER_SOURCE:
            break
        href = anchor.attrs.get("href", "")
        title = anchor.text()
        link = urljoin(effective_base, href)
        key = normalize_url(link)
        if key in seen or len(title) < 8 or len(title) > 180:
            continue
        summary, published_at = container_metadata(article_container(anchor), title)
        article = article_from_parts(effective_source, strip_leading_date(title), link, summary, published_at, "html", True)
        if article:
            seen.add(key)
            articles.append(article)
        if len(articles) >= MAX_ITEMS_PER_SOURCE:
            break
    return articles


def page_metadata(html: str, page_url: str) -> tuple[str, str, str]:
    parser = HtmlTreeParser()
    parser.feed(html)
    meta_values: dict[str, str] = {}
    canonical_url = ""
    for node in parser.root.descendants({"meta", "link"}, limit=500):
        key = (node.attrs.get("property") or node.attrs.get("name") or "").lower()
        if key and node.attrs.get("content"):
            meta_values[key] = node.attrs["content"]
        if node.tag == "link" and "canonical" in node.attrs.get("rel", "").lower():
            canonical_url = resolve_url(page_url, node.attrs.get("href", ""))

    published_at = next(
        (
            meta_values[key]
            for key in ("article:published_time", "datepublished", "date", "publishdate", "sailthru.date")
            if iso_date(meta_values.get(key, ""))
        ),
        "",
    )
    if not published_at:
        match = re.search(r'["\'](?:datePublished|publishDate|publishedAt)["\']\s*:\s*["\']([^"\']+)', html, re.I)
        published_at = match.group(1) if match and iso_date(match.group(1)) else ""
    if not published_at:
        time_elements = parser.root.descendants({"time"}, limit=20)
        for element in time_elements:
            candidate = element.attrs.get("datetime") or element.text()
            if iso_date(candidate):
                published_at = candidate
                break

    summary = next(
        (meta_values[key] for key in ("og:description", "twitter:description", "description") if len(clean_text(meta_values.get(key, ""))) >= 45),
        "",
    )
    if not summary:
        for paragraph in parser.root.descendants({"p"}, limit=80):
            candidate = paragraph.text()
            if 60 <= len(candidate) <= 800:
                summary = candidate
                break
    if not published_at:
        published_at = find_date(summary)
    return published_at, summary, canonical_url or page_url


def enrich_html_articles(source: dict[str, Any], articles: list[Article]) -> list[Article]:
    def enrich(article: Article) -> Article:
        generic_summary = article.summary.startswith(f"{source['name']} published an update") or len(article.summary) < 100
        if article.publishedAt and not generic_summary:
            return article
        try:
            html, final_url = fetch_document(article.link, int(source.get("detail_timeout_seconds", 10)))
            published_at, summary, canonical_url = page_metadata(html, final_url)
        except Exception:
            return article
        resolved_link = canonical_url if link_matches(source, canonical_url, True) else final_url
        resolved_link = repair_article_url(source, article.title, resolved_link)
        return replace(
            article,
            id=f"{source['id']}-{hash_id(resolved_link)}",
            link=resolved_link,
            summary=make_summary(source, article.title, summary or article.summary),
            publishedAt=iso_date(published_at) or article.publishedAt,
        )

    with ThreadPoolExecutor(max_workers=min(6, max(1, len(articles)))) as executor:
        return list(executor.map(enrich, articles))


def fetch_rendered_html(source: dict[str, Any]) -> tuple[str, str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed; rendered_html is unavailable") from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        page.goto(source.get("render_url") or source["feed_url"], wait_until="domcontentloaded", timeout=int(source.get("render_timeout_ms", 45000)))
        try:
            page.wait_for_load_state("networkidle", timeout=int(source.get("render_idle_timeout_ms", 15000)))
        except Exception:
            pass

        for selector in source.get("render_wait_selectors") or []:
            try:
                page.wait_for_selector(selector, timeout=int(source.get("render_selector_timeout_ms", 10000)))
                break
            except Exception:
                continue

        html = page.content()
        final_url = page.url
        browser.close()
        return html, final_url


def generator_order(source: dict[str, Any]) -> list[str]:
    primary = source.get("generator_type") or source["kind"]
    order = [primary, *(source.get("fallback_generator_types") or [])]
    if source.get("alternate_feed_urls"):
        order.append("official_google_news")

    unique: list[str] = []
    for item in order:
        if item not in unique:
            unique.append(item)
    return unique


def fetch_articles_for_strategy(source: dict[str, Any], strategy: str) -> tuple[list[Article], str, str]:
    if strategy == "rss":
        text = fetch_text(source["feed_url"], int(source.get("timeout_seconds", 30)))
        return parse_rss(source, text, "rss", apply_patterns=True), "rss", "OK"

    if strategy == "html":
        text, final_url = fetch_document(source["feed_url"], int(source.get("timeout_seconds", 30)))
        articles = parse_html(source, text, final_url)
        return enrich_html_articles(source, articles), "html", "OK"

    if strategy == "rendered_html":
        html, final_url = fetch_rendered_html(source)
        articles = parse_html(source, html, final_url)
        return enrich_html_articles(source, articles), "rendered_html", "OK via rendered HTML"

    if strategy == "official_google_news":
        last_error = "No official Google News fallback configured"
        for url in source.get("alternate_feed_urls") or []:
            try:
                text = fetch_text(url, int(source.get("timeout_seconds", 30)))
                articles = parse_rss(source, text, "google_news", apply_patterns=False)
                if articles:
                    return articles, "google_news", "OK via official fallback RSS"
            except Exception as exc:
                last_error = str(exc)
        raise RuntimeError(last_error)

    raise RuntimeError(f"Unknown generator strategy: {strategy}")


def load_previous() -> dict[str, list[Article]]:
    if not ARTICLES_PATH.exists():
        return {}
    try:
        payload = json.loads(ARTICLES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    previous: dict[str, list[Article]] = {}
    for item in payload.get("articles", []):
        try:
            article = Article(**item)
        except TypeError:
            continue
        previous.setdefault(article.sourceId, []).append(article)
    return previous


def fetch_source(source: dict[str, Any], previous: dict[str, list[Article]]) -> tuple[list[Article], SourceStatus]:
    last_error = "No articles found"
    minimum_items = int(source.get("minimum_items", 1))
    for strategy in generator_order(source):
        try:
            articles, kind, message = fetch_articles_for_strategy(source, strategy)
            if len(articles) >= minimum_items or (strategy == generator_order(source)[-1] and articles):
                return articles, SourceStatus(source["id"], source["name"], source["category"], kind, True, len(articles), message)
            last_error = f"{strategy} returned {len(articles)} item(s), below minimum {minimum_items}"
        except Exception as exc:  # noqa: BLE001 - generation should continue per source.
            last_error = f"{strategy}: {exc}"
        time.sleep(0.25)

    fallback = [article for article in previous.get(source["id"], []) if article_allowed(source, article)]
    if fallback:
        return fallback[:MAX_ITEMS_PER_SOURCE], SourceStatus(
            source["id"], source["name"], source["category"], source["kind"], True, len(fallback[:MAX_ITEMS_PER_SOURCE]), f"STALE: {last_error}"
        )
    return [], SourceStatus(source["id"], source["name"], source["category"], source["kind"], False, 0, last_error)


def dedupe_articles(articles: list[Article]) -> list[Article]:
    selected: list[Article] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for article in sorted(articles, key=lambda item: item.publishedAt or "", reverse=True):
        url_key = normalize_url(article.link)
        title_key = normalize_title(article.title)
        if url_key in seen_urls or (len(title_key) > 18 and title_key in seen_titles):
            continue
        seen_urls.add(url_key)
        if len(title_key) > 18:
            seen_titles.add(title_key)
        selected.append(article)
    return selected[:MAX_TOTAL_ITEMS]


def write_json(articles: list[Article], statuses: list[SourceStatus], source_count: int) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "articles": [asdict(article) for article in articles],
        "statuses": [asdict(status) for status in statuses],
        "sourceCount": source_count,
        "staleAfterHours": STALE_AFTER_HOURS,
    }
    ARTICLES_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    SOURCES_PATH.write_text(json.dumps({"generatedAt": payload["generatedAt"], "sources": [asdict(status) for status in statuses]}, indent=2), encoding="utf-8")


def xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def write_feed(path: Path, title: str, description: str, articles: list[Article]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "  <channel>",
        f"    <title>{xml_escape(title)}</title>",
        "    <link>https://company-news-monitor.future-inves-2634.chatgpt-team.site</link>",
        f"    <description>{xml_escape(description)}</description>",
        f"    <lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>",
    ]
    for article in articles:
        lines.extend(
            [
                "    <item>",
                f"      <title>{xml_escape(article.title)}</title>",
                f"      <link>{xml_escape(article.link)}</link>",
                f"      <guid isPermaLink=\"false\">{xml_escape(article.id)}</guid>",
                f"      <description>{xml_escape(article.summary)}</description>",
                f"      <source>{xml_escape(article.sourceName)}</source>",
                "    </item>",
            ]
        )
        if article.publishedAt:
            lines.insert(-2, f"      <pubDate>{rss_date(article.publishedAt)}</pubDate>")
    lines.extend(["  </channel>", "</rss>", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    sources = config["sources"]
    previous = load_previous()
    all_articles: list[Article] = []
    statuses: list[SourceStatus] = []

    for source in sources:
        articles, status = fetch_source(source, previous)
        all_articles.extend(articles)
        statuses.append(status)
        print(f"{source['id']}: {status.message} ({status.itemCount})")

    articles = dedupe_articles(all_articles)
    write_json(articles, statuses, len(sources))
    write_feed(FEEDS_DIR / "all.xml", "Company News Monitor - All", "Generated company news feed.", articles)
    write_feed(
        FEEDS_DIR / "fintech.xml",
        "Company News Monitor - Fintech",
        "Generated fintech company news feed.",
        [article for article in articles if article.category == "Fintech Blogs"],
    )
    write_feed(
        FEEDS_DIR / "big-tech.xml",
        "Company News Monitor - Big Tech",
        "Generated big tech company news feed.",
        [article for article in articles if article.category == "Big Tech Blogs"],
    )

    failures = [status for status in statuses if not status.ok]
    if failures:
        print(f"Completed with {len(failures)} source issue(s). Last-good cache may still keep the site usable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
