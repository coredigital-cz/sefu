#!/usr/bin/env python3
"""
status.py — Hybrid King Status (Termux / telefon)
===================================================
Vizualizare compacta a lead-urilor gata de trimis.
Foloseste SQLite direct (fara SQLAlchemy) — ruleaza instant.

Usage:
    python status.py              # ambele piete (CZ + RO)
    python status.py --market cz  # doar Cehia
    python status.py --market ro  # doar Romania
    python status.py --all        # toate job-urile (nu doar DEPLOYED)
    python status.py --pending    # SCRAPED in asteptare
"""
import argparse
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

# ════════════════════════════════════════════════════════════
# Configuratie
# ════════════════════════════════════════════════════════════

MARKETS = {
    "cz": {
        "db":    "hybridking_cz.db",
        "csv":   "outreach_cz.csv",
        "label": "CZ - bazos.cz",
    },
    "ro": {
        "db":    "hybridking_ro.db",
        "csv":   "outreach_ro.csv",
        "label": "RO - olx.ro",
    },
}


# ════════════════════════════════════════════════════════════
# Query SQLite direct
# ════════════════════════════════════════════════════════════

def query_jobs(db_path: str, status_filter: str | None = "DEPLOYED") -> list[dict]:
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        if status_filter:
            cur.execute(
                """
                SELECT id, business_name, phone_number, niche,
                       vercel_url, language, status, updated_at
                FROM jobs
                WHERE status = ? AND vercel_url IS NOT NULL
                ORDER BY updated_at DESC
                """,
                (status_filter,),
            )
        else:
            cur.execute(
                """
                SELECT id, business_name, phone_number, niche,
                       vercel_url, language, status, updated_at
                FROM jobs
                WHERE vercel_url IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT 50
                """
            )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def query_counts(db_path: str) -> dict[str, int]:
    if not Path(db_path).exists():
        return {}
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status")
        return {row[0]: row[1] for row in cur.fetchall()}
    finally:
        conn.close()


def query_pending(db_path: str) -> list[dict]:
    """Joburi SCRAPED in coada (fara site inca)."""
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, business_name, phone_number, niche, created_at
            FROM jobs WHERE status = 'SCRAPED'
            ORDER BY created_at DESC LIMIT 20
            """
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════
# Display helpers
# ════════════════════════════════════════════════════════════

def _wa_link(phone: str) -> str:
    clean = re.sub(r"[^\d]", "", phone)
    return f"https://wa.me/{clean}"


def print_header(markets_shown: list[str], counts_all: dict) -> None:
    w = 58
    now = datetime.now().strftime("%H:%M:%S")
    print("=" * w)
    print(f"  HYBRID KING STATUS  [{now}]")
    print("=" * w)
    for m in markets_shown:
        cfg    = MARKETS[m]
        counts = counts_all.get(m, {})
        total  = sum(counts.values())
        dep    = counts.get("DEPLOYED", 0)
        scraped= counts.get("SCRAPED", 0)
        sent   = counts.get("OUTREACH_SENT", 0)
        print(
            f"  [{m.upper()}] {cfg['label']:<20} | "
            f"GATA={dep}  IN_COADA={scraped}  TRIMIS={sent}  TOTAL={total}"
        )
    print("=" * w)


def print_job(market: str, job: dict, idx: int) -> None:
    thin = "-" * 58
    ts   = (job.get("updated_at") or "")[:16]
    print(f"\n  [{market.upper()} #{job['id']}]  {ts}")
    print(f"  Firma   : {job['business_name']}")
    print(f"  Telefon : {job['phone_number']}")
    print(f"  Nisa    : {job['niche']}")
    if job.get("vercel_url"):
        print(f"  Website : {job['vercel_url']}")
    print(f"  WA Link : {_wa_link(job['phone_number'])}")
    print(f"  {thin}")


def print_pending_job(market: str, job: dict) -> None:
    ts = (job.get("created_at") or "")[:16]
    print(f"  [{market.upper()} #{job['id']}]  {job['business_name'][:40]:<40}  "
          f"{job['phone_number']}  {ts}")


# ════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hybrid King Status — Termux view"
    )
    parser.add_argument(
        "--market", choices=["cz", "ro", "both"], default="both",
        help="Piata de afisat"
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Arata toate job-urile cu site, nu doar DEPLOYED"
    )
    parser.add_argument(
        "--pending", action="store_true",
        help="Arata job-urile SCRAPED in asteptare (fara site)"
    )
    args = parser.parse_args()

    markets_shown = (
        ["cz", "ro"] if args.market == "both"
        else [args.market]
    )

    # Colecteaza counts
    counts_all: dict[str, dict[str, int]] = {}
    for m in markets_shown:
        counts_all[m] = query_counts(MARKETS[m]["db"])

    print_header(markets_shown, counts_all)

    if args.pending:
        # Mod PENDING: arata joburi in coada
        print(f"\n  JOBURI IN COADA (SCRAPED — fara site inca):")
        print("  " + "-" * 56)
        found_any = False
        for m in markets_shown:
            jobs = query_pending(MARKETS[m]["db"])
            for job in jobs:
                print_pending_job(m, job)
                found_any = True
        if not found_any:
            print("  (niciun job in coada)")
        return

    # Mod default: arata job-uri DEPLOYED (gata de trimis)
    status_filter = None if args.all else "DEPLOYED"
    label = "TOATE (cu site)" if args.all else "GATA DE TRIMIS (DEPLOYED)"

    print(f"\n  {label}:")
    print("  " + "=" * 56)

    total_shown = 0
    for m in markets_shown:
        jobs = query_jobs(MARKETS[m]["db"], status_filter)
        if not jobs:
            print(f"\n  [{m.upper()}] Niciun job {label.lower()}.")
            continue
        for i, job in enumerate(jobs, 1):
            print_job(m, job, i)
            total_shown += 1

    if total_shown == 0:
        print("\n  Niciun lead gata de trimis momentan.")
        print("  Asteapta sa ruleze scraper-ul + deployer-ul.")
    else:
        print(f"\n  Total afisat: {total_shown} lead-uri")

    # Afiseaza locatia CSV-urilor
    print("\n  CSV-uri cu mesajele complete:")
    for m in markets_shown:
        csv_path = Path(MARKETS[m]["csv"]).resolve()
        exists   = "✅" if csv_path.exists() else "❌ (nu exista inca)"
        print(f"  [{m.upper()}] {csv_path}  {exists}")

    print()


if __name__ == "__main__":
    main()
