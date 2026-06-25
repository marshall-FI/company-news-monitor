import { getSources } from "../../../lib/news";

export const dynamic = "force-dynamic";

export async function GET() {
  return Response.json({
    sources: getSources(),
    categories: ["Fintech Blogs", "Big Tech Blogs"],
  });
}
