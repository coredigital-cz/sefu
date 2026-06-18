#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  Hybrid King — Ubuntu Setup Script
#  Ruleaza o singura data pe masina noua:
#    chmod +x setup.sh && ./setup.sh
# ══════════════════════════════════════════════════════════════
set -e

PROJ_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON=python3
VENV_DIR="$PROJ_DIR/venv"
LOG_DIR="$PROJ_DIR/logs"
SITES_DIR="$PROJ_DIR/sites"
TEMPLATES_DIR="$PROJ_DIR/templates"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║         HYBRID KING — Setup Ubuntu                  ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── 1. Verifica Python 3.10+ ──────────────────────────────
echo "→ Verific Python..."
PY_VER=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo "  Python $PY_VER prea vechi. Instalez Python 3.11..."
    sudo apt-get update -qq
    sudo apt-get install -y python3.11 python3.11-venv python3.11-dev python3-pip
    PYTHON=python3.11
fi
echo "  ✅  Python $($PYTHON --version)"

# ── 2. Instalare dependente sistem ────────────────────────
echo ""
echo "→ Instalez dependente sistem (lxml, etc.)..."
sudo apt-get update -qq
sudo apt-get install -y \
    python3-venv \
    python3-pip \
    libxml2-dev \
    libxslt1-dev \
    build-essential \
    git \
    curl \
    --no-install-recommends -qq
echo "  ✅  Dependente sistem instalate"

# ── 3. Virtualenv ─────────────────────────────────────────
echo ""
echo "→ Creez virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    $PYTHON -m venv "$VENV_DIR"
    echo "  ✅  venv creat: $VENV_DIR"
else
    echo "  ✅  venv exista deja"
fi

source "$VENV_DIR/bin/activate"

# ── 4. Upgrade pip + instalare requirements ───────────────
echo ""
echo "→ Instalez Python packages..."
pip install --upgrade pip -q
pip install -r "$PROJ_DIR/requirements.txt" -q
echo "  ✅  Toate pachetele instalate"

# ── 5. Creeaza directoare ─────────────────────────────────
echo ""
echo "→ Creez directoare..."
mkdir -p "$LOG_DIR" "$SITES_DIR" "$TEMPLATES_DIR"
echo "  ✅  logs/, sites/, templates/"

# ── 6. Copiaza .env ───────────────────────────────────────
echo ""
if [ ! -f "$PROJ_DIR/.env" ]; then
    cp "$PROJ_DIR/.env.example" "$PROJ_DIR/.env"
    echo "  ✅  .env creat din .env.example"
    echo ""
    echo "  ⚠️  ACUM EDITEAZA .env cu cheile tale API:"
    echo "      nano $PROJ_DIR/.env"
else
    echo "  ✅  .env exista deja"
fi

# ── 7. Verifica fisierele sursa ───────────────────────────
echo ""
echo "→ Verific fisierele sursa..."
MISSING_FILES=0
for f in factory.py orchestrator.py scraper_b2c.py database.py; do
    if [ -f "$PROJ_DIR/$f" ]; then
        echo "  ✅  $f"
    else
        echo "  ❌  LIPSESTE: $f — copiaza-l in $PROJ_DIR"
        MISSING_FILES=$((MISSING_FILES + 1))
    fi
done

# ── 8. Creeaza script de pornire ──────────────────────────
echo ""
echo "→ Creez run.sh..."
cat > "$PROJ_DIR/run.sh" << 'RUNSCRIPT'
#!/usr/bin/env bash
# Porneste Hybrid King
PROJ_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$PROJ_DIR/venv/bin/activate"
cd "$PROJ_DIR"
echo "Pornesc Hybrid King... (Ctrl+C pentru oprire)"
python main.py
RUNSCRIPT
chmod +x "$PROJ_DIR/run.sh"
echo "  ✅  run.sh creat"

# ── 9. Summary ────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════"
if [ "$MISSING_FILES" -gt 0 ]; then
    echo "  ⚠️  Copiaza fisierele lipsa in $PROJ_DIR"
    echo "      Apoi editeaza .env cu API keys"
    echo "      Apoi ruleaza: ./run.sh"
else
    echo "  🎉  Setup complet!"
    echo ""
    echo "  Pasi urmatori:"
    echo "  1. Editeaza .env:  nano $PROJ_DIR/.env"
    echo "  2. Porneste:       cd $PROJ_DIR && ./run.sh"
    echo ""
    echo "  Lead-urile gata de trimis apar in terminal"
    echo "  si se salveaza in: outreach_queue.csv"
fi
echo "══════════════════════════════════════════════════════"
echo ""
