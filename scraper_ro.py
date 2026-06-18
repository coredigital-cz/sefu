"""
scraper_ro.py — OLX Romania Lead Scraper
==========================================
Structura identica cu scraper_b2c.py dar pentru olx.ro.

FLOW:
  Browse categorii OLX servicii -> colecteaza URL-uri listing
  -> viziteaza pagina de detaliu -> extrage titlu + telefon
  -> incearca OLX API /limited-phones/ ca fallback
  -> insereaza in DB cu language="Romanian"
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup, Tag
from sqlalchemy import select

from database import AsyncSessionLocal, Job, JobStatus, init_db

logger = logging.getLogger(__name__)

_OLX_BASE = "https://www.olx.ro"

# Categorii OLX servicii (URL paths directe)
_OLX_SERVICE_PATHS: list[str] = [
    "/servicii/reparatii/",
    "/servicii/constructii/",
    "/servicii/curatenie/",
    "/servicii/instalatii-sanitare/",
    "/servicii/electrice/",
    "/servicii/gradinarit-agricultura/",
    "/servicii/transport-mutari/",
    "/servicii/tamplarie-geamuri/",
]

# Pattern URL listing OLX Romania (doua formate posibile)
_LISTING_RE = re.compile(
    r"/(?:oferta|d/oferta)/[a-zA-Z0-9\-_]+-ID[a-zA-Z0-9]+\.html"
    r"|/(?:oferta|d/oferta)/[a-zA-Z0-9\-_]+-\d+\.html"
    r"|/(?:oferta|d/oferta)/[^\"'\s?#]+"
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Connection": "keep-alive",
    "DNT": "1",
}

# ================================================================
# Telefoane Romania: +40 7xx xxx xxx (11 cifre cu prefix)
# ================================================================

_PHONE_RE_RO = re.compile(
    r"(?:\+?40[\s\-]?)?"           # prefix optional +40 sau 40
    r"0?"                           # 0 optional inainte de 7
    r"7[0-9]{2}"                    # 7xx
    r"[\s\-\.]?"
    r"[0-9]{3}"
    r"[\s\-\.]?"
    r"[0-9]{3}"
)


def _norm_ro(raw: str) -> str:
    """Normalizeaza la +40xxxxxxxxx"""
    d = re.sub(r"[^\d]", "", raw).lstrip("0")
    if d.startswith("40"):
        pass
    elif d.startswith("7"):
        d = "40" + d
    else:
        d = "40" + d
    return "+" + d


def _valid_ro(phone: str) -> bool:
    """Valideaza numar mobil roman: +40 7xx xxx xxx"""
    d = re.sub(r"[^\d]", "", phone)
    return len(d) == 11 and d.startswith("40") and d[2] == "7"


def _find_phone_ro(text: str) -> str | None:
    for m in _PHONE_RE_RO.finditer(text):
        p = _norm_ro(m.group(0))
        if _valid_ro(p):
            return p
    return None


# ================================================================
# Lead dataclass
# ================================================================

@dataclass
class Lead:
    business_name: str
    phone_number: str
    niche: str
    url: str


# ================================================================
# Niches Romania — mapate pe categorii OLX
# ================================================================

_NICHES_RO: list[tuple[str, str]] = [
    ("Zugravit si vopsit",       "/servicii/reparatii/"),
    ("Instalatii sanitare",      "/servicii/instalatii-sanitare/"),
    ("Electrician",              "/servicii/electrice/"),
    ("Constructii si renovari",  "/servicii/constructii/"),
    ("Servicii curatenie",       "/servicii/curatenie/"),
    ("Gradinarit",               "/servicii/gradinarit-agricultura/"),
    ("Mutari si transport",      "/servicii/transport-mutari/"),
    ("Tamplarie si acoperisuri", "/servicii/tamplarie-geamuri/"),
]


# ================================================================
# Extrage URL-uri listing de pe pagina categorie OLX
# ================================================================

def _get_listing_urls(html: str, base_url: str = "") -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    results: list[str] = []
    seen: set[str] = set()

    for a_tag in soup.find_all("a", href=True):
        if not isinstance(a_tag, Tag):
            continue
        href = str(a_tag.get("href", ""))

        # Curata query params si anchore
        href_clean = href.split("?")[0].split("#")[0]

        if not _LISTING_RE.search(href_clean):
            continue

        if href_clean.startswith("http"):
            url = href_clean
        elif href_clean.startswith("/"):
            url = _OLX_BASE + href_clean
        else:
            continue

        # Asigura ca e OLX Romania
        if "olx.ro" not in url:
            continue

        # Exclude pagini de cautare/categorii
        if any(p in url for p in ["/oferte/", "/oferta-ta/", "/cont/"]):
            continue

        if url in seen:
            continue

        seen.add(url)
        results.append(url)

    logger.info("Gasit %d URL-uri listing pe pagina", len(results))
    return results


# ================================================================
# Extrage titlu + telefon din pagina listing OLX
# ================================================================

def _parse_olx_detail(html: str) -> tuple[str, str | None]:
    """
    Parseaza pagina de detaliu OLX Romania.
    Returneaza (titlu, telefon_sau_None).
    """
    soup = BeautifulSoup(html, "lxml")

    # ── Titlu ─────────────────────────────────────────────
    title = "Prestator servicii"

    h1 = soup.find("h1")
    if h1 and isinstance(h1, Tag):
        t = h1.get_text(strip=True)
        if t and len(t) > 3:
            title = t

    if title == "Prestator servicii":
        og_title = soup.find("meta", {"property": "og:title"})
        if og_title and isinstance(og_title, Tag):
            c = og_title.get("content", "")
            if c:
                title = str(c).strip()

    # ── Telefon — Metoda 1: tel: link ─────────────────────
    phone: str | None = None

    for a in soup.find_all("a", href=True):
        href = str(a.get("href", ""))
        if href.startswith("tel:"):
            raw = href.replace("tel:", "").strip()
            p = _find_phone_ro(raw)
            if p:
                phone = p
                break

    # ── Telefon — Metoda 2: __NEXT_DATA__ JSON (Next.js) ──
    if not phone:
        nd_tag = soup.find("script", id="__NEXT_DATA__")
        if nd_tag and isinstance(nd_tag, Tag):
            try:
                nd_str = nd_tag.string or ""
                # Cauta direct in string inainte de JSON parse (mai rapid)
                p = _find_phone_ro(nd_str)
                if p:
                    phone = p
                else:
                    # Parse complet JSON pt campuri specifice
                    data = json.loads(nd_str)
                    ad = (data.get("props", {})
                               .get("pageProps", {})
                               .get("ad", {}))
                    for field in ["phone", "phoneNumber", "contact_phone",
                                  "seller_phone", "user_phone"]:
                        val = ad.get(field, "")
                        if val:
                            p = _find_phone_ro(str(val))
                            if p:
                                phone = p
                                break
            except (json.JSONDecodeError, AttributeError, KeyError):
                pass

    # ── Telefon — Metoda 3: data-phone / data-cy atribute ─
    if not phone:
        for attr in ["data-phone", "data-cy"]:
            for el in soup.find_all(attrs={attr: True}):
                if not isinstance(el, Tag):
                    continue
                raw = str(el.get(attr, ""))
                p = _find_phone_ro(raw)
                if p:
                    phone = p
                    break
            if phone:
                break

    # ── Telefon — Metoda 4: keywords in text ──────────────
    if not phone:
        full = soup.get_text(" ", strip=True)
        for kw in ["telefon", "tel.", "tel:", "mobil", "apeleaza",
                   "suna", "contact"]:
            idx = full.lower().find(kw)
            if idx >= 0:
                snippet = full[max(0, idx - 5): idx + 80]
                p = _find_phone_ro(snippet)
                if p:
                    phone = p
                    break

    # ── Telefon — Metoda 5: scan complet pagina ───────────
    if not phone:
        phone = _find_phone_ro(soup.get_text(" ", strip=True))

    return title, phone


# ================================================================
# OLX Romania API /limited-phones/ (fallback)
# ================================================================

async def _fetch_phone_olx_api(
    client: httpx.AsyncClient,
    listing_url: str,
) -> str | None:
    """
    Incearca OLX API sa obtina telefonul.
    Format URL listing: /oferta/{slug}-ID{offer_id}.html
                     sau /oferta/{slug}-{id}.html
    """
    # Extrage ID-ul ofertei din URL
    m = (
        re.search(r"-ID([A-Za-z0-9]+)\.html", listing_url)
        or re.search(r"-(\d{5,})(?:\.html)?$", listing_url)
    )
    if not m:
        return None

    offer_id = m.group(1)

    try:
        r = await client.get(
            f"{_OLX_BASE}/api/v1/offers/{offer_id}/limited-phones/",
            headers={
                **_HEADERS,
                "Accept": "application/json",
                "Referer": listing_url,
            },
            timeout=10.0,
        )
        if r.is_success:
            data = r.json()
            phones_list = data.get("data", [])
            if isinstance(phones_list, list) and phones_list:
                raw = str(phones_list[0].get("phone", ""))
                p = _find_phone_ro(raw)
                if p:
                    logger.debug("OLX API phone OK: %s", p)
                    return p
    except Exception as exc:
        logger.debug("OLX API /limited-phones/ eroare %s: %s",
                     offer_id, exc)

    return None


# ================================================================
# Scrape o nisa
# ================================================================

async def _scrape_niche_ro(
    client: httpx.AsyncClient,
    niche: str,
    category_path: str,
    target: int,
) -> list[Lead]:
    leads: list[Lead] = []
    seen_phones: set[str] = set()
    seen_urls: set[str] = set()

    category_url = f"{_OLX_BASE}{category_path}"

    # Colecteaza URL-uri din mai multe pagini de categorie
    for page in range(1, 8):  # pana la 7 pagini
        if len(seen_urls) >= target * 4:
            break

        params: dict[str, str] = {}
        if page > 1:
            params["page"] = str(page)

        try:
            r = await client.get(
                category_url,
                params=params,
                headers=_HEADERS,
                timeout=20.0,
                follow_redirects=True,
            )
            r.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("Eroare categorie OLX %s pagina %d: %s",
                         category_path, page, exc)
            break

        page_urls = _get_listing_urls(r.text)
        if not page_urls:
            logger.info("Nicio listare pe %s pagina %d", category_path, page)
            break

        new_urls = [u for u in page_urls if u not in seen_urls]
        seen_urls.update(new_urls)
        logger.info("nisa='%s' pagina=%d -> %d URL-uri noi",
                    niche, page, len(new_urls))

        await asyncio.sleep(2.5)

    logger.info("nisa='%s': %d URL-uri de vizitat", niche, len(seen_urls))

    # Viziteaza fiecare listing
    for url in list(seen_urls):
        if len(leads) >= target:
            break

        try:
            dr = await client.get(
                url,
                headers=_HEADERS,
                timeout=15.0,
                follow_redirects=True,
            )
            dr.raise_for_status()
        except httpx.HTTPError as exc:
            logger.debug("Eroare detaliu OLX %s: %s", url, exc)
            await asyncio.sleep(1.0)
            continue

        title, phone = _parse_olx_detail(dr.text)

        # Fallback: incearca API daca nu am gasit telefon in HTML
        if not phone:
            phone = await _fetch_phone_olx_api(client, url)

        if phone and phone not in seen_phones:
            seen_phones.add(phone)
            leads.append(Lead(
                business_name=title,
                phone_number=phone,
                niche=niche,
                url=url,
            ))
            logger.info("LEAD_RO [%s]: '%s' -> %s",
                        niche[:20], title[:40], phone)
        else:
            if not phone:
                logger.debug("Niciun telefon: %s", url)

        await asyncio.sleep(1.5)

    return leads[:target]


# ================================================================
# Insert in DB
# ================================================================

async def _insert_ro(leads: list[Lead]) -> int:
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
                language="Romanian",
                status=JobStatus.SCRAPED,
            ))
            n += 1
        await session.commit()
    return n


# ================================================================
# Main
# ================================================================

async def run_scraper_ro(total: int = 200) -> None:
    await init_db()
    per_niche = max(5, total // len(_NICHES_RO))
    logger.info("Scraper RO: %d nise x %d = %d target",
                len(_NICHES_RO), per_niche, total)

    grand = 0

    async with httpx.AsyncClient(
        http2=False,
        follow_redirects=True,
        timeout=30.0,
    ) as client:
        for niche, category in _NICHES_RO:
            logger.info("=== RO: %s ===", niche)
            leads = await _scrape_niche_ro(client, niche, category, per_niche)

            # De-duplicare
            seen: set[str] = set()
            unique = [l for l in leads
                      if not (l.phone_number in seen
                              or seen.add(l.phone_number))]  # type: ignore

            inserted = await _insert_ro(unique)
            grand += inserted
            logger.info("RO %s: gasit=%d inserat=%d",
                        niche, len(unique), inserted)
            await asyncio.sleep(4.0)

    logger.info("DONE RO. Total lead-uri noi: %d", grand)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )
    asyncio.run(run_scraper_ro(total=200))
