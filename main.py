"""
main.py — Hybrid King Entry Point (Manual WhatsApp Mode)
=========================================================
Suporta doua piete independente:
    python main.py --market cz   # Cehia: bazos.cz
    python main.py --market ro   # Romania: olx.ro

Fiecare piata = DB separat + CSV separat + log separat.
WhatsApp auto-send = DEZACTIVAT. Trimiti tu manual.
"""
import argparse
import os
import sys

# ══ 1. Parse market INAINTE de orice alt import ══════════════
def _parse_market() -> str:
    p = argparse.ArgumentParser(
        description="Hybrid King — Lead scraper + site generator",
        add_help=False,
    )
    p.add_argument(
        "--market", choices=["cz", "ro"], default="cz",
        help="Piata: cz (bazos.cz) | ro (olx.ro)"
    )
    args, _ = p.parse_known_args()
    return args.market

MARKET = _parse_market()

# ══ 2. Set env vars INAINTE de import config/database ════════
if "DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///./hybridking_{MARKET}.db"
os.environ["MARKET"] = MARKET   # folosit de orchestrator._is_mobile + _build_message

# ══ 3. Acum importam restul ══════════════════════════════════
import asyncio
import csv
import logging
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select, update

from config import settings
from database import AsyncSessionLocal, Job, JobStatus, init_db
from orchestrator import (
    _build_message,
    _is_mobile,
    _is_quality_lead,
    loop_deployer,
)

# ── Importa scraper-ul corect pentru piata ────────────────────
if MARKET == "ro":
    from scraper_ro import run_scraper_ro as _run_scraper
else:
    from scraper_b2c import run_scraper as _run_scraper

# ── Constante per piata ───────────────────────────────────────
_OUTREACH_CSV  = Path(f"outreach_{MARKET}.csv")
_LOG_FILE      = Path(f"logs/hybridking_{MARKET}.log")
_MARKET_LABEL  = "CZ - bazos.cz" if MARKET == "cz" else "RO - olx.ro"
_MARKET_FLAG   = "CZ" if MARKET == "cz" else "RO"

logger = logging.getLogger("hybridking.main")

# IDs deja notificate in aceasta sesiune
_notified_ids: set[int] = set()


# ═══════════════════════════════════════════════════════════
# CSV logger
# ═══════════════════════════════════════════════════════════

def _ensure_csv() -> None:
    if not _OUTREACH_CSV.exists():
        with open(_OUTREACH_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "piata", "job_id", "firma", "telefon", "nisa",
                "website", "wa_link", "mesaj_scurt", "notificat_la",
            ])


def _append_csv(job: Job, msg: str) -> None:
    clean = re.sub(r"[^\d]", "", job.phone_number)
    wa    = f"https://wa.me/{clean}"
    # Prima linie a mesajului ca preview
    preview = msg.split("\n\n")[0].replace("\n", " ")[:80]
    with open(_OUTREACH_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            MARKET.upper(),
            job.id,
            job.business_name,
            job.phone_number,
            job.niche,
            job.vercel_url,
            wa,
            preview,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ])


# ═══════════════════════════════════════════════════════════
# Notifier loop
# ═══════════════════════════════════════════════════════════

async def loop_notifier() -> None:
    """
    Scaneaza DB la fiecare 30s pentru joburi DEPLOYED noi.
    Afiseaza compact: telefon | nisa | website | firma + mesaj WA.
    Salveaza in CSV.
    """
    logger.info("[%s] Notifier pornit (manual WhatsApp mode)", _MARKET_FLAG)
    _ensure_csv()

    while True:
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Job).where(
                        Job.status == JobStatus.DEPLOYED,
                        Job.vercel_url.isnot(None),
                    )
                )
                jobs = result.scalars().all()

            for job in jobs:
                if job.id in _notified_ids:
                    continue

                # Filtreaza numere fixe si lead-uri de calitate slaba
                if not _is_mobile(job.phone_number):
                    _notified_ids.add(job.id)
                    continue
                if not _is_quality_lead(job.business_name):
                    _notified_ids.add(job.id)
                    continue

                _notified_ids.add(job.id)
                msg      = _build_message(job)
                clean    = re.sub(r"[^\d]", "", job.phone_number)
                wa_link  = f"https://wa.me/{clean}"
                ts       = datetime.now().strftime("%H:%M:%S")

                _append_csv(job, msg)

                # ── Output compact pentru Termux ──────────────────
                sep  = "═" * 58
                thin = "─" * 58
                print(f"\n{sep}")
                print(f"  [{_MARKET_FLAG}] SITE GATA  #{job.id}  [{ts}]")
                print(sep)
                print(f"  Firma   : {job.business_name}")
                print(f"  Telefon : {job.phone_number}")
                print(f"  Nisa    : {job.niche}")
                print(f"  Website : {job.vercel_url}")
                print(f"  WA Link : {wa_link}")
                print(f"  {thin}")
                print(f"  MESAJ WA:")
                for line in msg.split("\n"):
                    print(f"  {line}")
                print(f"  {thin}")
                print(f"  CSV: {_OUTREACH_CSV.resolve()}")
                print(f"{sep}\n")

                logger.info(
                    "[%s Job %d] NOTIFIED: '%s' | %s | %s",
                    _MARKET_FLAG, job.id,
                    job.business_name[:35], job.phone_number, job.vercel_url,
                )

        except Exception as exc:
            logger.error("[%s] Notifier eroare: %s", _MARKET_FLAG, exc, exc_info=True)

        await asyncio.sleep(30)


