# nappi for Home Assistant

Log diapers and bottles, run sleep, nursing and pumping timers, and see the latest entries for your baby in Home Assistant. No YAML: install, paste a connection key, pick a baby.

It uses the public nappi API (`https://api.nappi.app/v1`), which is part of nappi Premium and in beta.

## What you get

One device per baby, with:

| Entity | What it does |
| --- | --- |
| **Log wet diaper**, **Log dirty diaper**, **Log mixed diaper** (buttons) | Log a diaper change now. |
| **Log bottle** (button) | Log a bottle of the **Bottle amount**, as **Bottle contents**. |
| **Bottle amount** (number), **Bottle contents** (select) | Settings for the bottle button, kept across restarts. Milliliters by default; switch to ounces under *Configure*. |
| **Start nursing left**, **Start nursing right**, **Start nursing both** (buttons) | Start the nursing timer on that side, for the whole household. They only start a feed: if nursing is already running, nothing changes, and it keeps its side and the seconds counted on each side. Stop the timer first to start on another side. |
| **Sleep timer**, **Nursing timer**, **Pumping timer** (switches) | On while that timer runs, whether it was started here, on a phone, a watch or Alexa. Turning one on starts the timer for the whole household; turning it off stops it and saves the entry, as stopping it on the phone would. Attributes: `started_at`, `paused`, `elapsed_seconds` (as of the last poll), and the nursing side. |
| **Last sleep**, **Last nursing**, **Last bottle**, **Last diaper**, **Last pumping** (sensors) | When the newest entry of that type happened. Details such as the amount, side or diaper type are attributes. **Last sleep** is when the sleep ended; `start_time` is an attribute. |
| **Last potty**, **Last solids**, **Last activity**, **Last growth measurement**, **Last medicine** (sensors) | The same for other types. Off by default; turn them on from the device page. |

Entity names are translated into English, Spanish, Portuguese, Italian, French and German.

## Install

### HACS

nappi is in the HACS default store. In HACS, search for **nappi**, open it, choose **Download**, then restart Home Assistant. Or open it straight from here:

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=nappi-app&repository=nappi-home-assistant&category=integration)

### By hand

Copy `custom_components/nappi` into your Home Assistant `config/custom_components/` folder and restart.

## Set up

1. Sign in at [nappi.app/app/connections](https://nappi.app/app/connections) and create a connection key. Choosing **1 year** saves you from reconnecting often. The key is shown only once, so copy it.
2. In Home Assistant, go to **Settings > Devices & services > Add integration** and search for **nappi**, or use this button:

   [![Open your Home Assistant instance and start setting up nappi.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=nappi)

3. Paste the key. If the key's household has more than one baby, pick one.

For twins or siblings, add the integration again with the same key and pick the next baby.

## Voice control with Google Home or Alexa

The switches and buttons can be exposed to a voice assistant through Home Assistant. Then "Hey Google, turn on Phoebe Sleep timer" starts the sleep timer, and "Hey Google, activate Phoebe Log wet diaper" logs a diaper. Names follow your Home Assistant entity names, so rename them to whatever is easiest to say.

For Google Home there are three ways to connect: Home Assistant Cloud (easiest, paid), a Matter bridge (free and local, needs a Google hub) or Home Assistant's own Google Assistant setup (free, technical). The step-by-step guide is at [nappi.app/smart-home/google-home](https://nappi.app/smart-home/google-home).

## Polling and limits

The API allows 12 reads and 30 writes a minute per key, and asks that you poll no more than once a minute. This integration makes one request per baby every two minutes (`GET /status`, which returns the running timers and the latest entries together) and sends the last ETag, so an unchanged answer is a small 304. After you press a button or turn a timer on or off it reads again at once, so the sensors update straight away; a change made on a phone, a watch or Alexa shows up within two minutes. If the server says to slow down, it waits as long as the `Retry-After` header asks before polling again.

Buttons stay available even if the last poll failed, so a slow or rate-limited read never stops you logging. Every bottle and diaper press carries its own `Idempotency-Key`, and a press that loses its connection is retried once with the same key, so it is never logged twice.

## When something goes wrong

- **Key expired or revoked, or its owner left the household:** Home Assistant asks you to reconnect. Create a new key and paste it. Every baby set up with the old key gets the new one.
- **Premium ended:** a repair appears under **Settings > Repairs**. Renew Premium in the nappi app and the integration reconnects by itself within two minutes.
- **Baby removed from the household:** a repair appears. Delete the entry, or add the integration again with a key from the right household.

## Development

The source of truth is `integrations/home-assistant` in the nappi monorepo; this repository is published from it. Its layout is a HACS repository root (`hacs.json`, `custom_components/nappi`, `tests`, `.github/workflows`). Tests run with [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component) on Python 3.14:

```bash
uv venv --python 3.14 .venv
```

```bash
uv pip install --python .venv -r requirements_test.txt
```

```bash
.venv/bin/python -m pytest
```

The tests run against a fake of the API (`tests/conftest.py`), not the live service.
