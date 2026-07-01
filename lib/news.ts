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
const MAX_TOTAL_ITEMS = 240;
const REQUEST_TIMEOUT_MS = 20000;
const SOURCE_CONCURRENCY = 4;
const MIN_ENGLISH_SIGNAL_WORDS = 1;

const ENGLISH_SIGNAL_WORDS = new Set([
  "a",
  "about",
  "after",
  "all",
  "and",
  "are",
  "as",
  "at",
  "bank",
  "be",
  "blog",
  "by",
  "company",
  "customer",
  "customers",
  "data",
  "for",
  "from",
  "global",
  "how",
  "in",
  "into",
  "is",
  "its",
  "launches",
  "announces",
  "announcement",
  "market",
  "new",
  "news",
  "of",
  "on",
  "payment",
  "payments",
  "platform",
  "press",
  "product",
  "quarter",
  "release",
  "releases",
  "results",
  "report",
  "says",
  "service",
  "shares",
  "technology",
  "the",
  "to",
  "updates",
  "with",
]);

const NON_ENGLISH_SIGNAL_WORDS = new Set([
  "acquisti",
  "angeboten",
  "asistente",
  "auf",
  "avec",
  "banco",
  "bancos",
  "blir",
  "clientes",
  "con",
  "das",
  "del",
  "der",
  "des",
  "die",
  "el",
  "empresas",
  "espana",
  "est",
  "exklusiv",
  "faire",
  "feiert",
  "fur",
  "fuer",
  "gli",
  "genom",
  "handlare",
  "heute",
  "inicia",
  "inizia",
  "les",
  "los",
  "kunden",
  "la",
  "lanza",
  "las",
  "mais",
  "mit",
  "nach",
  "nei",
  "nella",
  "norden",
  "nueva",
  "nuevo",
  "oggi",
  "para",
  "per",
  "pour",
  "risparmiare",
  "sulla",
  "sur",
  "startet",
  "tillgaengligt",
  "tillgangligt",
  "tusentals",
  "una",
  "und",
  "vier",
  "viertagige",
]);

const STRONG_NON_ENGLISH_SIGNAL_WORDS = new Set([
  "inicia",
  "inizia",
  "lanza",
  "startet",
  "angeboten",
  "exklusiv",
  "risparmiare",
]);

const NON_ENGLISH_PHRASES = [
  /\b(nota de prensa|sala de prensa|comunicado de prensa)\b/i,
  /\b(communique de presse|salle de presse)\b/i,
  /\b(comunicato stampa|sala stampa)\b/i,
  /\b(pressemitteilung|nachrichtenraum)\b/i,
  /\b(comunicado de imprensa|sala de imprensa)\b/i,
];

const NON_ENGLISH_LOCALE_SEGMENT =
  /\/(?:ar|br|cn|de|de-de|es|es-es|es-mx|fr|fr-fr|it|it-it|ja|jp|ko|kr|nl|pl|pt|pt-br|sv|zh|zh-cn|zh-tw)(?:\/|$)/i;

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
  const decoded = decodeEntities(decodeEntities(value));
  return decoded.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

