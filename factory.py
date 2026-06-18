"""
factory.py — Hybrid King Website Generator v4
===============================================
Simplified for reliability. Only 24 Groq-filled placeholders
(down from 40+). Stock photos mapped by niche (no AI guessing).

AI: Groq API (free, 14,400 req/day, no credit card)
Model: llama-3.3-70b-versatile
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import time
from pathlib import Path

import aiofiles
import httpx
from pydantic import BaseModel

from config import settings

logger = logging.getLogger(__name__)

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
_MODEL = "llama-3.3-70b-versatile"
_REQUEST_DELAY = 4.0
_last_request_time: float = 0.0


class WebsiteOutput(BaseModel):
    index_html: str
    style_css: str = ""


# ═══════════════════════════════════════════════════════════
# Unsplash photos mapped by niche keyword
# Reliable — no AI guessing needed
# ═══════════════════════════════════════════════════════════

_NICHE_PHOTOS: dict[str, str] = {
    # Construction / building
    "staveb":    "photo-1504307651254-35680f356dfd",
    "zedni":     "photo-1504307651254-35680f356dfd",
    "rekonstruk":"photo-1581094794329-c8112a89af12",
    # Painting
    "malir":     "photo-1562259929-b4e1fd3aef09",
    "malov":     "photo-1562259929-b4e1fd3aef09",
    "lakyr":     "photo-1562259929-b4e1fd3aef09",
    "nater":     "photo-1562259929-b4e1fd3aef09",
    # Plumbing / heating
    "instal":    "photo-1585704032915-c3400ca199e7",
    "vodoin":    "photo-1585704032915-c3400ca199e7",
    "topen":     "photo-1585704032915-c3400ca199e7",
    "voda":      "photo-1585704032915-c3400ca199e7",
    # Electrical
    "elektr":    "photo-1621905251918-48416bd8575a",
    "elektro":   "photo-1621905251918-48416bd8575a",
    # Cleaning
    "uklid":     "photo-1581578731548-c64695cc6952",
    "cisten":    "photo-1581578731548-c64695cc6952",
    # Gardening / landscaping
    "zahrad":    "photo-1416879595882-3373a0480b5b",
    "sekani":    "photo-1416879595882-3373a0480b5b",
    # Moving
    "stehov":    "photo-1600518464441-9154a4dea21b",
    # Carpentry / roofing
    "tesar":     "photo-1588854337236-6889d631faa8",
    "pokryvac":  "photo-1588854337236-6889d631faa8",
    "strech":    "photo-1588854337236-6889d631faa8",
    # Handyman
    "hodinov":   "photo-1581578731548-c64695cc6952",
    "manzel":    "photo-1581578731548-c64695cc6952",
    "oprav":     "photo-1581578731548-c64695cc6952",
    # Tiling / flooring
    "obklad":    "photo-1584622650111-993a426fbf0a",
    "dlazb":     "photo-1584622650111-993a426fbf0a",
    "podlah":    "photo-1584622650111-993a426fbf0a",
    # Welding / metal
    "svar":      "photo-1504328345606-18bbc8c9d7d1",
    "zamec":     "photo-1504328345606-18bbc8c9d7d1",
    "kov":       "photo-1504328345606-18bbc8c9d7d1",
    # HVAC / heat pumps
    "klimat":    "photo-1631545806609-05172b622742",
    "cerpad":    "photo-1631545806609-05172b622742",
    "reviz":     "photo-1631545806609-05172b622742",
    # Default
    "default":   "photo-1504307651254-35680f356dfd",
}


def _pick_photo(business_name: str, niche: str) -> str:
    """Pick Unsplash photo ID based on niche/name keywords."""
    combined = (business_name + " " + niche).lower()
    for keyword, photo_id in _NICHE_PHOTOS.items():
        if keyword in combined:
            return photo_id
    return _NICHE_PHOTOS["default"]


# ═══════════════════════════════════════════════════════════
# Groq placeholders — only 24 keys (was 40+)
# ═══════════════════════════════════════════════════════════

_PLACEHOLDERS = [
    "TAGLINE",
    "COLOR", "ACCENT",
    "STAT_1", "STAT_1_LABEL",
    "STAT_2", "STAT_2_LABEL",
    "STAT_3", "STAT_3_LABEL",
    "SERVICES_TITLE", "SERVICES_SUB",
    "SVC_1_TITLE", "SVC_1_DESC",
    "SVC_2_TITLE", "SVC_2_DESC",
    "SVC_3_TITLE", "SVC_3_DESC",
    "REVIEW_1", "REVIEW_1_NAME",
    "REVIEW_2", "REVIEW_2_NAME",
    "REVIEW_3", "REVIEW_3_NAME",
    "CTA_TEXT",
]


# ═══════════════════════════════════════════════════════════
# System prompts — simple, clear instructions
# ═══════════════════════════════════════════════════════════

_SYSTEM_CZECH = (
    "Jsi copywriter pro ceske remeslniky. Vyplnis JSON pro web stranky.\n\n"
    "PRAVIDLA:\n"
    "- TAGLINE: 1 veta, max 12 slov. Napr: 'Spolehlivy malar v Praze. Bezplatna nabidka do 24 hodin.'\n"
    "- COLOR: hlavni hex barva pro obor (tmavsi). Napr: #1A5276 (instalater), #C0392B (stavba), #196F3D (zahrada)\n"
    "- ACCENT: vyrazna hex barva pro tlacitka. Napr: #E67E22, #F39C12, #2ECC71\n"
    "- STAT cisla: realisticka. Napr: '150+', '12 let', '100%'\n"
    "- SVC: 3 sluzby s krátkym popisem (1-2 vety)\n"
    "- REVIEW: 3 recenze (15-25 slov), ceska jmena\n"
    "- CTA_TEXT: 1 veta motivace zavolat. Napr: 'Nabidka zdarma do 24 hodin. Zavolejte a domluvime se.'\n"
    "- ZAKAZANO: emoji, specialni znaky\n"
    "- Vrat POUZE JSON, nic jineho.\n"
)

_SYSTEM_ENGLISH = (
    "You are a copywriter for local tradespeople. Fill JSON for a website.\n\n"
    "RULES:\n"
    "- TAGLINE: 1 sentence, max 12 words. E.g: 'Your trusted plumber in London. Free quotes within 24h.'\n"
    "- COLOR: dark industry hex. E.g: #1A5276 (plumber), #C0392B (builder), #196F3D (gardener)\n"
    "- ACCENT: bright button hex. E.g: #E67E22, #F39C12, #2ECC71\n"
    "- STAT numbers: realistic. E.g: '150+', '12 years', '100%'\n"
    "- SVC: 3 services with short description (1-2 sentences)\n"
    "- REVIEW: 3 reviews (15-25 words), realistic names\n"
    "- CTA_TEXT: 1 sentence motivating to call. E.g: 'Free quote within 24h. Call us and we will arrange everything.'\n"
    "- NO emoji\n"
    "- Return ONLY JSON.\n"
)

_SYSTEM_ROMANIAN = (
    "Esti copywriter pentru meseriasi. Completezi JSON pentru un site web.\n\n"
    "REGULI:\n"
    "- TAGLINE: 1 propozitie, max 12 cuvinte\n"
    "- COLOR: culoare hex industrie (inchisa). Ex: #1A5276 (instalator), #C0392B (constructor)\n"
    "- ACCENT: culoare hex butoane (vie). Ex: #E67E22, #F39C12\n"
    "- STAT cifre: realiste. Ex: '150+', '12 ani', '100%'\n"
    "- SVC: 3 servicii cu descriere scurta\n"
    "- REVIEW: 3 recenzii (15-25 cuvinte), nume romanesti\n"
    "- CTA_TEXT: 1 propozitie motivanta sa sune\n"
    "- INTERZIS: emoji\n"
    "- Returneaza DOAR JSON.\n"
)

_SYSTEMS = {
    "Czech":    _SYSTEM_CZECH,
    "English":  _SYSTEM_ENGLISH,
    "Romanian": _SYSTEM_ROMANIAN,
}


# ═══════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════

def _extract_json(text: str) -> str:
    """Extract first complete JSON object from text."""
    text = re.sub(r"^```(?:json)?\s*\n?", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\n?```\s*$", "", text, flags=re.MULTILINE).strip()
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON in response")
    depth, in_str, escape = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise ValueError("Unterminated JSON")


def _strip_emoji(text: str) -> str:
    return re.sub(
        "[\U00010000-\U0010FFFF\u2600-\u26FF\u2700-\u27BF]",
        "", text
    ).strip()


async def _load_template(language: str) -> str:
    base = Path(__file__).parent / "templates"
    mapping = {
        "Czech":    "czech_tradesman.html",
        "English":  "czech_tradesman.html",  # same template, different text
        "Romanian": "czech_tradesman.html",
    }
    path = base / mapping.get(language, "czech_tradesman.html")
    async with aiofiles.open(path, encoding="utf-8") as fh:
        return await fh.read()


def _build_prompt(name: str, niche: str, lang: str, phone: str, city: str) -> str:
    keys = "\n".join(f'  "{k}": "..."' for k in _PLACEHOLDERS)
    return (
        f"Business: {name}\n"
        f"Niche: {niche}\n"
        f"City: {city}\n"
        f"Language: {lang}\n\n"
        f"Return JSON with EXACTLY these keys:\n"
        f"{{\n{keys}\n}}\n\n"
        f"No emoji. All text in {lang}. Hex colors only."
    )


async def _call_groq(system: str, prompt: str) -> str:
    """Call Groq API with rate limiting + automatic key rotation on 429."""
    global _last_request_time
    from groq_rotator import get_rotator

    rotator = get_rotator()

    # Incearca fiecare cheie disponibila
    for attempt in range(rotator.count):
        now = time.monotonic()
        elapsed = now - _last_request_time
        if elapsed < _REQUEST_DELAY:
            await asyncio.sleep(_REQUEST_DELAY - elapsed)
        _last_request_time = time.monotonic()

        key = rotator.current
        if not key:
            raise RuntimeError("GROQ_API_KEY(S) nu sunt setate in .env")

        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                _GROQ_URL,
                json={
                    "model": _MODEL,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user",   "content": prompt},
                    ],
                    "temperature": 0.7,
                    "max_tokens": 1500,
                    "response_format": {"type": "json_object"},
                },
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            )

            if r.status_code == 429:
                logger.warning(
                    "Groq 429 (rate limit) pe cheie slot=%d — rotesc...",
                    attempt,
                )
                rotator.rotate()
                await asyncio.sleep(3)
                continue

            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

    # Toate cheile au 429 — asteapta si ridica exceptie pt retry extern
    raise RuntimeError(
        "429: Toate cheile Groq au atins rate limit. "
        f"Stare rotator: {rotator.status()}"
    )


# ═══════════════════════════════════════════════════════════
# Main generator
# ═══════════════════════════════════════════════════════════

async def generate_website(
    business_name: str,
    niche: str,
    language: str = "Czech",
    phone: str = "",
    city: str = "",
) -> WebsiteOutput:
    """Generate website from template + Groq JSON."""

    # Defaults
    city = city.strip() or (
        "Praha" if language == "Czech"
        else "Bucuresti" if language == "Romanian"
        else "London"
    )
    phone_raw = phone.strip() or "+420 000 000 000"
    phone_href = re.sub(r"[^\d+]", "", phone_raw)
    phone_digits = re.sub(r"[^\d]", "", phone_raw)

    # Pick stock photo by niche
    photo_id = _pick_photo(business_name, niche)

    # Load template
    template = await _load_template(language)

    # Call Groq
    system = _SYSTEMS.get(language, _SYSTEM_CZECH)
    prompt = _build_prompt(business_name, niche, language, phone_raw, city)

    placeholders = {}
    last_error = None

    for attempt in range(1, 4):
        try:
            raw = await _call_groq(system, prompt)
            data = json.loads(_extract_json(raw))

            # Check all keys present
            missing = [k for k in _PLACEHOLDERS if k not in data]
            if missing:
                raise ValueError(f"Missing keys: {missing}")

            # Clean emoji from all values
            for k in data:
                data[k] = _strip_emoji(str(data[k]))

            placeholders = data
            logger.info(
                "Groq OK for '%s' (%s) attempt %d",
                business_name, language, attempt,
            )
            break

        except (ValueError, json.JSONDecodeError, KeyError,
                httpx.HTTPError) as exc:
            last_error = exc
            logger.warning(
                "Attempt %d/3 for '%s': %s",
                attempt, business_name, exc,
            )
            # Longer wait on 429
            if "429" in str(exc):
                logger.info("Groq rate limit — waiting 65s...")
                await asyncio.sleep(65)
            else:
                await asyncio.sleep(4 * attempt)
    else:
        raise RuntimeError(
            f"Groq failed 3x for '{business_name}': {last_error}"
        ) from last_error

    # ── Replace all placeholders ─────────────────────────

    # Static fields (from scraper data, not Groq)
    static = {
        "BUSINESS_NAME": business_name,
        "CITY":          city,
        "PHONE_RAW":     phone_href,
        "PHONE_DISPLAY": phone_raw,
        "PHONE_DIGITS":  phone_digits,
        "PHOTO_ID":      photo_id,
    }

    html = template
    for k, v in static.items():
        html = html.replace("{{" + k + "}}", v)
    for k, v in placeholders.items():
        html = html.replace("{{" + k + "}}", v)

    # Verify
    remaining = re.findall(r"\{\{[A-Z_0-9]+\}\}", html)
    if remaining:
        logger.warning("Unreplaced: %s", remaining)

    logger.info("Site ready: '%s' | %d chars", business_name, len(html))
    return WebsiteOutput(index_html=html, style_css="")


# ═══════════════════════════════════════════════════════════
# Demo
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    async def _demo():
        r = await generate_website(
            business_name="Novak Instalaterske prace",
            niche="Instalaterske prace",
            language="Czech",
            phone="+420 721 234 567",
            city="Praha",
        )
        print(f"HTML: {len(r.index_html):,} chars")
        Path("demo.html").write_text(r.index_html, encoding="utf-8")
        print("Saved: demo.html")

    asyncio.run(_demo())