# ═══════════════════════════════════════════════════════════
# Status printer (la fiecare 5 min)
# ═══════════════════════════════════════════════════════════

async def loop_status() -> None:
    await asyncio.sleep(60)
    while True:
        try:
            async with AsyncSessionLocal() as session:
                counts: dict[str, int] = {}
                for st in JobStatus:
                    r = await session.execute(
                        select(func.count(Job.id)).where(Job.status == st)
                    )
                    counts[st.value] = r.scalar_one() or 0
            ts = datetime.now().strftime("%H:%M")
            summary = " | ".join(
                f"{k}={v}" for k, v in counts.items() if v > 0
            )
            print(f"\n  [{_MARKET_FLAG}] {ts}  {summary}\n")
        except Exception as exc:
            logger.error("Status loop eroare: %s", exc)
        await asyncio.sleep(300)


# ═══════════════════════════════════════════════════════════
# Validare configuratie
# ═══════════════════════════════════════════════════════════

def _validate_config() -> list[str]:
    errors = []
    groq_ok = bool(
        os.getenv("GROQ_API_KEYS") or os.getenv("GROQ_API_KEY") or settings.groq_api_key
    )
    if not groq_ok:
        errors.append("GROQ_API_KEYS lipseste din .env")
    if not settings.vercel_token:
        errors.append("VERCEL_TOKEN lipseste din .env")
    return errors


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════

async def main() -> None:
    errors = _validate_config()

    print(f"""
╔══════════════════════════════════════════════════════════╗
║      HYBRID KING v4 — {_MARKET_LABEL:<35}║
╠══════════════════════════════════════════════════════════╣
║  1. Scraper  → lead-uri noi                             ║
║  2. Deployer → genereaza site Groq + publica Vercel     ║
║  3. Notifier → afiseaza date + mesaj WA pentru tine     ║
║                                                          ║
║  WhatsApp auto-send = DEZACTIVAT (trimiti manual)       ║
╚══════════════════════════════════════════════════════════╝
""")

    if errors:
        print("❌  ERORI CONFIGURATIE:")
        for e in errors:
            print(f"    • {e}")
        print("\n    Editeaza .env si reporneaste.\n")
        sys.exit(1)

    print(f"  Market      : {_MARKET_LABEL}")
    print(f"  Database    : hybridking_{MARKET}.db")
    print(f"  CSV output  : {_OUTREACH_CSV.resolve()}")
    print(f"  Log         : {_LOG_FILE.resolve()}")
    print()

    await init_db()

    # Reseteaza joburi blocate din sesiuni anterioare
    async with AsyncSessionLocal() as session:
        stuck = await session.execute(
            update(Job)
            .where(Job.status == JobStatus.GENERATING)
            .values(status=JobStatus.SCRAPED)
        )
        if stuck.rowcount:
            logger.info("[%s] Reset %d joburi GENERATING->SCRAPED",
                        _MARKET_FLAG, stuck.rowcount)
        await session.commit()

    logger.info("[%s] Pornesc Scraper + Deployer + Notifier...", _MARKET_FLAG)

    results = await asyncio.gather(
        _run_scraper(total=200),
        loop_deployer(),
        loop_notifier(),
        loop_status(),
        return_exceptions=True,
    )

    names = ["scraper", "deployer", "notifier", "status"]
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            logger.critical("[%s] Task '%s' crashed: %s",
                            _MARKET_FLAG, names[i], res, exc_info=res)


# ═══════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    os.makedirs("logs", exist_ok=True)
    os.makedirs("sites", exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(str(_LOG_FILE), encoding="utf-8"),
        ],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n\n  Oprit manual [{_MARKET_FLAG}]. Lead-urile gata sunt in {_OUTREACH_CSV}\n")
