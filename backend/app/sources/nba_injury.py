"""Adapter for the NBA's official injury report PDFs (raw collection only; docs/INJURY_COLLECTOR_PLAN.md).

Files: https://ak-static.cms.nba.com/referee/injury/Injury-Report_<YYYY-MM-DD>_<HH_MM><AM|PM>.pdf, Eastern time in the
name (quarter-hour naming since 2025-12-22). Nothing here reads the PDF's content. NBA.com terms allow downloading for
personal, non-commercial use; the host's robots.txt does not disallow this path, asks for a 1 s crawl delay and
disallows query strings, so no query string is ever sent (docs/DATA_SOURCES.md).
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

SOURCE = "nba_injury_report"
BASE = "https://ak-static.cms.nba.com/referee/injury/"
IMPORTER_VERSION = "nba-injury-raw-1"
EASTERN = ZoneInfo("America/New_York")
CRAWL_DELAY_SECONDS = 1.0
PDF_MAGIC = b"%PDF-"


@dataclass(frozen=True)
class Response:
    status: int
    last_modified: Optional[str]
    etag: Optional[str]
    content_length: Optional[int]
    body: bytes = b""


def file_name(slot: datetime) -> str:
    """File name for a slot (an aware datetime), using Eastern wall time: 17:30 ET -> Injury-Report_<date>_05_30PM.pdf."""
    local = slot.astimezone(EASTERN)
    hour12 = local.hour % 12 or 12
    return f"Injury-Report_{local:%Y-%m-%d}_{hour12:02d}_{local.minute:02d}{'AM' if local.hour < 12 else 'PM'}.pdf"


def request_key(name: str) -> str:
    return f"file={name}"


def url(name: str) -> str:
    return BASE + name


def _response(resp, with_body: bool) -> Response:
    length = resp.headers.get("Content-Length")
    return Response(resp.status_code, resp.headers.get("Last-Modified"), resp.headers.get("ETag"),
                    int(length) if length and length.isdigit() else None, resp.content if with_body else b"")


def fetch(name: str, timeout: float = 30) -> Response:
    """GET one report. Returns whatever status the server gives (403/404 mean not available); raises on network errors.
    The call returns only after the whole body has been read."""
    import httpx  # imported lazily so tests never need the network stack

    return _response(httpx.get(url(name), timeout=timeout), with_body=True)


def head(name: str, timeout: float = 20) -> Response:
    import httpx

    return _response(httpx.head(url(name), timeout=timeout), with_body=False)


def problem(resp: Response) -> Optional[str]:
    """Why a 200 response must not be stored, or None if it is a complete PDF."""
    if not resp.body.startswith(PDF_MAGIC):
        return "body is not a PDF"
    if resp.content_length is not None and resp.content_length != len(resp.body):
        return f"body has {len(resp.body)} bytes but Content-Length says {resp.content_length}"
    return None
