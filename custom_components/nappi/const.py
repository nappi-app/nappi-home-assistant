"""Constants for the nappi integration."""

from __future__ import annotations

from datetime import timedelta
import re

DOMAIN = "nappi"
VERSION = "0.1.0"

API_BASE_URL = "https://api.nappi.app/v1"
USER_AGENT = f"HomeAssistant-nappi/{VERSION}"
KEYS_URL = "https://nappi.app/app/connections"

# The API asks for no more than one poll a minute. Each poll costs two reads
# (latest entries + timers) out of the key's 12 reads a minute.
SCAN_INTERVAL = timedelta(minutes=1)

KEY_PATTERN = re.compile(r"^nappi_[0-9a-f]{48}$")

CONF_BABY_ID = "baby_id"
CONF_BABY_NAME = "baby_name"
CONF_BOTTLE_UNIT = "bottle_unit"

UNIT_ML = "ml"
UNIT_OZ = "oz"
DEFAULT_BOTTLE_UNIT = UNIT_ML

TIMER_TYPES = ("sleep", "nursing", "pumping")

ENTRY_TYPES = (
    "sleep",
    "nursing",
    "bottle",
    "diaper",
    "potty",
    "pumping",
    "solids",
    "activity",
    "growth",
    "medication",
)

BOTTLE_TYPES = (
    "breast_milk",
    "formula",
    "tube_feeding",
    "cow_milk",
    "goat_milk",
    "soy_milk",
    "other",
)
BOTTLE_TYPE_UNSPECIFIED = "unspecified"

ISSUE_PREMIUM_REQUIRED = "premium_required"
ISSUE_BABY_NOT_FOUND = "baby_not_found"
