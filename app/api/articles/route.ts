import { getArticles } from "../../../lib/news";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const url = new URL(request.url);
  const categoryParam = url.searchParams.get("category");
  const category =
    categoryParam === "Fintech Blogs" || categoryParam === "Big Tech Blogs"
      ? categoryParam
      : undefined;

  const payload = await getArticles(category);
  return Response.json(payload, {
    headers: {
      "cache-control": "public, max-age=300",
    },
  });
}
