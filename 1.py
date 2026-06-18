"""
scraper_b2c.py - Hybrid King Lead Scraper
==========================================
Sources:
  - firmy.cz  : Czech business directory, targets PROMOTED listings only
  - bazos.cz  : Czech classifieds, targets TOP/PROMOTED listings only

Why promoted only?
  Businesses paying for promoted placement already spend money on
  visibility. They understand the value of being found online.
  Conversion rate from promoted listings is 2-3x higher than organic.

Promoted signals per source:
  firmy.cz : listings with class 'premiumItem', 'topItem', or
             data-premium attribute
  bazos.cz : listings with class 'lista-nadpis top', 'topitem',
             or highlighted border styling

Output: inserts Job rows with status=SCRAPED and language=Czech
        into Postgres, skipping duplicates by phone number.
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

# ================================================================
# Constants
# ================================================================

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "cs-CZ,cs;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Czech phone: mobile 6xx-7xx or landline 2xx-5xx, 9 digits after prefix
_PHONE_RE = re.compile(
    r"(?:\+420\s?)?"
    r"(?:0)?"
    r"(?:[2-7]\d{2})"
    r"[\s\-]?\d{3}"
    r"[\s\-]?\d{3}"
)

# Niches: human label -> (firmy.cz slug, bazos.cz category path)
_NICHES: list[tuple[str, str, str]] = [
    ("Maliri a natiraci",   "maliri-natiraci",      "remeslnici-a-kutilove"),
    ("Instalaterske prace", "instalaterske-prace",  "remeslnici-a-kutilove"),
    ("Elektrikari",         "elektrikari",           "remeslnici-a-kutilove"),
    ("Stavebni prace",      "stavebni-prace",        "remeslnici-a-kutilove"),
    ("Uklidove sluzby",     "uklidove-sluzby",       "ostatni-sluzby"),
    ("Zahradnicke prace",   "zahradnicke-prace",     "ostatni-sluzby"),
    ("Stehovani",           "stehovani",             "doprava"),
    ("Tesari a pokrivaci",  "tesari-pokrivacu",      "remeslnici-a-kutilove"),
]


# ================================================================
# Data container
# ================================================================

@dataclass
class Lead:
    business_name: str
    phone_number: str
    niche: str
    source: str   # "firmy" or "bazos"


# ================================================================
# Phone helpers
# ================================================================

def _normalise_phone(raw: str) -> str:
    """Normalise to +420XXXXXXXXX format."""
    digits = re.sub(r"[^\d]", "", raw)
    # Strip leading zero
    digits = digits.lstrip("0")
    if not digits.startswith("420"):
        digits = "420" + digits
    return "+" + digits


def _is_valid_czech_phone(phone: str) -> bool:
    """Basic sanity: +420 followed by 9 digits."""
    digits = re.sub(r"[^\d]", "", phone)
    return len(digits) == 12 and digits.startswith("420")


def _extract_phone(text: str) -> str | None:
    """Extract and normalise first Czech phone from arbitrary text."""
    match = _PHONE_RE.search(text)
    if not match:
        return None
    normalised = _normalise_phone(match.group(0))
    return normalised if _is_valid_czech_phone(normalised) else None


# ================================================================
# Firmy.cz scraper — promoted listings only
# ================================================================

_FIRMY_BASE = "https://www.firmy.cz"

# CSS selectors that firmy.cz uses to mark promoted/premium items.
# They A/B test layouts so we try several.
_FIRMY_PROMOTED_SELECTORS = [
    "div.premiumItem",
    "div.topItem",
    "li.premiumItem",
    "li.topItem",
    "article[data-premium]",
    "div[data-dot='firmyTopItem']",
    "div.companyCard--premium",
    "div.companyCard--top",
]

# Selectors for the business name inside a promoted card
_FIRMY_NAME_SELECTORS = [
    "h2.companyTitle a",
    "h2[data-dot='firmyName']",
    "a.company-title",
    "h2.firm-name a",
    ".companyCard__name",
]


def _parse_firmy_page(html: str, niche: str) -> list[Lead]:
    """Parse one firmy.cz results page, return promoted leads only."""
    soup = BeautifulSoup(html, "lxml")
    leads: list[Lead] = []

    # Collect all promoted cards
    promoted_cards: list[Tag] = []
    for selector in _FIRMY_PROMOTED_SELECTORS:
        promoted_cards.extend(soup.select(selector))

    # De-duplicate cards that matched multiple selectors
    seen_ids: set[int] = set()
    unique_cards: list[Tag] = []
    for card in promoted_cards:
        card_id = id(card)
        if card_id not in seen_ids:
            seen_ids.add(card_id)
            unique_cards.append(card)

    for card in unique_cards:
        # Skip if already has a website listed (not our target)
        web_link = card.select_one(
            "a[data-dot='firmyWeb'], "
            "a.website-link, "
            "a[href*='http']:not([href*='firmy.cz']):not([href*='mapy.cz'])"
        )
        if web_link:
            continue

        # Extract name
        name: str = ""
        for sel in _FIRMY_NAME_SELECTORS:
            el = card.select_one(sel)
            if el:
                name = el.get_text(strip=True)
                break
        if not name or len(name) < 2:
            continue

        # Extract phone
        card_text = card.get_text(" ", strip=True)
        phone = _extract_phone(card_text)
        if not phone:
            continue

        leads.append(Lead(
            business_name=name,
            phone_number=phone,
            niche=niche,
            source="firmy",
        ))

    return leads


async def _scrape_firmy_niche(
    client: httpx.AsyncClient,
    niche_label: str,
    slug: str,
    per_niche: int,
) -> list[Lead]:
    """Scrape promoted listings for one niche on firmy.cz."""
    results: list[Lead] = []

    for page in range(1, 8):  # max 7 pages per niche
        if len(results) >= per_niche:
            break

        url = f"{_FIRMY_BASE}/{slug}?page={page}"
        try:
            resp = await client.get(
                url,
                headers=_HEADERS,
                timeout=20.0,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("[firmy] HTTP error niche '%s' page %d: %s",
                         niche_label, page, exc)
            break

        page_leads = _parse_firmy_page(resp.text, niche_label)

        if not page_leads:
            logger.info("[firmy] No promoted leads on '%s' page %d — stopping.",
                        niche_label, page)
            break

        results.extend(page_leads)
        logger.info("[firmy] '%s' page %d -> %d promoted leads (total: %d)",
                    niche_label, page, len(page_leads), len(results))

        # Polite crawl delay
        await asyncio.sleep(2.0)

    return results[:per_niche]


# ================================================================
# Bazos.cz scraper — top/promoted listings only
# ================================================================

_BAZOS_BASE = "https://www.bazos.cz"

# Bazos marks promoted listings with specific classes or attributes.
# Top listings sit above the horizontal separator line.
_BAZOS_PROMOTED_SELECTORS = [
    "div.inzeraty.inzeratytop",   # top sponsored block
    "div.inzerat.topitem",
    "li.topitem",
    "div[class*='topitem']",
    "div.inzeraty > div.inzerat:has(span.vip)",
    "div.inzerat.vip",
]

_BAZOS_NAME_SELECTORS = [
    "h2.nadpis a",
    "h3.nadpis a",
    "a.nadpis",
    ".inzerat-nadpis a",
]


def _parse_bazos_page(html: str, niche: str) -> list[Lead]:
    """Parse one bazos.cz results page, return promoted leads only."""
    soup = BeautifulSoup(html, "lxml")
    leads: list[Lead] = []

    promoted_cards: list[Tag] = []
    for selector in _BAZOS_PROMOTED_SELECTORS:
        try:
            promoted_cards.extend(soup.select(selector))
        except Exception:
            # Some CSS4 selectors may not be supported by bs4 version
            continue

    # Fallback: on bazos, top ads appear before a <hr> separator
    # so grab all inzerat divs before the first hr
    if not promoted_cards:
        hr = soup.find("hr")
        if hr:
            for sib in hr.previous_siblings:
                if isinstance(sib, Tag):
                    cards = sib.select("div.inzerat")
                    promoted_cards.extend(cards)
                    if promoted_cards:
                        break

    seen_ids: set[int] = set()
    unique_cards: list[Tag] = []
    for card in promoted_cards:
        card_id = id(card)
        if card_id not in seen_ids:
            seen_ids.add(card_id)
            unique_cards.append(card)

    for card in unique_cards:
        # Extract name from listing title
        name: str = ""
        for sel in _BAZOS_NAME_SELECTORS:
            el = card.select_one(sel)
            if el:
                name = el.get_text(strip=True)
                break
        if not name or len(name) < 3:
            continue

        # Bazos listings often have phone in the body text
        card_text = card.get_text(" ", strip=True)
        phone = _extract_phone(card_text)

        # If no phone in card, try to get it from the listing detail page
        # We skip detail page fetching to stay fast — phone in card is enough
        if not phone:
            continue

        leads.append(Lead(
            business_name=name,
            phone_number=phone,
            niche=niche,
            source="bazos",
        ))

    return leads


async def _scrape_bazos_niche(
    client: httpx.AsyncClient,
    niche_label: str,
    category_path: str,
    per_niche: int,
) -> list[Lead]:
    """Scrape promoted listings for one niche on bazos.cz."""
    results: list[Lead] = []

    for page_offset in range(0, per_niche * 25, 25):  # bazos uses offset
        if len(results) >= per_niche:
            break

        url = f"{_BAZOS_BASE}/{category_path}/{page_offset}/"
        try:
            resp = await client.get(
                url,
                headers=_HEADERS,
                timeout=20.0,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("[bazos] HTTP error niche '%s' offset %d: %s",
                         niche_label, page_offset, exc)
            break

        page_leads = _parse_bazos_page(resp.text, niche_label)

        if not page_leads and page_offset > 0:
            logger.info("[bazos] No promoted leads on '%s' offset %d — stopping.",
                        niche_label, page_offset)
            break

        results.extend(page_leads)
        logger.info("[bazos] '%s' offset %d -> %d promoted leads (total: %d)",
                    niche_label, page_offset, len(page_leads), len(results))

        await asyncio.sleep(2.5)

    return results[:per_niche]


# ================================================================
# Database insertion
# ================================================================

async def _insert_leads(leads: list[Lead]) -> int:
    """Insert leads into Postgres, skip duplicates by phone number."""
    inserted = 0
    async with AsyncSessionLocal() as session:
        for lead in leads:
            # Check for existing phone to avoid duplicates
            existing = await session.scalar(
                select(Job).where(Job.phone_number == lead.phone_number)
            )
            if existing:
                logger.debug("Skipping duplicate phone %s (%s)",
                             lead.phone_number, lead.business_name)
                continue

            session.add(Job(
                business_name=lead.business_name,
                phone_number=lead.phone_number,
                niche=lead.niche,
                language="Czech",
                status=JobStatus.SCRAPED,
            ))
            inserted += 1

        await session.commit()

    return inserted


# ================================================================
# Main runner
# ================================================================

async def run_scraper(
    total_target: int = 400,
    firmy_ratio: float = 0.6,
) -> None:
    """
    Run both scrapers concurrently across all niches.

    Args:
        total_target : total leads to aim for across all niches and sources
        firmy_ratio  : fraction of leads to pull from firmy.cz (rest = bazos)
                       0.6 = 60% firmy, 40% bazos
    """
    await init_db()

    per_niche_firmy = max(1, int((total_target * firmy_ratio) / len(_NICHES)))
    per_niche_bazos = max(1, int((total_target * (1 - firmy_ratio)) / len(_NICHES)))

    logger.info(
        "Starting scraper: %d niches | firmy target %d/niche | "
        "bazos target %d/niche",
        len(_NICHES), per_niche_firmy, per_niche_bazos,
    )

    grand_total = 0

    async with httpx.AsyncClient(http2=False, follow_redirects=True) as client:
        for niche_label, firmy_slug, bazos_path in _NICHES:
            logger.info("--- Niche: %s ---", niche_label)
            all_leads: list[Lead] = []

            # Run both sources sequentially within a niche to avoid
            # hammering servers simultaneously
            firmy_leads = await _scrape_firmy_niche(
                client, niche_label, firmy_slug, per_niche_firmy
            )
            all_leads.extend(firmy_leads)

            # Brief pause between sources
            await asyncio.sleep(3.0)

            bazos_leads = await _scrape_bazos_niche(
                client, niche_label, bazos_path, per_niche_bazos
            )
            all_leads.extend(bazos_leads)

            # De-duplicate by phone within this batch before inserting
            seen_phones: set[str] = set()
            unique_leads: list[Lead] = []
            for lead in all_leads:
                if lead.phone_number not in seen_phones:
                    seen_phones.add(lead.phone_number)
                    unique_leads.append(lead)

            inserted = await _insert_leads(unique_leads)
            grand_total += inserted

            logger.info(
                "Niche '%s': firmy=%d, bazos=%d, unique=%d, inserted=%d",
                niche_label,
                len(firmy_leads),
                len(bazos_leads),
                len(unique_leads),
                inserted,
            )

            # Pause between niches
            await asyncio.sleep(4.0)

    logger.info(
        "Scraper complete. Total new leads inserted into DB: %d",
        grand_total,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )
    asyncio.run(run_scraper(total_target=400, firmy_ratio=0.6))