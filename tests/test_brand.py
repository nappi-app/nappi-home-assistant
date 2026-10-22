"""Home Assistant serves custom integrations' icons from their brand/ folder."""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

BRAND = Path(__file__).parents[1] / "custom_components" / "nappi" / "brand"


async def test_icons_are_served(hass: HomeAssistant, hass_client) -> None:
    assert await async_setup_component(hass, "brands", {})
    client = await hass_client()
    for image, size in (("icon.png", 256), ("icon@2x.png", 512)):
        resp = await client.get(f"/api/brands/integration/nappi/{image}")
        assert resp.status == HTTPStatus.OK, image
        body = await resp.read()
        assert body == (BRAND / image).read_bytes()
        # PNG IHDR: width and height at bytes 16-24.
        assert int.from_bytes(body[16:20]) == size
        assert int.from_bytes(body[20:24]) == size
