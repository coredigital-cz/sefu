"""
scraper_b2c.py - Hybrid King Lead Scraper
==========================================
FIXES APPLIED:
  1. Filter listing URLs to ONLY service-related bazos subdomains:
     sluzby, prace, remeslnici, dum (not auto/sport/motorky/etc)

  2. Get listing TITLE from detail page <h1> tag,
     not from the search results link (which has no text)

  3. Get PHONE from detail page (hidden on search results page)

FLOW:
  Search bazos -> filter to service URLs -> visit each detail page
  -> extract title from h1 -> extract phone -> insert to DB
"""
from __future__ import annotations

import asyncio
import logging
import re
import sys
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup, Tag
from sqlalchemy import select

from database import AsyncSessionLocal, Job, JobStatus, init_db

logger = logging.getLogger(__name__)

_BAZOS_BASE = "https://www.bazos.cz"
_BAZOS_SEARCH = f"{_BAZOS_BASE}/search.php"

# Only accept listings from service-related bazos subdomains
# NOT: auto, sport, motorky, elektro, pc, obleceni, reality, zvire...
_SERVICE_SUBDOMAINS = {
    "sluzby",       # services
    "prace",        # work / jobs
    "remeslnici",   # craftsmen
    "dum",          # home + DIY (includes some tradespeople)
    "www",          # main site (generic)
}

# Pattern: /inzerat/DIGITS/anything.php
_LISTING_RE = re.compile(r"/inzerat/(\d+)/[^\"'\s]+\.php")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "cs-CZ,cs;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Connection": "keep-alive",
}

# ================================================================
# Phone helpers
# ================================================================

_PHONE_RE = re.compile(
    r"(?:\+?420[\s\-]?)?"
    r"(?:0)?"
    r"(?:[2-7]\d{2})"
    r"[\s\-]?\d{3}"
    r"[\s\-]?\d{3}"
)


def _norm(raw: str) -> str:
    d = re.sub(r"[^\d]", "", raw).lstrip("0")
    if not d.startswith("420"):
        d = "420" + d
    return "+" + d


def _valid(phone: str) -> bool:
    d = re.sub(r"[^\d]", "", phone)
    return len(d) == 12 and d.startswith("420")


def _find_phone(text: str) -> str | None:
    for m in _PHONE_RE.finditer(text):
        p = _norm(m.group(0))
        if _valid(p):
            return p
    return None


# ================================================================
# Lead
# ================================================================

@dataclass
class Lead:
    business_name: str
    phone_number: str
    niche: str
    url: str


# ================================================================
# Niches — search terms that relate to SERVICES on bazos
# ================================================================

_NICHES: list[tuple[str, list[str]]] = [
    ("Maliri a natiraci",   ["maliř", "malování pokojů", "natěrač", "malir"]),
    ("Instalaterske prace", ["instalatér", "voda topení", "topenářství"]),
    ("Elektrikari",         ["elektrikář", "elektroinstalace", "elektro práce"]),
    ("Stavebni prace",      ["rekonstrukce bytu", "stavební práce", "bourání"]),
    ("Uklidove sluzby",     ["úklid bytu", "uklízení", "čištění koberců"]),
    ("Zahradnicke prace",   ["zahradník", "sekání trávy", "zahradnické práce"]),
    ("Stehovani",           ["stěhování", "stěhovací služba", "transport nábytku"]),
    ("Tesari a pokrivaci",  ["pokrývač", "střecha oprava", "tesařské práce"]),
]


# ================================================================
# Get service listing URLs from search page
# FILTERS to only service-related subdomains
# ================================================================

def _get_service_urls(html: str) -> list[str]:
    """
    Find all bazos listing URLs on a search page,
    but ONLY from service-related subdomains.
    Returns list of absolute URLs.
    """
    soup = BeautifulSoup(html, "lxml")
    results: list[str] = []
    seen: set[str] = set()

    for a_tag in soup.find_all("a", href=True):
        if not isinstance(a_tag, Tag):
            continue
        href = str(a_tag.get("href", ""))

        if not _LISTING_RE.search(href):
            continue

        # Make absolute URL
        if href.startswith("http"):
            url = href
        elif href.startswith("/"):
            url = _BAZOS_BASE + href
        else:
            continue

        if url in seen:
            continue

        # Extract subdomain to check if it's a service category
        subdomain = "www"
        try:
            # URL format: https://SUBDOMAIN.bazos.cz/inzerat/...
            host = url.split("//")[1].split("/")[0]  # e.g. sluzby.bazos.cz
            subdomain = host.split(".")[0]            # e.g. sluzby
        except (IndexError, ValueError):
            pass

        # FILTER: only accept service-related subdomains
        if subdomain not in _SERVICE_SUBDOMAINS:
            logger.debug("Skipping non-service URL (%s): %s",
                         subdomain, url[:60])
            continue

        seen.add(url)
        results.append(url)

    logger.info("Found %d service listing URLs (filtered by subdomain)",
                len(results))
    return results


# ================================================================
# Extract title + phone from detail page
# ================================================================

