"""Small, bounded public-PDF reading pilot. No paid APIs, models or OCR."""
from __future__ import annotations
import ipaddress
import json
import re
import socket
import sys
import time
from datetime import datetime, timezone, timedelta
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urldefrag

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
from updater import is_report_candidate

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "pdf_summaries.json"
MAX_REPORTS = 10
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_PAGES = 35
DEADLINE = 360
HEADERS = {"User-Agent": "WorldWatch-PublicReportReader/1.0", "Accept": "text/html,application/pdf"}
HEADING = re.compile(r"^\s*(executive summary|summary of findings|key findings|main findings|key messages|highlights|summary for policymakers|overview|key takeaways|significant events, trends, and issues)\s*[:.]?\s*$", re.I)
NEXT_HEADING = re.compile(r"^\s*(?:chapter\s+\d+.*|introduction|table of contents|contents|references|acknowledgements|acknowledgments|methodology|foreword|appendix)\s*$", re.I)
NON_REPORT = re.compile(r"\b(?:transcripts?|speeches|speech|webinars?|interviews?|podcasts?|videos?|remarks|press[-_ ]releases?|media[-_ ]releases?|presentation|slides|factsheet|fact sheet)\b", re.I)
PREFERRED = {"frontier_seed_ai_safety_2026": 104, "frontier_seed_ai_index_2026": 103, "frontier_seed_ifr_humanoid": 102, "frontier_seed_ifr_ai_robotics": 101, "strategic_seed_16": 100, "strategic_seed_9": 99, "strategic_seed_8": 98, "strategic_seed_10": 97}
OFFICIAL_PDF_HINTS = {
    "frontier_seed_ai_safety_2026": "https://internationalaisafetyreport.org/sites/default/files/2026-02/international-ai-safety-report-2026_1.pdf",
    "frontier_seed_ai_index_2026": "https://hai.stanford.edu/assets/files/ai_index_report_2026.pdf",
    "frontier_seed_stanford_setr_2026": "https://setr.stanford.edu/sites/default/files/2026-02/SETR2026_web-260219.pdf",
    "strategic_seed_16": "https://data.consilium.europa.eu/doc/document/ST-12388-2025-INIT/en/pdf",
    "strategic_seed_9": "https://iea.blob.core.windows.net/assets/19c60aa0-ee07-4a20-a138-33c967ac5008/GlobalCriticalMineralsOutlook2026.pdf",
    "strategic_seed_8": "https://thedocs.worldbank.org/en/doc/2ac43f46629cf90c2ea5b8929a343593-0050012026/commodity-markets-outlook-october-2026",
    "strategic_seed_10": "https://pubs.usgs.gov/publication/mcs2026",
}
STOP = set("the a an of in to and for on with by from report global world new edition annual quarterly update outlook research pdf summary".split())
PRIORITY = {"AI, LLMs & Robotics", "Investment & Financial Markets", "Commodities & Critical Minerals", "International Relations & Geopolitical Risk", "Geopolitics & Security", "Strategic Foresight & Emerging Industries"}

def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default

def words(text):
    return set(re.findall(r"[a-z]{3,}", text.lower())) - STOP

def public_url(url):
    p = urlsplit(url)
    if p.scheme not in ("https", "http") or not p.hostname or p.username or p.password or p.port not in (None, 80, 443):
        raise ValueError("unsupported URL")
    for result in socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(result[4][0]).is_global:
            raise ValueError("non-public address")
    return url

def download(url, deadline):
    """Follow at most four public redirects; bound bytes and total time."""
    with requests.Session() as session:
        session.trust_env = False
        for _ in range(5):
            if time.monotonic() >= deadline:
                raise TimeoutError("time limit")
            with session.get(public_url(url), headers=HEADERS, timeout=(6, 9), stream=True, allow_redirects=False) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers["Location"])
                    continue
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").lower()
                pdf_hint = "pdf" in content_type or urlsplit(url).path.lower().endswith(".pdf")
                limit = MAX_PDF_BYTES if pdf_hint else MAX_HTML_BYTES
                if int(response.headers.get("Content-Length", "0") or 0) > limit:
                    raise ValueError("file exceeds pilot size limit")
                buf = bytearray()
                for part in response.iter_content(65536):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("time limit")
                    buf.extend(part)
                    if buf[:5] == b"%PDF-":
                        limit = MAX_PDF_BYTES
                    if len(buf) > limit:
                        raise ValueError("file exceeds pilot size limit")
                return bytes(buf), url
        raise ValueError("too many redirects")

