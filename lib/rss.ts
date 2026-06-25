import type { Article } from "./news";

function escapeXml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

export function renderRssFeed(title: string, description: string, siteUrl: string, articles: Article[]) {
  const items = articles
    .map((article) => {
      const itemTitle = `${article.company}: ${article.title}`;
      const summary = article.summary || `${article.sourceName} item from ${article.category}.`;
      return `    <item>
      <title>${escapeXml(itemTitle)}</title>
      <link>${escapeXml(article.link)}</link>
      <guid isPermaLink="false">${escapeXml(article.id)}</guid>
      <pubDate>${new Date(article.publishedAt).toUTCString()}</pubDate>
      <description>${escapeXml(summary)}</description>
      <source>${escapeXml(article.sourceName)}</source>
    </item>`;
    })
    .join("\n");

  return `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>${escapeXml(title)}</title>
    <link>${escapeXml(siteUrl)}</link>
    <description>${escapeXml(description)}</description>
    <lastBuildDate>${new Date().toUTCString()}</lastBuildDate>
${items}
  </channel>
</rss>`;
}
