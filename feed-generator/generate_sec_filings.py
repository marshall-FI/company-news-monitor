from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import json
import os
import time


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
CONFIG_PATH = ROOT / "sec_companies.json"
OUTPUT_DIR = REPO_ROOT / "public" / "generated"
FEEDS_DIR = OUTPUT_DIR / "feeds"
FILINGS_PATH = OUTPUT_DIR / "sec-filings.json"
SEC_FEED_PATH = FEEDS_DIR / "sec-filings.xml"
MAX_FILINGS_PER_COMPANY = 12
MAX_TOTAL_FILINGS = 280
REQUEST_TIMEOUT_SECONDS = 30
SEC_BASE = "https://data.sec.gov/submissions"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
DEFAULT_USER_AGENT = "CompanyNewsMonitor/1.0 contact@example.com"


@dataclass(frozen=True)
class SecCompany:
    company: str
    display_name: str
    ticker: str
    cik: str
    category: str
    forms: list[str]
    filing_note: str = ""


@dataclass(frozen=True)
class SecFiling:
    id: str
    company: str
    displayName: str
    ticker: str
    cik: str
    category: str
    form: str
    accessionNumber: str
    filingDate: str
    reportDate: str
    acceptanceDateTime: str
    primaryDocument: str
    title: str
    summary: str
    filingUrl: str
    documentUrl: str
    note: str


@dataclass(frozen=True)
class SecStatus:
    company: str
    ticker: str
    cik: str
    ok: bool
    filingCount: int
    message: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def cik10(cik: str) -> str:
    return str(cik).zfill(10)


def sec_headers() -> dict[str, str]:
    return {
        "User-Agent": os.environ.get("SEC_USER_AGENT", DEFAULT_USER_AGENT),
        "Accept": "application/json, */*",
        "Accept-Encoding": "identity",
    }


def fetch_json(url: str) -> dict[str, Any]:
    request = Request(url, headers=sec_headers())
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def load_companies() -> list[SecCompany]:
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    companies: list[SecCompany] = []
    for item in payload["companies"]:
        companies.append(
            SecCompany(
                company=item["company"],
                display_name=item["display_name"],
                ticker=item["ticker"],
                cik=str(item["cik"]),
                category=item["category"],
                forms=item["forms"],
                filing_note=item.get("filing_note", ""),
            )
        )
    return companies


def filing_title(company: SecCompany, form: str, filing_date: str, report_date: str) -> str:
    period = report_date or filing_date
    return f"{company.company} {form} filed {filing_date}" if not period else f"{company.company} {form} for {period}"


def filing_summary(company: SecCompany, form: str, filing_date: str, report_date: str) -> str:
    if form in {"10-K", "20-F", "40-F"}:
        kind = "annual report"
    elif form == "10-Q":
        kind = "quarterly report"
    elif form in {"8-K", "6-K"}:
        kind = "current report"
    elif form == "DEF 14A":
        kind = "proxy statement"
    elif form in {"S-1", "F-1", "424B"}:
        kind = "registration or prospectus filing"
    else:
        kind = "SEC filing"

    report_part = f" for report date {report_date}" if report_date else ""
    note = f" {company.filing_note}" if company.filing_note else ""
    return f"{company.display_name} filed a {kind} ({form}) on {filing_date}{report_part}. Open the SEC filing for source documents.{note}"


def filing_urls(cik: str, accession_number: str, primary_document: str) -> tuple[str, str]:
    accession_path = accession_number.replace("-", "")
    cik_path = str(int(cik))
    filing_url = f"{ARCHIVES_BASE}/{cik_path}/{accession_path}/{accession_number}-index.html"
    document_url = f"{ARCHIVES_BASE}/{cik_path}/{accession_path}/{primary_document}" if primary_document else filing_url
    return filing_url, document_url


