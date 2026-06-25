import { sources, type NewsSource, type SourceCategory } from "./sources";

export type Article = {
  id: string;
  title: string;
  link: string;
  summary: string;
  publishedAt: string;
  sourceId: string;
  sourceName: string;
  company: string;
  category: SourceCategory;
  sourceKind: NewsSource["kind"];
};

export type SourceStatus = {
  sourceId: string;
  sourceName: string;
  category: SourceCategory;
  kind: NewsSource["kind"];
  ok: boolean;
  itemCount: number;
  message: string;
};

export type ArticleResponse = {
  generatedAt: string;
  articles: Article[];
  statuses: SourceStatus[];
  sourceCount: number;
};

const MAX_ITEMS_PER_SOURCE = 18;
const REQUEST_TIMEOUT_MS = 20000;
const SOURCE_CONCURRENCY = 4;

function decodeEntities(value: string) {
  return value
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&#x27;/g, "'")
    .replace(/&#x2F;/g, "/");
}

function stripTags(value: string) {
  return decodeEntities(value.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim());
}

function getTag(block: string, tag: string) {
  const match = block.match(new RegExp(`<${tag}(?:\\s[^>]*)?>([\\s\\S]*?)<\\/${tag}>`, "i"));
  return match ? decodeEntities(match[1]).trim() : "";
}

function getAtomLink(block: string) {
  const alternate = block.match(/<link\b(?=[^>]*rel=["']alternate["'])(?=[^>]*href=["']([^"']+)["'])[^>]*>/i);
  const first = alternate ?? block.match(/<link\b(?=[^>]*href=["']([^"']+)["'])[^>]*>/i);
  return first ? decodeEntities(first[1]).trim() : "";
}

function normalizeArticleUrl(url: string) {
  try {
    const parsed = new URL(url);
    parsed.hash = "";
    parsed.searchParams.delete("utm_source");
    parsed.searchParams.delete("utm_medium");
    parsed.searchParams.delete("utm_campaign");
    parsed.searchParams.delete("utm_term");
    parsed.searchParams.delete("utm_content");
    return `${parsed.hostname.replace(/^www\./, "")}${parsed.pathname.replace(/\/$/, "")}`;
  } catch {
    return url;
  }
}

function normalizeTitle(title: string) {
  return title
    .toLowerCase()
    .replace(/\s+[-|]\s+[^-|]+$/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\b(the|a|an|and|or|to|of|in|for|with|on|at|by|from|as|is|are|be|this|that|new|inc|ltd|llc|corp|company|holdings|technologies)\b/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function toIsoDate(value: string) {
  if (!value) {
    return new Date().toISOString();
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? new Date().toISOString() : date.toISOString();
}

function hashId(value: string) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash << 5) - hash + value.charCodeAt(index);
    hash |= 0;
  }
  return Math.abs(hash).toString(36);
}

async function fetchText(url: string) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(url, {
      signal: controller.signal,
      headers: {
        "user-agent": "Mozilla/5.0 (compatible; CompanyNewsMonitor/1.0; +https://openai.com)",
        "accept-language": "en-US,en;q=0.9",
        accept: "application/rss+xml, application/atom+xml, text/xml, text/html;q=0.9, */*;q=0.8",
      },
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    return await response.text();
  } finally {
    clearTimeout(timeout);
  }
}

function articleFromParts(source: NewsSource, title: string, link: string, summary: string, publishedAt: string): Article | null {
  const cleanTitle = stripTags(title);
  if (!cleanTitle || !link) {
    return null;
  }

  let absoluteLink = link;
  try {
    absoluteLink = new URL(link, source.sourceUrl).toString();
  } catch {
    return null;
  }

  return {
    id: `${source.id}-${hashId(absoluteLink || cleanTitle)}`,
    title: cleanTitle,
    link: absoluteLink,
    summary: stripTags(summary).slice(0, 320),
    publishedAt: toIsoDate(publishedAt),
    sourceId: source.id,
    sourceName: source.name,
    company: source.company,
    category: source.category,
    sourceKind: source.kind,
  };
}

function parseRss(source: NewsSource, xml: string) {
  const itemBlocks = [...xml.matchAll(/<item\b[\s\S]*?<\/item>/gi)].map((match) => match[0]);
  const atomBlocks = [...xml.matchAll(/<entry\b[\s\S]*?<\/entry>/gi)].map((match) => match[0]);
  const blocks = itemBlocks.length > 0 ? itemBlocks : atomBlocks;

  return blocks
    .map((block) => {
      const isAtom = block.toLowerCase().startsWith("<entry");
      const title = getTag(block, "title");
      const link = isAtom ? getAtomLink(block) : getTag(block, "link") || getTag(block, "guid");
      const summary = getTag(block, "description") || getTag(block, "summary") || getTag(block, "content:encoded");
      const date = getTag(block, "pubDate") || getTag(block, "published") || getTag(block, "updated");
      return articleFromParts(source, title, link, summary, date);
    })
    .filter((article): article is Article => Boolean(article))
    .slice(0, MAX_ITEMS_PER_SOURCE);
}

function linkMatches(source: NewsSource, url: string) {
  const includePatterns = source.includePatterns ?? [];
  const excludePatterns = source.excludePatterns ?? [];
  const includeOk = includePatterns.length === 0 || includePatterns.some((pattern) => url.includes(pattern));
  const excludeOk = !excludePatterns.some((pattern) => url.includes(pattern));
  return includeOk && excludeOk;
}

function parseHtml(source: NewsSource, html: string, baseUrl = source.feedUrl) {
  const matches = [...html.matchAll(/<a\b[^>]*href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi)];
  const seen = new Set<string>();
  const articles: Article[] = [];

  function addArticle(rawHref: string, rawTitle: string) {
    let link: string;
    try {
      link = new URL(decodeEntities(rawHref), baseUrl).toString();
    } catch {
      return;
    }

    if (!linkMatches(source, link) || seen.has(normalizeArticleUrl(link))) {
      return;
    }

    const title = stripTags(rawTitle);
    const titleKey = title.toLowerCase();
    const navigationTitle =
      /[{};]/.test(title) ||
      /^(news|general news|europe news|other markets|initiatives|press|blog|learn more|read more)$/i.test(titleKey);

    if (navigationTitle || title.length < 8 || title.length > 180) {
      return;
    }

    seen.add(normalizeArticleUrl(link));
    const article = articleFromParts(source, title, link, "", new Date().toISOString());
    if (article) {
      articles.push(article);
    }
  }

  for (const match of matches) {
    addArticle(match[1], match[2]);
    if (articles.length >= MAX_ITEMS_PER_SOURCE) {
      break;
    }
  }

  if (articles.length < MAX_ITEMS_PER_SOURCE) {
    const scriptLinks = [...html.matchAll(/"(?:(?:url)|(?:href)|(?:slug)|(?:path))"\s*:\s*"([^"]+)"/gi)];
    const titleFields = [...html.matchAll(/"(?:(?:title)|(?:headline)|(?:name))"\s*:\s*"([^"]{8,180})"/gi)].map((match) =>
      decodeEntities(match[1])
    );

    let titleIndex = 0;
    for (const match of scriptLinks) {
      const fallbackTitle = match[1].split("/").filter(Boolean).pop()?.replace(/[-_]+/g, " ") ?? "";
      const candidateTitle = titleFields[titleIndex] ?? fallbackTitle;
      titleIndex += 1;
      addArticle(match[1].replace(/\\\//g, "/"), candidateTitle);
      if (articles.length >= MAX_ITEMS_PER_SOURCE) {
        break;
      }
    }
  }

  return articles;
}

async function fetchSource(source: NewsSource) {
  try {
    const text = await fetchText(source.feedUrl);
    const articles = source.kind === "html" ? parseHtml(source, text) : parseRss(source, text);
    return {
      articles,
      status: {
        sourceId: source.id,
        sourceName: source.name,
        category: source.category,
        kind: source.kind,
        ok: articles.length > 0,
        itemCount: articles.length,
        message: articles.length > 0 ? "OK" : "No articles found",
      } satisfies SourceStatus,
    };
  } catch (error) {
    if (source.kind === "google_news") {
      try {
        const fallbackText = await fetchText(source.sourceUrl);
        const fallbackArticles = parseHtml(source, fallbackText, source.sourceUrl);
        if (fallbackArticles.length > 0) {
          return {
            articles: fallbackArticles,
            status: {
              sourceId: source.id,
              sourceName: source.name,
              category: source.category,
              kind: source.kind,
              ok: true,
              itemCount: fallbackArticles.length,
              message: "OK via source page fallback",
            } satisfies SourceStatus,
          };
        }
      } catch {
        // Keep the original failure below; it is usually more useful.
      }
    }

    return {
      articles: [],
      status: {
        sourceId: source.id,
        sourceName: source.name,
        category: source.category,
        kind: source.kind,
        ok: false,
        itemCount: 0,
        message: error instanceof Error ? error.message : "Fetch failed",
      } satisfies SourceStatus,
    };
  }
}

function dedupeArticles(articles: Article[]) {
  const seenUrl = new Set<string>();
  const seenTitle = new Set<string>();
  const deduped: Article[] = [];

  for (const article of articles.sort((a, b) => Date.parse(b.publishedAt) - Date.parse(a.publishedAt))) {
    const urlKey = normalizeArticleUrl(article.link);
    const titleKey = normalizeTitle(article.title);
    if (seenUrl.has(urlKey) || (titleKey.length > 18 && seenTitle.has(titleKey))) {
      continue;
    }
    seenUrl.add(urlKey);
    if (titleKey.length > 18) {
      seenTitle.add(titleKey);
    }
    deduped.push(article);
  }

  return deduped;
}

export async function getArticles(category?: SourceCategory): Promise<ArticleResponse> {
  const selectedSources = category ? sources.filter((source) => source.category === category) : sources;
  const results = [];
  for (let index = 0; index < selectedSources.length; index += SOURCE_CONCURRENCY) {
    const batch = selectedSources.slice(index, index + SOURCE_CONCURRENCY);
    results.push(...(await Promise.all(batch.map((source) => fetchSource(source)))));
  }
  const articles = dedupeArticles(results.flatMap((result) => result.articles)).slice(0, 240);

  return {
    generatedAt: new Date().toISOString(),
    articles,
    statuses: results.map((result) => result.status),
    sourceCount: selectedSources.length,
  };
}

export function getSources() {
  return sources;
}