function cleanWhitespace(value: string) {
  return value.replace(/\s+/g, " ").trim();
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

function unwrapKnownRedirect(url: string) {
  try {
    const parsed = new URL(url);
    if (parsed.hostname.endsWith("bing.com") && parsed.pathname.includes("/news/apiclick")) {
      const target = parsed.searchParams.get("url");
      if (target) {
        return new URL(target).toString();
      }
    }
  } catch {
    // Keep the original URL when redirect decoding fails.
  }
  return url;
}

function isProbablyEnglish(title: string, summary: string) {
  const text = cleanWhitespace(`${title} ${summary}`);
  if (!text) {
    return false;
  }

  if (NON_ENGLISH_PHRASES.some((pattern) => pattern.test(text))) {
    return false;
  }

  if (/[\u0400-\u04ff\u0590-\u05ff\u0600-\u06ff\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]/.test(text)) {
    return false;
  }

  const letters = text.match(/[A-Za-z]/g)?.length ?? 0;
  if (letters < 8) {
    return false;
  }

  const nonAsciiLetters = text.match(/[^\x00-\x7F]/g)?.length ?? 0;
  if (nonAsciiLetters / Math.max(text.length, 1) > 0.12) {
    return false;
  }

  const words = text.toLowerCase().match(/[a-z]{2,}/g) ?? [];
  if (words.some((word) => STRONG_NON_ENGLISH_SIGNAL_WORDS.has(word))) {
    return false;
  }

  const nonEnglishSignalCount = words.filter((word) => NON_ENGLISH_SIGNAL_WORDS.has(word)).length;
  if (nonEnglishSignalCount >= 2) {
    return false;
  }

  const signalCount = words.filter((word) => ENGLISH_SIGNAL_WORDS.has(word)).length;
  if (signalCount >= MIN_ENGLISH_SIGNAL_WORDS) {
    return true;
  }

  return letters / Math.max(text.length, 1) > 0.65 && words.length >= 5 && nonEnglishSignalCount === 0;
}

function isAllowedLanguageUrl(url: string) {
  try {
    const parsed = new URL(url);
    return !NON_ENGLISH_LOCALE_SEGMENT.test(parsed.pathname);
  } catch {
    return true;
  }
}

function sourceArea(source: NewsSource) {
  if (/investor|press|release|newsroom/i.test(source.name)) {
    return "company news, press activity, and investor-facing updates";
  }
  if (/blog/i.test(source.name)) {
    return "product, market, and company blog updates";
  }
  if (source.category === "Fintech Blogs") {
    return "payments, fintech, banking, and product strategy";
  }
  return "technology, platform, and corporate news";
}

function headlineFocus(title: string) {
  const normalized = title.toLowerCase();
  if (/\b(earnings|revenue|quarter|results|guidance|forecast|outlook)\b/.test(normalized)) {
    return "financial performance";
  }
  if (/\b(partner|partnership|collaborat|alliance|customer|client)\b/.test(normalized)) {
    return "partnerships and customer momentum";
  }
  if (/\b(launch|introduc|rolls out|unveil|announce|release|product|feature|platform)\b/.test(normalized)) {
    return "product and platform updates";
  }
  if (/\b(acquir|merger|deal|investment|funding|ipo|stock|shares|index|s&p|nasdaq)\b/.test(normalized)) {
    return "market activity and corporate transactions";
  }
  if (/\b(ai|data|cloud|chip|semiconductor|model|developer|security|technology)\b/.test(normalized)) {
    return "technology and infrastructure developments";
  }
  if (/\b(regulat|compliance|lawsuit|court|policy|license)\b/.test(normalized)) {
    return "regulatory and legal developments";
  }
  if (/\b(report|survey|study|research|insight|trend)\b/.test(normalized)) {
    return "research, trends, and market commentary";
  }
  if (/\b(appoint|hire|ceo|cfo|executive|board|leadership)\b/.test(normalized)) {
    return "leadership and organizational changes";
  }
  return "company and market developments";
}

function topicPhrase(source: NewsSource, title: string) {
  const companyPattern = new RegExp(`\\b${source.company.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "gi");
  const topic = cleanWhitespace(
    title
      .replace(companyPattern, "")
      .replace(/\s+[-|]\s+[^-|]+$/g, "")
      .replace(/\b(press release|newsroom|blog|latest news)\b/gi, "")
      .replace(/\s+/g, " ")
  );

  if (topic.length >= 24) {
    return topic.slice(0, 150);
  }

  return `${source.company} ${headlineFocus(title)}`;
}

function makeSummary(source: NewsSource, title: string, summary: string) {
  const cleanedSummary = cleanWhitespace(stripTags(summary))
    .replace(/\s*Continue reading\.?$/i, "")
    .replace(/\s*Read more\.?$/i, "")
    .trim();
  const cleanedTitle = cleanWhitespace(stripTags(title));

  const summaryKey = normalizeTitle(cleanedSummary);
  const titleKey = normalizeTitle(cleanedTitle);
  const summaryRepeatsTitle =
    summaryKey === titleKey || (summaryKey.length > 0 && titleKey.length > 0 && summaryKey.includes(titleKey));

  if (cleanedSummary.length >= 45 && !summaryRepeatsTitle) {
    return cleanedSummary.slice(0, 320);
  }

  if (cleanedTitle) {
    const focus = headlineFocus(cleanedTitle);
    const topic = topicPhrase(source, cleanedTitle);
    return `${source.name} flagged this as a ${focus} item for ${source.company}. It appears relevant to ${sourceArea(
      source
    )}, with the headline centered on ${topic}.`.slice(0, 320);
  }

  return `${source.name} published an update relevant to ${sourceArea(source)}. Open the original item for full details.`;
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
  const cleanSummary = stripTags(summary);
  if (!cleanTitle || !link || !isProbablyEnglish(cleanTitle, cleanSummary)) {
    return null;
  }

  let absoluteLink = link;
  try {
    absoluteLink = unwrapKnownRedirect(new URL(link, source.sourceUrl).toString());
  } catch {
    return null;
  }

  if (source.kind !== "bing_news" && source.kind !== "google_news" && !linkMatches(source, absoluteLink)) {
    return null;
  }

  if (!isAllowedLanguageUrl(absoluteLink)) {
    return null;
  }

  return {
    id: `${source.id}-${hashId(absoluteLink || cleanTitle)}`,
    title: cleanTitle,
    link: absoluteLink,
    summary: makeSummary(source, cleanTitle, cleanSummary),
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
    if (articles.length === 0 && source.alternateFeedUrls?.length) {
      for (const alternateFeedUrl of source.alternateFeedUrls) {
        try {
          const alternateText = await fetchText(alternateFeedUrl);
          const alternateArticles = parseRss({ ...source, kind: "google_news" }, alternateText);
          if (alternateArticles.length > 0) {
            return {
              articles: alternateArticles,
              status: {
                sourceId: source.id,
                sourceName: source.name,
                category: source.category,
                kind: source.kind,
                ok: true,
                itemCount: alternateArticles.length,
                message: "OK via alternate RSS",
              } satisfies SourceStatus,
            };
          }
        } catch {
          // Try the next alternate before marking the source unhealthy.
        }
      }
    }
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
    if (source.alternateFeedUrls?.length) {
      for (const alternateFeedUrl of source.alternateFeedUrls) {
        try {
          const alternateText = await fetchText(alternateFeedUrl);
          const alternateArticles = parseRss({ ...source, kind: "google_news" }, alternateText);
          if (alternateArticles.length > 0) {
            return {
              articles: alternateArticles,
              status: {
                sourceId: source.id,
                sourceName: source.name,
                category: source.category,
                kind: source.kind,
                ok: true,
                itemCount: alternateArticles.length,
                message: "OK via alternate RSS",
              } satisfies SourceStatus,
            };
          }
        } catch {
          // Try the next fallback path.
        }
      }
    }

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
  const sortedArticles = [...articles].sort((a, b) => Date.parse(b.publishedAt) - Date.parse(a.publishedAt));

  for (const article of sortedArticles) {
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

  const includedSources = new Set(deduped.map((article) => article.sourceId));
  for (const article of sortedArticles) {
    if (!includedSources.has(article.sourceId)) {
      deduped.push(article);
      includedSources.add(article.sourceId);
    }
  }

  return deduped.sort((a, b) => Date.parse(b.publishedAt) - Date.parse(a.publishedAt));
}

function limitArticlesWithSourceCoverage(articles: Article[], limit: number) {
  if (articles.length <= limit) {
    return articles;
  }

  const sourceRepresentatives = new Map<string, Article>();
  for (const article of articles) {
    if (!sourceRepresentatives.has(article.sourceId)) {
      sourceRepresentatives.set(article.sourceId, article);
    }
  }

  const selected = new Map<string, Article>();
  for (const article of sourceRepresentatives.values()) {
    selected.set(article.id, article);
  }

  for (const article of articles) {
    if (selected.size >= limit) {
      break;
    }
    selected.set(article.id, article);
  }

  return [...selected.values()].sort((a, b) => Date.parse(b.publishedAt) - Date.parse(a.publishedAt));
}

export async function getArticles(category?: SourceCategory): Promise<ArticleResponse> {
  const selectedSources = category ? sources.filter((source) => source.category === category) : sources;
  const results = [];
  for (let index = 0; index < selectedSources.length; index += SOURCE_CONCURRENCY) {
    const batch = selectedSources.slice(index, index + SOURCE_CONCURRENCY);
    results.push(...(await Promise.all(batch.map((source) => fetchSource(source)))));
  }
  const articles = limitArticlesWithSourceCoverage(dedupeArticles(results.flatMap((result) => result.articles)), MAX_TOTAL_ITEMS);

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
