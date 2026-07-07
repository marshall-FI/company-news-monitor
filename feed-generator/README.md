# Feed Generator

This folder contains the GitHub Actions-ready version of the local FreshRSS/generator setup.

It converts the employer watchlist into static artifacts for the site:

- `public/generated/articles.json`
- `public/generated/sources.json`
- `public/generated/feeds/all.xml`
- `public/generated/feeds/fintech.xml`
- `public/generated/feeds/big-tech.xml`

The Codex Site reads `public/generated/articles.json` first. If that file does not exist during local development, the site falls back to the existing live `/api/articles` fetch path.

## Run Locally

From the site repo:

```powershell
python feed-generator/generate.py
```

Then start the site:

```powershell
npm run dev
```

## GitHub Actions

The workflow at `.github/workflows/generate-feeds.yml` runs hourly and can also be triggered manually with `workflow_dispatch`.

The workflow:

1. Checks out the repo.
2. Runs `python feed-generator/generate.py`.
3. Writes generated JSON and RSS files to `public/generated/`.
4. Commits the changed generated artifacts back to the repo.

## Source Policy

Each source is tried in this order:

1. Official RSS or official page.
2. Rendered HTML when a source is JavaScript-heavy or returns too few static items.
3. Official-domain Google News RSS fallback, scoped with `site:`.
4. Last-good generated articles already present in `public/generated/articles.json`.

The generator does not use broad company-name news searches or random syndicated fallback sites.

## Rendered Sources

GitHub Actions installs Playwright and Chromium so the generator can render JavaScript-heavy pages before falling back to Google News. This is used for sources such as Q4-style investor relations pages and client-rendered blogs.

Local runs without Playwright still work: rendered sources skip to their next configured fallback.

## Health Semantics

If a source fails but previous articles exist, the source stays usable and is marked with a `STALE:` message. The reader can continue showing the last good entries instead of surfacing a broken feed during transient outages.

If a source fails and has no previous articles, it is marked as an issue.
