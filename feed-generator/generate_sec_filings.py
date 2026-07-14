from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen
import argparse
import gzip
import json
import os
import shutil
import subprocess
import time


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
CONFIG_PATH = ROOT / "sec_companies.json"
OUTPUT_DIR = REPO_ROOT / "public" / "generated"
FEEDS_DIR = OUTPUT_DIR / "feeds"
FILINGS_PATH = OUTPUT_DIR / "sec-filings.json"
SEC_FEED_PATH = FEEDS_DIR / "sec-filings.xml"
SNAPSHOT_DIR = ROOT / ".sec-cache"
MAX_FILINGS_PER_COMPANY = 12
MAX_TOTAL_FILINGS = 280
REQUEST_TIMEOUT_SECONDS = 30
SEC_BASE = "https://data.sec.gov/submissions"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
NASDAQ_BASE = "https://api.nasdaq.com/api/company"
EFTS_BASE = "https://efts.sec.gov/LATEST/search-index"
JINA_READER_BASE = "https://r.jina.ai/"
DEFAULT_USER_AGENT = "Marshall FI Company News Monitor marshall-FI@users.noreply.github.com"
NASDAQ_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36"
SEC_CACHE: dict[str, dict[str, Any]] = {}
NASDAQ_CACHE: dict[str, list[dict[str, Any]]] = {}


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
    dataSource: str


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


def sec_headers(url: str) -> dict[str, str]:
    user_agent = os.environ.get("SEC_USER_AGENT", DEFAULT_USER_AGENT)
    return {
        "User-Agent": user_agent,
        "From": os.environ.get("SEC_CONTACT_EMAIL", "marshall-FI@users.noreply.github.com"),
        "Accept": "application/json, */*",
        "Accept-Encoding": "gzip, deflate",
        "Host": urlparse(url).netloc,
    }


def fetch_json(
    url: str,
    headers: dict[str, str],
    attempts: int = 3,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    for attempt in range(attempts):
        try:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=timeout) as response:
                body = response.read()
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    body = gzip.decompress(body)
                return json.loads(body.decode("utf-8"))
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == attempts - 1:
                raise
        except (URLError, TimeoutError):
            if attempt == attempts - 1:
                raise
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Unable to fetch {url}")


def decode_wrapped_json(body: str) -> dict[str, Any]:
    json_start = body.find("{")
    if json_start < 0:
        raise json.JSONDecodeError("No JSON object found", body, 0)
    payload, _ = json.JSONDecoder().raw_decode(body[json_start:])
    return payload


def curl_text(url: str, headers: dict[str, str]) -> str:
    curl = shutil.which("curl") or shutil.which("curl.exe")
    if not curl:
        raise RuntimeError("curl is unavailable")
    command = [
        curl,
        "--fail",
        "--silent",
        "--show-error",
        "--location",
        "--connect-timeout",
        "10",
        "--max-time",
        "30",
        "--retry",
        "2",
        "--retry-all-errors",
    ]
    for name, value in headers.items():
        command.extend(["--header", f"{name}: {value}"])
    command.append(url)
    result = subprocess.run(command, capture_output=True, check=False, text=True, encoding="utf-8", timeout=100)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"curl exited with {result.returncode}")
    return result.stdout


def fetch_wrapped_json(url: str, headers: dict[str, str], attempts: int = 1) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=12) as response:
                body = response.read().decode("utf-8")
            return decode_wrapped_json(body)
        except HTTPError as exc:
            last_error = exc
            if exc.code not in {429, 500, 502, 503, 504} or attempt == attempts - 1:
                break
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
        time.sleep(1.5 * (attempt + 1))
    try:
        return decode_wrapped_json(curl_text(url, headers))
    except (OSError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as curl_error:
        raise RuntimeError(f"urllib: {last_error}; curl: {curl_error}") from curl_error


def form_allowed(form: str, allowed_forms: set[str]) -> bool:
    normalized = form.strip().upper()
    base = normalized.removesuffix("/A")
    return normalized in allowed_forms or base in allowed_forms or ("424B" in allowed_forms and base.startswith("424B"))


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
    elif form.startswith("F-6"):
        kind = "American depositary receipt registration"
    elif form.startswith("SCHEDULE 13G"):
        kind = "beneficial ownership report"
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
        if not form_allowed(form, allowed_forms):
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
                dataSource="SEC EDGAR",
            )
        )
        if len(filings) >= MAX_FILINGS_PER_COMPANY:
            break

    return filings


