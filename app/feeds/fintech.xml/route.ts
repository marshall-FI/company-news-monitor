import { getArticles } from "../../../lib/news";
import { renderRssFeed } from "../../../lib/rss";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const payload = await getArticles("Fintech Blogs");
  const origin = new URL(request.url).origin;
  const xml = renderRssFeed(
    "Company News Monitor - Fintech Blogs",
    "Fintech company news from the employer watchlist.",
    origin,
    payload.articles
  );

  return new Response(xml, {
    headers: {
      "content-type": "application/rss+xml; charset=utf-8",
      "cache-control": "public, max-age=300",
    },
  });
}
