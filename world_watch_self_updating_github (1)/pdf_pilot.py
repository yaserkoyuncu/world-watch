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
MAX_REPORTS = 5
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_PAGES = 35
DEADLINE = 240
HEADERS = {"User-Agent": "WorldWatch-PublicReportReader/1.0", "Accept": "text/html,application/pdf"}
HEADING = re.compile(r"^\s*(executive summary|summary of findings|key findings|main findings|key messages|highlights|summary for policymakers)\s*[:.]?\s*$", re.I)
NEXT_HEADING = re.compile(r"^\s*(?:chapter\s+\d+.*|introduction|table of contents|contents|references|acknowledgements|acknowledgments|methodology|foreword|appendix)\s*$", re.I)
STOP = set("the a an of in to and for on with by from report global world new edition annual quarterly update outlook research pdf summary".split())
PRIORITY = {"Investment & Financial Markets", "Commodities & Critical Minerals", "International Relations & Geopolitical Risk", "Geopolitics & Security", "Strategic Foresight & Emerging Industries"}

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
        if not (re.search(r"\.pdf(?:[?#]|$)", link, re.I) or re.search(r"\b(?:download|full report|pdf)\b", label, re.I)):
            continue
        if re.search(r"\b(?:annex|appendix|methodology|presentation|slides|press release|infographic|summary only)\b", label, re.I):
            continue
        score = 4 * len(words(title) & words(blob)) + (4 if re.search(r"\b(?:full report|download report|download pdf)\b", label, re.I) else 0) + (3 if ".pdf" in link.lower() else 0)
        options[link] = max(score, options.get(link, -1))
    for meta in soup.find_all("meta"):
        if str(meta.get("name", "")).lower() in ("citation_pdf_url", "dc.identifier.pdf") and meta.get("content"):
            options[urljoin(url, meta["content"])] = 100
    return sorted(options, key=lambda k: options[k], reverse=True)[:3]

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
            if not matches_title(title, " ".join(pages)):
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
    data, url = download(report["url"], deadline)
    if data.startswith(b"%PDF-"):
        return inspect_pdf(data, url, report["title"], deadline)
    for candidate in pdf_links(data, url, report["title"]):
        try:
            blob, final = download(candidate, deadline)
            return inspect_pdf(blob, final, report["title"], deadline)
        except (ValueError, OSError, requests.RequestException, TimeoutError):
            continue
        except Exception:
            continue
    return {"status": "unavailable"}

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
    curated = read_json(ROOT / "updates.json", {}).get("updates", [])
    auto = read_json(ROOT / "auto_updates.json", {}).get("updates", [])
    reports = sorted([r for r in curated + auto if eligible(r)], key=lambda r: (r.get("category") in PRIORITY, r.get("date", ""), r.get("type") == "Reference report"), reverse=True)
    attempted = 0
    seen = set()
    for report in reports:
        if attempted >= MAX_REPORTS or time.monotonic() >= deadline:
            break
        rid = str(report["id"])
        if report["url"] in seen or rid in records:
            continue
        seen.add(report["url"])
        attempted += 1
        try:
            entry = process(report, min(deadline, time.monotonic() + 45))
        except Exception:
            entry = {"status": "unavailable"}
        records[rid] = {**entry, "report_url": report["url"], "checked": today.isoformat()}
        print(rid, entry["status"])
    if attempted:
        OUTPUT.write_text(json.dumps({"version": 1, "last_checked": today.isoformat(), "reports": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Pilot:", attempted, "reports attempted;", sum(v.get("status") == "ready" for v in records.values()), "excerpts saved.")

if __name__ == "__main__":
    main()