def fetch_sec_company(company: SecCompany) -> list[SecFiling]:
    url = f"{SEC_BASE}/CIK{cik10(company.cik)}.json"
    if company.cik not in SEC_CACHE:
        SEC_CACHE[company.cik] = fetch_json(url, sec_headers(url))
    return parse_company_filings(company, SEC_CACHE[company.cik])


def efts_forms(companies: list[SecCompany]) -> list[str]:
    forms = {form.upper() for company in companies for form in company.forms}
    if "424B" in forms:
        forms.remove("424B")
        forms.update({f"424B{number}" for number in range(1, 9)})
    return sorted(forms)


def parse_efts_hit(company: SecCompany, hit: dict[str, Any]) -> SecFiling | None:
    source = hit.get("_source") or {}
    form = str(source.get("form") or source.get("file_type") or "")
    ciks = {str(cik).zfill(10) for cik in source.get("ciks") or []}
    if cik10(company.cik) not in ciks or not form_allowed(form, {item.upper() for item in company.forms}):
        return None
    accession_number = str(source.get("adsh") or "")
    if not accession_number:
        return None
    hit_id = str(hit.get("_id") or "")
    primary_document = hit_id.split(":", 1)[1] if ":" in hit_id else ""
    filing_date = str(source.get("file_date") or "")
    report_date = str(source.get("period_ending") or "")
    filing_url, document_url = filing_urls(company.cik, accession_number, primary_document)
    return SecFiling(
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
        acceptanceDateTime=filing_date,
        primaryDocument=primary_document,
        title=filing_title(company, form, filing_date, report_date),
        summary=filing_summary(company, form, filing_date, report_date),
        filingUrl=filing_url,
        documentUrl=document_url,
        note=company.filing_note,
        dataSource="SEC EDGAR via read-through",
    )


def efts_url(companies: list[SecCompany], offset: int) -> str:
    today = datetime.now(timezone.utc).date()
    params = {
        "forms": ",".join(efts_forms(companies)),
        "startdt": (today - timedelta(days=400)).isoformat(),
        "enddt": today.isoformat(),
        "ciks": ",".join(sorted({cik10(company.cik) for company in companies})),
        "size": 100,
        "from": offset,
    }
    inner_url = f"{EFTS_BASE}?{urlencode(params)}"
    return f"{JINA_READER_BASE}{quote(inner_url, safe=':/?=')}"


def collect_efts_hits(
    companies: list[SecCompany],
    hits: list[dict[str, Any]],
    filings_by_company: dict[str, list[SecFiling]],
    seen: set[tuple[str, str]],
) -> None:
    for hit in hits:
        for company in companies:
            if len(filings_by_company[company.company]) >= MAX_FILINGS_PER_COMPANY:
                continue
            filing = parse_efts_hit(company, hit)
            if not filing or (company.company, filing.accessionNumber) in seen:
                continue
            filings_by_company[company.company].append(filing)
            seen.add((company.company, filing.accessionNumber))


def fetch_batched_efts(companies: list[SecCompany]) -> dict[str, list[SecFiling]]:
    filings_by_company: dict[str, list[SecFiling]] = {company.company: [] for company in companies}
    seen: set[tuple[str, str]] = set()
    offset = 0
    total = 1

    while offset < total and offset < 1000:
        url = efts_url(companies, offset)
        try:
            payload = fetch_wrapped_json(
                url,
                {"User-Agent": NASDAQ_USER_AGENT, "Accept": "text/plain, application/json, */*"},
            )
        except (HTTPError, URLError, TimeoutError, OSError, RuntimeError, json.JSONDecodeError):
            if offset > 0:
                break
            raise
        hits_block = payload.get("hits") or {}
        total_value = hits_block.get("total", 0)
        total = int(total_value.get("value", 0) if isinstance(total_value, dict) else total_value)
        hits = hits_block.get("hits") or []
        if not hits:
            break
        collect_efts_hits(companies, hits, filings_by_company, seen)
        if all(len(items) >= MAX_FILINGS_PER_COMPANY for items in filings_by_company.values()):
            break
        offset += len(hits)

    return filings_by_company


