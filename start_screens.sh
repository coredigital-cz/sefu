#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  start_screens.sh — Porneste ambele instante in screen
#
#  Apeleaza o singura data pe server. Ruleaza nonstop in fundal.
#
#  Comenzi utile (din Termux sau SSH):
#    screen -r hk_cz    → ataseaza la instanta Cehia
#    screen -r hk_ro    → ataseaza la instanta Romania
#    Ctrl+A, D          → detaseaza (lasa sa ruleze)
#    screen -ls         → lista sesiuni active
#    ./stop_screens.sh  → opreste tot
# ══════════════════════════════════════════════════════════════
set -e

PROJ="$(cd "$(dirname "$0")" && pwd)"
VENV="$PROJ/venv/bin/activate"
PYTHON="$PROJ/venv/bin/python"

# Verifica ca venv exista
if [ ! -f "$VENV" ]; then
    echo "❌  venv nu exista! Ruleaza mai intai: ./setup.sh"
    exit 1
fi

# Verifica ca .env exista
if [ ! -f "$PROJ/.env" ]; then
    echo "❌  .env nu exista! Copiaza .env.example si completeaza."
    exit 1
fi

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║        HYBRID KING — Pornesc ambele instante        ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── Opreste sesiuni vechi daca exista ─────────────────────
for session in hk_cz hk_ro; do
    if screen -list | grep -q "$session"; then
        echo "  → Opresc sesiune veche: $session"
        screen -S "$session" -X quit 2>/dev/null || true
        sleep 1
    fi
done

# ── Creeaza directoare ────────────────────────────────────
mkdir -p "$PROJ/logs" "$PROJ/sites"

# ── Porneste instanta CEHIA ───────────────────────────────
echo "  → Pornesc hk_cz (Cehia — bazos.cz)..."
screen -dmS hk_cz bash -c "
    cd '$PROJ'
    source '$VENV'
    echo ''
    echo '  [CZ] Hybrid King Cehia pornit la: \$(date)'
    echo ''
    python main.py --market cz 2>&1 | tee -a logs/hybridking_cz.log
    echo ''
    echo '  [CZ] INSTANTA OPRITA. Apasa Enter pentru a iesi.'
    read
"
sleep 2

# ── Porneste instanta ROMANIA ─────────────────────────────
echo "  → Pornesc hk_ro (Romania — olx.ro)..."
screen -dmS hk_ro bash -c "
    cd '$PROJ'
    source '$VENV'
    echo ''
    echo '  [RO] Hybrid King Romania pornit la: \$(date)'
    echo ''
    python main.py --market ro 2>&1 | tee -a logs/hybridking_ro.log
    echo ''
    echo '  [RO] INSTANTA OPRITA. Apasa Enter pentru a iesi.'
    read
"
sleep 2

# ── Verifica ca au pornit ─────────────────────────────────
echo ""
echo "  Sesiuni active:"
screen -ls | grep -E "hk_(cz|ro)" | sed 's/^/  /' || echo "  (nicio sesiune gasita)"

echo ""
echo "══════════════════════════════════════════════════════"
echo "  Ambele instante ruleaza in fundal."
echo ""
echo "  Ataseaza-te din Termux / SSH:"
echo "    screen -r hk_cz    → Cehia (bazos.cz)"
echo "    screen -r hk_ro    → Romania (olx.ro)"
echo "    Ctrl+A, D          → detaseaza"
echo ""
echo "  Verifica lead-urile gata:"
echo "    python status.py"
echo ""
echo "  CSV-uri cu mesajele:"
echo "    $PROJ/outreach_cz.csv"
echo "    $PROJ/outreach_ro.csv"
echo "══════════════════════════════════════════════════════"
echo ""
