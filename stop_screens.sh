#!/usr/bin/env bash
# stop_screens.sh — Opreste ambele instante screen

echo ""
echo "Opresc instante Hybrid King..."

for session in hk_cz hk_ro; do
    if screen -list 2>/dev/null | grep -q "$session"; then
        screen -S "$session" -X quit
        echo "  ✅  Oprit: $session"
    else
        echo "  –   Nu ruleaza: $session"
    fi
done

echo ""
echo "  Toate sesiunile oprite."
echo "  Lead-urile deja gasite sunt in outreach_cz.csv si outreach_ro.csv"
echo ""