def snapshot_requests(companies: list[SecCompany]) -> list[tuple[str, str]]:
    requests: list[tuple[str, str]] = []
    for batch_index, start in enumerate(range(0, len(companies), 7)):
        company_batch = companies[start:start + 7]
        for offset in (0, 100, 200):
            requests.append((f"batch-{batch_index}-{offset}.txt", efts_url(company_batch, offset)))
    return requests


def load_efts_snapshots(companies: list[SecCompany]) -> dict[str, list[SecFiling]]:
    filings_by_company: dict[str, list[SecFiling]] = {company.company: [] for company in companies}
    if not SNAPSHOT_DIR.exists():
        return filings_by_company
    seen: set[tuple[str, str]] = set()
    for batch_index, start in enumerate(range(0, len(companies), 7)):
        company_batch = companies[start:start + 7]
        for offset in (0, 100, 200):
            path = SNAPSHOT_DIR / f"batch-{batch_index}-{offset}.txt"
            if not path.exists() or path.stat().st_size == 0:
                continue
            try:
                payload = decode_wrapped_json(path.read_text(encoding="utf-8"))
                hits = ((payload.get("hits") or {}).get("hits") or [])
                collect_efts_hits(company_batch, hits, filings_by_company, seen)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
    return filings_by_company


def nasdaq_headers(company: SecCompany) -> dict[str, str]:
    return {
        "User-Agent": NASDAQ_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.nasdaq.com",
        "Referer": f"https://www.nasdaq.com/market-activity/stocks/{company.ticker.lower()}/sec-filings",
    }


def parse_nasdaq_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return value


def nasdaq_reference(url: str) -> str:
    values = parse_qs(urlparse(url).query)
    return values.get("ref", [""])[0]


def fetch_nasdaq_company(company: SecCompany) -> list[SecFiling]:
    ticker = company.ticker.upper()
    if ticker not in NASDAQ_CACHE:
        query = urlencode(
            {
                "limit": 20,
                "sortColumn": "filed",
                "sortOrder": "desc",
                "IsQuoteMedia": "true",
            }
        )
        url = f"{NASDAQ_BASE}/{ticker}/sec-filings?{query}"
        payload = fetch_json(url, nasdaq_headers(company), attempts=1, timeout=20)
        data = payload.get("data") or {}
        rows = list(data.get("rows") or [])
        existing_urls = {
            str((row.get("view") or {}).get("htmlLink") or "")
            for row in rows
        }
        for latest in data.get("latest") or []:
            latest_url = str(latest.get("value") or "")
            if not latest_url or latest_url in existing_urls:
                continue
            query_values = parse_qs(urlparse(latest_url).query)
            rows.append(
                {
                    "companyName": query_values.get("companyName", [company.display_name])[0],
                    "formType": query_values.get("formType", [latest.get("label", "")])[0],
                    "filed": query_values.get("dateFiled", [""])[0],
                    "period": "",
                    "view": {"htmlLink": latest_url},
                }
            )
        NASDAQ_CACHE[ticker] = rows

    filings: list[SecFiling] = []
    allowed_forms = {form.upper() for form in company.forms}
    for row in NASDAQ_CACHE[ticker]:
        form = str(row.get("formType", "")).strip()
        if not form_allowed(form, allowed_forms):
            continue
        view = row.get("view") or {}
        filing_url = str(view.get("htmlLink") or view.get("docLink") or view.get("pdfLink") or "")
        if not filing_url:
            continue
        filing_date = parse_nasdaq_date(str(row.get("filed", "")))
        report_date = parse_nasdaq_date(str(row.get("period", "")))
        reference = nasdaq_reference(filing_url) or f"{ticker}-{form}-{filing_date}-{report_date}"
        filings.append(
            SecFiling(
                id=f"{ticker}-NASDAQ-{reference}",
                company=company.company,
                displayName=company.display_name,
                ticker=ticker,
                cik=cik10(company.cik),
                category=company.category,
                form=form,
                accessionNumber=f"QM-{reference}",
                filingDate=filing_date,
                reportDate=report_date,
                acceptanceDateTime=filing_date,
                primaryDocument="",
                title=filing_title(company, form, filing_date, report_date),
                summary=filing_summary(company, form, filing_date, report_date),
                filingUrl=filing_url,
                documentUrl=filing_url,
                note=company.filing_note,
                dataSource="Nasdaq / Quotemedia",
            )
        )
        if len(filings) >= MAX_FILINGS_PER_COMPANY:
            break
    return filings


