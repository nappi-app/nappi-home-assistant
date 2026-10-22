"""Every locale carries every string, with the same placeholders."""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

TRANSLATIONS = Path(__file__).parents[1] / "custom_components" / "nappi" / "translations"
LOCALES = ("en", "es", "pt", "it", "fr", "de")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _flatten(node: dict, prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten(value, path))
        else:
            flat[path] = value
    return flat


def _load(locale: str) -> dict[str, str]:
    return _flatten(json.loads((TRANSLATIONS / f"{locale}.json").read_text(encoding="utf-8")))


def test_only_the_six_app_locales_ship() -> None:
    assert sorted(path.stem for path in TRANSLATIONS.glob("*.json")) == sorted(LOCALES)


@pytest.mark.parametrize("locale", LOCALES[1:])
def test_locale_matches_english(locale: str) -> None:
    english = _load("en")
    other = _load(locale)
    assert other.keys() == english.keys()
    for key, text in other.items():
        assert text.strip(), f"{locale}: {key} is empty"
        assert set(PLACEHOLDER.findall(text)) == set(PLACEHOLDER.findall(english[key])), f"{locale}: {key}"
    # Real translations, not English copies (short brand-like labels excepted).
    copied = [k for k, v in other.items() if v == english[k] and len(v) > 12]
    assert not copied, f"{locale} copies English: {copied}"


@pytest.mark.parametrize("locale", LOCALES)
def test_no_em_dashes(locale: str) -> None:
    assert "\u2014" not in (TRANSLATIONS / f"{locale}.json").read_text(encoding="utf-8")
