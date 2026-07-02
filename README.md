# Company News Monitor

Shareable company-news reader for the employer watchlist. The site presents FreshRSS-style cards and RSS endpoints for two groups:

- Fintech Blogs
- Big Tech Blogs

The current architecture keeps the reader UI separate from feed generation:

- `feed-generator/` converts official RSS, official web pages, and official-domain fallback feeds into normalized data.
- `public/generated/` stores generated JSON and RSS files for the site.
- `.github/workflows/generate-feeds.yml` runs the generator hourly on GitHub Actions and commits updated artifacts.
- `app/` renders the site. It reads `public/generated/articles.json` first and falls back to the live `/api/articles` path when generated data is missing locally.

## Local Setup

```powershell
npm install
npm run feeds:generate
npm run dev
```

Production build check:

```powershell
npm run build
```

## Generated Artifacts

Run:

```powershell
npm run feeds:generate
```

Outputs:

- `public/generated/articles.json`
- `public/generated/sources.json`
- `public/generated/feeds/all.xml`
- `public/generated/feeds/fintech.xml`
- `public/generated/feeds/big-tech.xml`

The generated RSS URLs on the deployed site are:

- `/generated/feeds/all.xml`
- `/generated/feeds/fintech.xml`
- `/generated/feeds/big-tech.xml`

## GitHub Actions

After this repo is pushed to GitHub, the workflow at `.github/workflows/generate-feeds.yml` can run manually or hourly.

Required repository setting:

- Actions must have permission to write repository contents so the workflow can commit updated `public/generated` files.

The workflow uses only Python standard library modules, so there are no Python dependencies to install.

## Source Policy

The generator keeps the sourcing rules conservative:

1. Official RSS or official page.
2. Official-domain Google News RSS fallback, scoped with `site:`.
3. Last-good generated articles already committed in `public/generated/articles.json`.

It does not use broad company-name searches or random third-party news fallbacks.

## FreshRSS Compatibility

FreshRSS can subscribe to the generated RSS endpoints directly once the site is deployed:

- `https://company-news-monitor.future-inves-2634.chatgpt-team.site/generated/feeds/all.xml`
- `https://company-news-monitor.future-inves-2634.chatgpt-team.site/generated/feeds/fintech.xml`
- `https://company-news-monitor.future-inves-2634.chatgpt-team.site/generated/feeds/big-tech.xml`

## Useful Commands

- `npm run dev`: start local development
- `npm run build`: verify the vinext build output
- `npm run feeds:generate`: regenerate static article JSON and RSS feeds
- `npm run db:generate`: generate Drizzle migrations after schema changes
