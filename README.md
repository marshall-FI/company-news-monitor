# Company News Monitor

Shareable company-news reader for the employer watchlist. The site presents FreshRSS-style cards and RSS endpoints for two groups:

- Fintech Blogs
- Big Tech Blogs

The current architecture keeps the reader UI separate from feed generation:

- `feed-generator/` converts official RSS, official web pages, and official-domain fallback feeds into normalized data.
- `public/generated/` stores generated JSON and RSS files for the site.
- `static-reader/` contains a no-build HTML reader for GitHub Pages.
- `.github/workflows/generate-feeds.yml` runs the generator hourly on GitHub Actions, commits updated artifacts, and deploys GitHub Pages.
- `app/` renders the site. It reads `public/generated/articles.json` first and falls back to the live `/api/articles` path when generated data is missing locally.

## Local Setup

```powershell
npm install
npm run feeds:generate
npm run feeds:pages
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

`npm run feeds:pages` builds a GitHub Pages artifact at `pages-dist/`. That folder is ignored locally because GitHub Actions rebuilds it for deployment.

The generated RSS URLs on the deployed site are:

- `/generated/feeds/all.xml`
- `/generated/feeds/fintech.xml`
- `/generated/feeds/big-tech.xml`

## GitHub Actions

After this repo is pushed to GitHub, the workflow at `.github/workflows/generate-feeds.yml` can run manually or hourly.

Required repository setting:

- Actions must have permission to write repository contents so the workflow can commit updated `public/generated` files.
- GitHub Pages must be enabled with the source set to `GitHub Actions`.

The workflow uses only Python standard library modules, so there are no Python dependencies to install.

The GitHub Pages deployment will serve:

- `/` - static reader
- `/generated/articles.json` - generated article data
- `/generated/sources.json` - generated source health data
- `/generated/feeds/all.xml` - all articles RSS
- `/generated/feeds/fintech.xml` - fintech RSS
- `/generated/feeds/big-tech.xml` - big tech RSS

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

If using GitHub Pages, replace the domain with the repository's Pages URL. Example:

- `https://OWNER.github.io/REPO/generated/feeds/all.xml`

## Useful Commands

- `npm run dev`: start local development
- `npm run build`: verify the vinext build output
- `npm run feeds:generate`: regenerate static article JSON and RSS feeds
- `npm run feeds:pages`: build the static GitHub Pages reader artifact
- `npm run db:generate`: generate Drizzle migrations after schema changes