def pdf_links(html, url, title):
    soup = BeautifulSoup(html, "html.parser")
    options = {}
    for a in soup.find_all("a", href=True):
        link = urldefrag(urljoin(url, a["href"]))[0]
        if urlsplit(link).scheme not in ("http", "https"):
            continue
        label = a.get_text(" ", strip=True)
        blob = label + " " + link
        if NON_REPORT.search(re.sub(r"[_-]", " ", blob)):
            continue
        if not (re.search(r"\.pdf(?:[?#]|$)", link, re.I) or re.search(r"\b(?:download|full report|pdf)\b", label, re.I)):
            continue
        if re.search(r"\b(?:annex|appendix|methodology|presentation|slides|press release|infographic|summary only)\b", label, re.I):
            continue
        score = 4 * len(words(title) & words(blob)) + (4 if re.search(r"\b(?:full report|download report|download pdf)\b", label, re.I) else 0) + (3 if ".pdf" in link.lower() else 0)
        options[link] = max(score, options.get(link, -1))
    for meta in soup.find_all("meta"):
        if str(meta.get("name", "")).lower() in ("citation_pdf_url", "dc.identifier.pdf") and meta.get("content"):
            link = urljoin(url, meta["content"])
            if not NON_REPORT.search(re.sub(r"[_-]", " ", link)):
                options[link] = 100
    return sorted(options, key=lambda k: options[k], reverse=True)[:5]

def matches_title(title, opening):
    target = words(title)
    overlap = target & words(opening)
    if len(overlap) < min(2, len(target)) or not target or len(overlap) / len(target) < .40:
        return False
    years = re.findall(r"\b20\d{2}\b", title)
    return not years or any(y in opening for y in years)

def find_section(pages):
    for page_no, text in enumerate(pages, 1):
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not HEADING.fullmatch(line):
                continue
            rest = []
            for following in lines[i+1:]:
                if NEXT_HEADING.fullmatch(following):
                    break
                rest.append(following)
            body = re.sub(r"\s+", " ", " ".join(rest)).strip()
            # Skip table-of-contents hits and short navigation labels.
            if len(body.split()) < 45 or re.search(r"\.{4,}|\btable of contents\b", body[:180], re.I):
                continue
            # Short attributed excerpt, not a republished executive summary.
            # Keep the total quoted text below 25 words per report.
            sentence = re.split(r"(?<=[.!?])\s+", body, maxsplit=1)[0]
            excerpt = sentence.split()[:22]
            if len(excerpt) < 8:
                excerpt = body.split()[:22]
            return {"section": line.strip(), "page": page_no, "excerpt": " ".join(excerpt) + (" …" if len(sentence.split()) > 22 or len(excerpt) < len(body.split()) else "")}
    return None

def inspect_pdf(data, url, title, deadline):
    if NON_REPORT.search(re.sub(r"[_-]", " ", urlsplit(url).path)):
        raise ValueError("non-report PDF")
    if not data.startswith(b"%PDF-"):
        raise ValueError("not a PDF")
    reader = PdfReader(BytesIO(data), strict=False)
    if reader.is_encrypted:
        raise ValueError("encrypted PDF")
    pages = []
    for page in reader.pages[:MAX_PAGES]:
        if time.monotonic() >= deadline:
            raise TimeoutError("time limit")
        pages.append(page.extract_text() or "")
        if len(pages) == min(4, len(reader.pages)):
            opening = " ".join(pages)
            if re.search(r"(?im)^\s*(?:webinar transcript|transcript|interview transcript|speech transcript|podcast transcript)\s*[:.]?\s*$", opening):
                raise ValueError("transcript, not report")
            if not matches_title(title, opening):
                raise ValueError("PDF title or edition could not be matched")
        if len(pages) >= 4:
            section = find_section(pages)
            if section:
                return {"status": "ready", "pdf_url": url, **section}
    if not matches_title(title, " ".join(pages[:4])):
        raise ValueError("PDF title or edition could not be matched")
    section = find_section(pages)
    return {"status": "ready", "pdf_url": url, **section} if section else {"status": "pdf_only", "pdf_url": url}

def process(report, deadline):
    targets = list(dict.fromkeys([OFFICIAL_PDF_HINTS.get(str(report["id"])), report["url"]]))
    best = None
    errors = []
    for target in targets:
        if not target:
            continue
        try:
            data, url = download(target, deadline)
            if data.startswith(b"%PDF-"):
                result = inspect_pdf(data, url, report["title"], deadline)
                if result["status"] == "ready":
                    return result
                best = result
                continue
            for candidate in pdf_links(data, url, report["title"]):
                try:
                    blob, final = download(candidate, deadline)
                    result = inspect_pdf(blob, final, report["title"], deadline)
                    if result["status"] == "ready":
                        return result
                    best = result
                except Exception as exc:
                    errors.append(type(exc).__name__)
        except Exception as exc:
            errors.append(type(exc).__name__)
    return best or {"status": "unavailable", "reason": ", ".join(dict.fromkeys(errors))[:160] or "No matching public PDF"}