def _parse_detail_page(html: str) -> tuple[str, str | None]:
    """
    Parse bazos listing detail page.
    Returns (title, phone_or_None).
    Title comes from <h1> tag (reliable).
    Phone comes from tel: links or page text.
    """
    soup = BeautifulSoup(html, "lxml")

    # Title from h1 (always present on bazos detail pages)
    title = "Neznamy podnikatel"
    h1 = soup.find("h1")
    if h1 and isinstance(h1, Tag):
        title = h1.get_text(strip=True)
        if not title or len(title) < 3:
            title = "Neznamy podnikatel"

    # Phone Method 1: tel: links
    phone: str | None = None
    for a in soup.find_all("a", href=True):
        href = str(a.get("href", ""))
        if href.startswith("tel:"):
            raw = href.replace("tel:", "").strip()
            p = _find_phone(raw)
            if p:
                phone = p
                break

    if not phone:
        # Phone Method 2: known phone container classes
        for cls_name in ["inzeratytelefon", "telefon", "kontakt"]:
            for el in soup.find_all(attrs={"class": True}):
                if not isinstance(el, Tag):
                    continue
                if cls_name in " ".join(el.get("class") or []):
                    p = _find_phone(el.get_text(" ", strip=True))
                    if p:
                        phone = p
                        break
            if phone:
                break

    if not phone:
        # Phone Method 3: search near keywords
        full = soup.get_text(" ", strip=True)
        for kw in ["telefon", "tel.", "tel:", "volat", "mobil", "kontakt"]:
            idx = full.lower().find(kw)
            if idx >= 0:
                snippet = full[max(0, idx - 5): idx + 80]
                p = _find_phone(snippet)
                if p:
                    phone = p
                    break

    if not phone:
        # Phone Method 4: full page scan
        phone = _find_phone(soup.get_text(" ", strip=True))

    return title, phone


# ================================================================
# Main bazos scraper
# ================================================================

async def _scrape_niche(
    client: httpx.AsyncClient,
    niche: str,
    terms: list[str],
    target: int,
) -> list[Lead]:
    leads: list[Lead] = []
    seen_phones: set[str] = set()
    seen_urls: set[str] = set()

    for term in terms:
        if len(leads) >= target:
            break

        # Collect service listing URLs from multiple search pages
        service_urls: list[str] = []

        for start in [0, 20, 40, 60]:
            if len(service_urls) >= target * 3:
                break

            params = {
                "hledat":    term,
                "rubriky":   "www",
                "hlokalita": "",
                "humkreis":  "25",
                "order":     "",
                "start":     str(start),
            }

            try:
                r = await client.get(
                    _BAZOS_SEARCH,
                    params=params,
                    headers=_HEADERS,
                    timeout=20.0,
                    follow_redirects=True,
                )
                r.raise_for_status()
            except httpx.HTTPError as exc:
                logger.error("Search error term='%s' start=%d: %s",
                             term, start, exc)
                break

            page_urls = _get_service_urls(r.text)
            if not page_urls:
                logger.info("No service listings for term='%s' start=%d",
                            term, start)
                break

            service_urls.extend(u for u in page_urls if u not in seen_urls)
            logger.info("term='%s' start=%d -> %d service URLs",
                        term, start, len(page_urls))
            await asyncio.sleep(2.0)

        logger.info("term='%s': %d service URLs to visit",
                    term, len(service_urls))

        # Visit each detail page
        for url in service_urls:
            if len(leads) >= target:
                break
            if url in seen_urls:
                continue
            seen_urls.add(url)

            try:
                dr = await client.get(
                    url,
                    headers=_HEADERS,
                    timeout=15.0,
                    follow_redirects=True,
                )
                dr.raise_for_status()
            except httpx.HTTPError as exc:
                logger.debug("Detail error %s: %s", url, exc)
                await asyncio.sleep(1.0)
                continue

            title, phone = _parse_detail_page(dr.text)

            if phone and phone not in seen_phones:
                seen_phones.add(phone)
                leads.append(Lead(
                    business_name=title,
                    phone_number=phone,
                    niche=niche,
                    url=url,
                ))
                logger.info("LEAD [%s]: '%s' -> %s",
                            niche[:20], title[:40], phone)
            else:
                if not phone:
                    logger.debug("No phone: %s", url)

            await asyncio.sleep(1.5)

        logger.info("term='%s' done -> %d leads total", term, len(leads))
        await asyncio.sleep(2.0)

    return leads[:target]


# ================================================================
# Database insert
# ================================================================

async def _insert(leads: list[Lead]) -> int:
    n = 0
    async with AsyncSessionLocal() as session:
        for lead in leads:
            exists = await session.scalar(
                select(Job).where(Job.phone_number == lead.phone_number)
            )
            if exists:
                continue
            session.add(Job(
                business_name=lead.business_name,
                phone_number=lead.phone_number,
                niche=lead.niche,
                language="Czech",
                status=JobStatus.SCRAPED,
            ))
            n += 1
        await session.commit()
    return n


# ================================================================
# Main
# ================================================================

async def run_scraper(total: int = 200) -> None:
    await init_db()
    per_niche = max(5, total // len(_NICHES))
    logger.info("Scraper: %d niches x %d = %d target",
                len(_NICHES), per_niche, total)

    grand = 0

    async with httpx.AsyncClient(
        http2=False,
        follow_redirects=True,
        timeout=30.0,
    ) as client:
        for niche, terms in _NICHES:
            logger.info("=== %s ===", niche)
            leads = await _scrape_niche(client, niche, terms, per_niche)

            # De-duplicate
            seen: set[str] = set()
            unique = [l for l in leads
                      if not (l.phone_number in seen
                              or seen.add(l.phone_number))]  # type: ignore[func-returns-value]

            inserted = await _insert(unique)
            grand += inserted
            logger.info("%s: found=%d inserted=%d",
                        niche, len(unique), inserted)
            await asyncio.sleep(4.0)

    logger.info("DONE. Total new leads: %d", grand)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )
    asyncio.run(run_scraper(total=200))