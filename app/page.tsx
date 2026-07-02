"use client";

import { useEffect, useMemo, useState } from "react";

type Category = "All" | "Fintech Blogs" | "Big Tech Blogs";

type Article = {
  id: string;
  title: string;
  link: string;
  summary: string;
  publishedAt: string;
  sourceName: string;
  company: string;
  category: "Fintech Blogs" | "Big Tech Blogs";
  sourceKind: "rss" | "google_news" | "bing_news" | "html";
};

type SourceStatus = {
  sourceId: string;
  sourceName: string;
  category: "Fintech Blogs" | "Big Tech Blogs";
  kind: "rss" | "google_news" | "bing_news" | "html";
  ok: boolean;
  itemCount: number;
  message: string;
};

type ArticleResponse = {
  generatedAt: string;
  articles: Article[];
  statuses: SourceStatus[];
  sourceCount: number;
};

const categories: Category[] = ["All", "Fintech Blogs", "Big Tech Blogs"];

function formatTime(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function kindLabel(kind: Article["sourceKind"]) {
  if (kind === "google_news") {
    return "Google";
  }
  if (kind === "bing_news") {
    return "Bing";
  }
  if (kind === "html") {
    return "Generated";
  }
  return "RSS";
}

export default function Home() {
  const [data, setData] = useState<ArticleResponse | null>(null);
  const [category, setCategory] = useState<Category>("All");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    fetch("/generated/articles.json", { cache: "no-store", signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Generated data unavailable: HTTP ${response.status}`);
        }
        return response.json() as Promise<ArticleResponse>;
      })
      .catch(() => {
        return fetch("/api/articles", { signal: controller.signal }).then((response) => {
          if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
          }
          return response.json() as Promise<ArticleResponse>;
        });
      })
      .then((response) => {
        setData(response);
        setError("");
      })
      .catch((fetchError) => {
        if (fetchError.name !== "AbortError") {
          setError(fetchError instanceof Error ? fetchError.message : "Unable to load articles");
        }
      })
      .finally(() => setLoading(false));

    return () => controller.abort();
  }, []);

  const articles = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return (data?.articles ?? []).filter((article) => {
      const categoryOk = category === "All" || article.category === category;
      const queryOk =
        normalizedQuery === "" ||
        `${article.title} ${article.summary} ${article.company} ${article.sourceName}`.toLowerCase().includes(normalizedQuery);
      return categoryOk && queryOk;
    });
  }, [category, data, query]);

  const stats = useMemo(() => {
    const statuses = data?.statuses ?? [];
    return {
      sourceCount: data?.sourceCount ?? 28,
      articleCount: data?.articles.length ?? 0,
      healthyCount: statuses.filter((status) => status.ok).length,
      issueCount: statuses.filter((status) => !status.ok).length,
    };
  }, [data]);

  const categoryCounts = useMemo(() => {
    const allArticles = data?.articles ?? [];
    return {
      All: allArticles.length,
      "Fintech Blogs": allArticles.filter((article) => article.category === "Fintech Blogs").length,
      "Big Tech Blogs": allArticles.filter((article) => article.category === "Big Tech Blogs").length,
    };
  }, [data]);

  return (
    <main className="min-h-screen bg-[#f6f7f4] text-[#161a1d]">
      <div className="mx-auto flex max-w-7xl flex-col gap-6 px-5 py-6 lg:px-8">
        <header className="flex flex-col gap-4 border-b border-[#d5d9d0] pb-5 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-sm font-semibold uppercase text-[#52746f]">Company News Monitor</p>
            <h1 className="mt-2 text-3xl font-semibold sm:text-4xl">Employer watchlist</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-[#5b665f]">
              English-only company updates from the same FreshRSS feed set, with short blurbs and links back to each original article.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <div className="metric">
              <span>{stats.sourceCount}</span>
              <p>Sources</p>
            </div>
            <div className="metric">
              <span>{stats.articleCount}</span>
              <p>Articles</p>
            </div>
            <div className="metric">
              <span>{stats.healthyCount}</span>
              <p>Healthy</p>
            </div>
            <div className="metric issue">
              <span>{stats.issueCount}</span>
              <p>Issues</p>
            </div>
          </div>
        </header>

        <section className="toolbar">
          <div className="tabs" aria-label="Category filters">
            {categories.map((item) => (
              <button
                className={category === item ? "tab active" : "tab"}
                key={item}
                onClick={() => setCategory(item)}
                type="button"
              >
                <span>{item}</span>
                <strong>{categoryCounts[item]}</strong>
              </button>
            ))}
          </div>
          <input
            aria-label="Search articles"
            className="search"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search company, source, headline"
            type="search"
            value={query}
          />
        </section>

        <section className="grid gap-6 lg:grid-cols-[1fr_320px]">
          <div className="article-list">
            {loading ? <div className="state-line">Loading current articles...</div> : null}
            {error ? <div className="state-line error-text">{error}</div> : null}
            {!loading && !error && articles.length === 0 ? <div className="state-line">No matching articles.</div> : null}

            {articles.map((article) => (
              <article className="article-card" key={article.id}>
                <div className="article-meta">
                  <span>{article.company}</span>
                  <span>{article.category}</span>
                  <span>{kindLabel(article.sourceKind)}</span>
                  <time>{formatTime(article.publishedAt)}</time>
                </div>
                <a className="article-title" href={article.link} rel="noreferrer" target="_blank">
                  {article.title}
                </a>
                {article.summary ? <p className="article-summary">{article.summary}</p> : null}
                <div className="article-footer">
                  <span>{article.sourceName}</span>
                  <a href={article.link} rel="noreferrer" target="_blank">
                    Open article
                  </a>
                </div>
              </article>
            ))}
          </div>

          <aside className="side-panel">
            <div className="panel-section">
              <h2>Feeds</h2>
              <a href="/generated/feeds/all.xml">All</a>
              <a href="/generated/feeds/fintech.xml">Fintech</a>
              <a href="/generated/feeds/big-tech.xml">Big Tech</a>
            </div>
            <div className="panel-section">
              <h2>Source Health</h2>
              <div className="status-list">
                {(data?.statuses ?? []).map((status) => (
                  <div className="status-row" key={status.sourceId}>
                    <span className={status.ok ? "dot ok" : "dot bad"} />
                    <div>
                      <strong>{status.sourceName}</strong>
                      <p>{status.ok ? `${status.itemCount} items` : status.message}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
            {data?.generatedAt ? <p className="updated">Updated {formatTime(data.generatedAt)}</p> : null}
          </aside>
        </section>
      </div>
    </main>
  );
}