def parse_company_filings(company: SecCompany, payload: dict[str, Any]) -> list[SecFiling]:
    recent = payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accession_numbers = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    acceptance_times = recent.get("acceptanceDateTime", [])
    primary_documents = recent.get("primaryDocument", [])
    allowed_forms = set(company.forms)
    filings: list[SecFiling] = []

    for index, form in enumerate(forms):
        if form not in allowed_forms:
            continue

        accession_number = accession_numbers[index]
        filing_date = filing_dates[index]
        report_date = report_dates[index] if index < len(report_dates) else ""
        acceptance_time = acceptance_times[index] if index < len(acceptance_times) else ""
        primary_document = primary_documents[index] if index < len(primary_documents) else ""
        filing_url, document_url = filing_urls(company.cik, accession_number, primary_document)
        filings.append(
            SecFiling(
                id=f"{company.ticker}-{accession_number}",
                company=company.company,
                displayName=company.display_name,
                ticker=company.ticker,
                cik=cik10(company.cik),
                category=company.category,
                form=form,
                accessionNumber=accession_number,
                filingDate=filing_date,
                reportDate=report_date,
                acceptanceDateTime=acceptance_time,
                primaryDocument=primary_document,
                title=filing_title(company, form, filing_date, report_date),
                summary=filing_summary(company, form, filing_date, report_date),
                filingUrl=filing_url,
                documentUrl=document_url,
                note=company.filing_note,
            )
        )
        if len(filings) >= MAX_FILINGS_PER_COMPANY:
            break

    return filings


def generate_company(company: SecCompany) -> tuple[list[SecFiling], SecStatus]:
    url = f"{SEC_BASE}/CIK{cik10(company.cik)}.json"
    try:
        payload = fetch_json(url)
        filings = parse_company_filings(company, payload)
        return filings, SecStatus(
            company=company.company,
            ticker=company.ticker,
            cik=cik10(company.cik),
            ok=bool(filings),
            filingCount=len(filings),
            message=f"{len(filings)} filings" if filings else "No matching tracked forms found",
        )
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return [], SecStatus(
            company=company.company,
            ticker=company.ticker,
            cik=cik10(company.cik),
            ok=False,
            filingCount=0,
            message=str(exc),
        )


def rss_date(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return format_datetime(parsed.astimezone(timezone.utc))


def write_rss(filings: list[SecFiling]) -> None:
    items: list[str] = []
    for filing in filings[:MAX_TOTAL_FILINGS]:
        pub_date = filing.acceptanceDateTime or filing.filingDate
        items.append(
            f"""
    <item>
      <title>{escape(filing.title)}</title>
      <link>{escape(filing.filingUrl)}</link>
      <guid isPermaLink="false">{escape(filing.id)}</guid>
      <pubDate>{rss_date(pub_date)}</pubDate>
      <description>{escape(filing.summary)}</description>
    </item>""".rstrip()
        )

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Company News Monitor SEC Filings</title>
    <link>https://www.sec.gov/edgar/search/</link>
    <description>Recent SEC filings for companies tracked by Company News Monitor.</description>
    <lastBuildDate>{rss_date(utc_now())}</lastBuildDate>
{chr(10).join(items)}
  </channel>
</rss>
"""
    SEC_FEED_PATH.write_text(xml, encoding="utf-8")


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FEEDS_DIR.mkdir(parents=True, exist_ok=True)
    companies = load_companies()
    all_filings: list[SecFiling] = []
    statuses: list[SecStatus] = []

    for company in companies:
        filings, status = generate_company(company)
        all_filings.extend(filings)
        statuses.append(status)
        time.sleep(0.12)

    all_filings.sort(key=lambda filing: filing.acceptanceDateTime or filing.filingDate, reverse=True)
    all_filings = all_filings[:MAX_TOTAL_FILINGS]
    payload = {
        "generatedAt": utc_now(),
        "companyCount": len(companies),
        "filingCount": len(all_filings),
        "filings": [asdict(filing) for filing in all_filings],
        "statuses": [asdict(status) for status in statuses],
    }
    FILINGS_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    write_rss(all_filings)
    print(
        f"Generated SEC filings: companies={len(companies)} filings={len(all_filings)} "
        f"healthy={sum(1 for status in statuses if status.ok)} issues={sum(1 for status in statuses if not status.ok)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