def priority(report, records, now):
    rid = str(report["id"])
    previous = records.get(rid, {})
    # First fill known public institutional sources; unattempted reports before ordinary retries.
    host = urlsplit(report["url"]).hostname or ""
    public = any(host == domain or host.endswith("." + domain) for domain in ("worldbank.org", "europa.eu", "iea.org", "usgs.gov", "imf.org", "sipri.org", "who.int", "unctad.org", "irena.org", "oecd.org", "un.org"))
    return (PREFERRED.get(rid, 0) if rid not in records else 0, not previous, public, report.get("category") in PRIORITY, report.get("date", ""))

def retry_due(entry, now):
    if not entry:
        return True
    if NON_REPORT.search(re.sub(r"[_-]", " ", entry.get("pdf_url", ""))):
        return True
    if entry.get("status") == "ready":
        return False
    try:
        return now - datetime.fromisoformat(entry["checked"]) >= timedelta(days=7)
    except (ValueError, KeyError, TypeError):
        return True


def eligible(report):
    return bool(report.get("id") and report.get("url") and report.get("title") and (not report.get("automatic") or report.get("series_id") or is_report_candidate(report["title"], report["url"])))

def self_test():
    filler = "Demand projections indicate changes across global commodity markets and new production plans in several regions. " * 6
    hit = find_section(["Contents\nExecutive summary\n3", "Executive summary\n" + filler])
    assert hit and hit["page"] == 2 and len(hit["excerpt"].split()) <= 23
    assert find_section(["Executive summary\n2"]) is None
    assert matches_title("Global Critical Minerals Outlook 2026", "Critical Minerals Outlook 2026")
    assert not matches_title("Global Critical Minerals Outlook 2026", "Critical Minerals Outlook 2025")
    assert pdf_links(b'<a href="/report.pdf">Download full report</a>', "https://example.org/reports", "Example report") == ["https://example.org/report.pdf"]
    assert pdf_links(b'<a href="/webinar-transcript.pdf">Download PDF</a>', "https://example.org/reports", "Example report") == []
    assert retry_due({"status":"unavailable", "checked":"2025-01-01T00:00:00+00:00"}, datetime.now(timezone.utc))
    print("PDF pilot unit checks passed.")

def main():
    self_test()
    if "--self-test" in sys.argv:
        return
    start = time.monotonic()
    deadline = start + DEADLINE
    today = datetime.now(timezone.utc)
    previous = read_json(OUTPUT, {"version": 1, "reports": {}})
    records = previous.get("reports", {})
    # Remove known transcript candidates immediately, even if no new extraction succeeds.
    records = {k: v for k, v in records.items() if not NON_REPORT.search(re.sub(r"[_-]", " ", v.get("pdf_url", "")))}
    curated = read_json(ROOT / "updates.json", {}).get("updates", [])
    auto = read_json(ROOT / "auto_updates.json", {}).get("updates", [])
    reports = sorted([r for r in curated + auto if eligible(r)], key=lambda r: priority(r, records, today), reverse=True)
    retries = [r for r in reports if str(r["id"]) in records and retry_due(records[str(r["id"])], today)]
    # Reserve up to two attempts for overdue failures; keep the first four priority reports first.
    reports = reports[:4] + retries[:2] + reports[4:]
    attempted = 0
    seen = set()
    for report in reports:
        if attempted >= MAX_REPORTS or time.monotonic() >= deadline:
            break
        rid = str(report["id"])
        if report["url"] in seen or not retry_due(records.get(rid), today):
            continue
        seen.add(report["url"])
        attempted += 1
        try:
            entry = process(report, min(deadline, time.monotonic() + 32))
        except Exception:
            entry = {"status": "unavailable"}
        records[rid] = {**entry, "report_url": report["url"], "checked": today.isoformat()}
        print(rid, entry["status"])
    if attempted or records != previous.get("reports", {}):
        OUTPUT.write_text(json.dumps({"version": 1, "last_checked": today.isoformat(), "reports": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Pilot:", attempted, "reports attempted;", sum(v.get("status") == "ready" for v in records.values()), "excerpts saved.")

if __name__ == "__main__":
    main()
