"""
groq_rotator.py — Groq API Key Rotation
=========================================
Gestioneaza mai multe chei Groq.
La 429 (rate limit), roteste automat la urmatoarea cheie.

Config in .env:
    GROQ_API_KEYS=gsk_key1,gsk_key2,gsk_key3
    (sau legacy: GROQ_API_KEY=gsk_key1)
"""
from __future__ import annotations

import itertools
import logging
import os

logger = logging.getLogger("hybridking.groq_rotator")


class GroqKeyRotator:
    def __init__(self) -> None:
        # Suporta: GROQ_API_KEYS=k1,k2,k3 SAU GROQ_API_KEY=k1
        raw = os.getenv("GROQ_API_KEYS", "") or os.getenv("GROQ_API_KEY", "")
        keys = [k.strip().strip('"').strip("'")
                for k in raw.split(",") if k.strip()]

        if not keys:
            raise RuntimeError(
                "Nicio cheie Groq configurata! "
                "Seteaza GROQ_API_KEYS in .env"
            )

        self._keys: list[str] = keys
        self._cycle = itertools.cycle(keys)
        self._current: str = next(self._cycle)
        self._idx: int = 0
        self._rotations: int = 0

        logger.info(
            "Groq key rotator gata: %d cheie(i) configurate",
            len(keys),
        )

    @property
    def current(self) -> str:
        return self._current

    @property
    def count(self) -> int:
        return len(self._keys)

    @property
    def total_rotations(self) -> int:
        return self._rotations

    def rotate(self) -> str:
        """Trece la urmatoarea cheie. Returneaza noua cheie."""
        prev_idx = self._idx
        self._current = next(self._cycle)
        self._idx = (self._idx + 1) % len(self._keys)
        self._rotations += 1
        logger.warning(
            "Rotit cheie Groq: slot %d -> slot %d  "
            "(total rotatii: %d / %d chei)",
            prev_idx, self._idx, self._rotations, len(self._keys),
        )
        return self._current

    def status(self) -> str:
        return (
            f"slot_activ={self._idx}/{len(self._keys)-1}  "
            f"rotatii={self._rotations}"
        )


# ── Singleton global ─────────────────────────────────────────
_rotator: GroqKeyRotator | None = None


def get_rotator() -> GroqKeyRotator:
    global _rotator
    if _rotator is None:
        _rotator = GroqKeyRotator()
    return _rotator


def current_key() -> str:
    return get_rotator().current


def rotate_key() -> str:
    return get_rotator().rotate()