def generate_company(
    company: SecCompany,
    batched_filings: dict[str, list[SecFiling]] | None = None,
    batch_error: str = "",
) -> tuple[list[SecFiling], SecStatus]:
    errors: list[str] = []
    filings = (batched_filings or {}).get(company.company, [])
    if filings:
        return filings, SecStatus(
            company=company.company,
            ticker=company.ticker,
            cik=cik10(company.cik),
            ok=True,
            filingCount=len(filings),
            message=f"{len(filings)} filings via SEC EDGAR read-through",
        )
    errors.append(f"SEC EDGAR read-through: {batch_error or 'no matching tracked forms'}")

    try:
        filings = fetch_sec_company(company)
        if filings:
            return filings, SecStatus(
                company=company.company,
                ticker=company.ticker,
                cik=cik10(company.cik),
                ok=True,
                filingCount=len(filings),
                message=f"{len(filings)} filings via SEC EDGAR",
            )
        errors.append("SEC EDGAR returned no matching tracked forms")
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        errors.append(f"SEC EDGAR: {exc}")

    try:
        filings = fetch_nasdaq_company(company)
        if not filings:
            raise ValueError("no matching tracked forms")
        return filings, SecStatus(
            company=company.company,
            ticker=company.ticker,
            cik=cik10(company.cik),
            ok=True,
            filingCount=len(filings),
            message=f"{len(filings)} filings via Nasdaq fallback",
        )
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"Nasdaq fallback: {exc}")
        return [], SecStatus(
            company=company.company,
            ticker=company.ticker,
            cik=cik10(company.cik),
            ok=False,
            filingCount=0,
            message="; ".join(errors),
        )


def load_previous_filings() -> dict[str, list[SecFiling]]:
    if not FILINGS_PATH.exists():
        return {}
    try:
        payload = json.loads(FILINGS_PATH.read_text(encoding="utf-8"))
        previous: dict[str, list[SecFiling]] = {}
        for item in payload.get("filings", []):
            item.setdefault("dataSource", "SEC EDGAR")
            filing = SecFiling(**item)
            previous.setdefault(filing.company, []).append(filing)
        return previous
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}


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
    previous_filings = load_previous_filings()
    batched_filings = load_efts_snapshots(companies)
    batch_errors: list[str] = []
    for start in range(0, len(companies), 7):
        company_batch = companies[start:start + 7]
        if all(batched_filings.get(company.company) for company in company_batch):
            continue
        try:
            fetched = fetch_batched_efts(company_batch)
            for company_name, filings in fetched.items():
                if filings:
                    batched_filings[company_name] = filings
        except (HTTPError, URLError, TimeoutError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            batch_errors.append(f"{company_batch[0].company}-{company_batch[-1].company}: {exc}")
    batch_error = "; ".join(batch_errors)

    for company in companies:
        filings, status = generate_company(company, batched_filings, batch_error)
        if not filings and company.company in previous_filings:
            filings = previous_filings[company.company]
            status = SecStatus(
                company=company.company,
                ticker=company.ticker,
                cik=cik10(company.cik),
                ok=False,
                filingCount=len(filings),
                message=f"STALE: retained {len(filings)} last-known-good filings; {status.message}",
            )
        all_filings.extend(filings)
        statuses.append(status)
        time.sleep(0.2)

    all_filings.sort(key=lambda filing: filing.acceptanceDateTime or filing.filingDate, reverse=True)
    deduplicated: dict[str, SecFiling] = {}
    for filing in all_filings:
        key = f"{filing.cik}:{filing.accessionNumber or filing.filingUrl}"
        deduplicated.setdefault(key, filing)
    all_filings = list(deduplicated.values())
    all_filings = all_filings[:MAX_TOTAL_FILINGS]
    if not all_filings:
        raise SystemExit("No SEC filings were generated; refusing to overwrite the last deployment with an empty feed.")
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-snapshot-urls", action="store_true")
    args = parser.parse_args()
    if args.print_snapshot_urls:
        for filename, url in snapshot_requests(load_companies()):
            print(f"{filename}\t{url}")
        raise SystemExit(0)
    raise SystemExit(main())
