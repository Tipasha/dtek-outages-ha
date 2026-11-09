import aiohttp
import base64
import jwt
import logging
import re
import time
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import SA_KEY_PATH, SA_SCOPES, UA_MONTHS
from .helper import async_read_image_base64, async_read_json

_LOGGER = logging.getLogger(__name__)

_MONTH_PATTERN = "|".join(re.escape(month) for month in UA_MONTHS)
_DATE_PATTERN = re.compile(
    rf"\b(?:"
    rf"(?P<day>\d{{1,2}})\s+(?P<month>{_MONTH_PATTERN})(?:\s+(?P<year>\d{{4}}))?"
    rf"|(?P<numeric_day>\d{{1,2}})[./](?P<numeric_month>\d{{1,2}})[./](?P<numeric_year>\d{{4}})"
    rf")\b",
    re.IGNORECASE,
)
_TIME_RANGE_PATTERN = re.compile(
    r"(?<!\d)(\d{1,2}:\d{2})\s*(?:->|→|-|–|—|−|━|─|до)\s*(\d{1,2}:\d{2})(?!\d)",
    re.IGNORECASE,
)
_OUTAGE_MARKERS = (
    "❌",
    "❎",
    "✖",
    "⛔",
    "без світла",
    "без електроенергії",
    "немає електроенергії",
    "відсутнє електропостачання",
    "відключ",
    "знеструм",
)
_POWER_MARKERS = ("💡", "✅", "☀", "зі світлом", "є світло", "світло є")


async def get_access_token(hass, key_path):
    sa = await async_read_json(key_path)

    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    payload = {
        "iss": sa["client_email"],
        "scope": " ".join(SA_SCOPES),
        "aud": sa["token_uri"],
        "iat": now,
        "exp": now + 3600,
    }

    signed_jwt = jwt.encode(
        payload, sa["private_key"], algorithm="RS256", headers=header
    )

    session = async_get_clientsession(hass)

    async with session.post(
        sa["token_uri"],
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": signed_jwt,
        },
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
        return data["access_token"]


def extract_date(text: str) -> str | None:
    match = _DATE_PATTERN.search(text)
    if not match:
        return None

    if match.group("day"):
        day = int(match.group("day"))
        month = UA_MONTHS[match.group("month").lower()]
        year = int(match.group("year")) if match.group("year") else datetime.now().year
    else:
        day = int(match.group("numeric_day"))
        month = int(match.group("numeric_month"))
        year = int(match.group("numeric_year"))

    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None


def extract_hours(text: str) -> list[str]:
    """Extract outage intervals as HH:MM-HH:MM strings."""
    pattern = r"\D*(\d{1,2}:\d{2})\D*(\d{1,2}:\d{2})"
    return [f"{start}-{end}" for start, end in re.findall(pattern, text)]


def _outage_status(line: str) -> bool | None:
    """Return whether a schedule line marks an outage, power, or unknown status."""
    normalized = line.casefold()
    if any(marker in normalized for marker in _OUTAGE_MARKERS):
        return True
    if any(marker in normalized for marker in _POWER_MARKERS):
        return False
    return None


def _extract_text_outage_hours(section: str) -> list[str]:
    intervals: list[tuple[str, bool | None]] = []
    for line in section.splitlines():
        status = _outage_status(line)
        intervals.extend(
            (f"{start}-{end}", status)
            for start, end in _TIME_RANGE_PATTERN.findall(line)
        )

    if not intervals:
        return []

    if all(status is not None for _, status in intervals):
        return [interval for interval, status in intervals if status]

    return [interval for interval, status in intervals if status is True]


async def parse_schedule_from_image(hass: HomeAssistant, path: str) -> dict | None:
    try:
        encoded_image = await async_read_image_base64(path)

        key_path = hass.config.path(SA_KEY_PATH)
        token = await get_access_token(hass, key_path)

        session = async_get_clientsession(hass)
        async with session.post(
            "https://vision.googleapis.com/v1/images:annotate",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "requests": [
                    {
                        "image": {"content": encoded_image},
                        "features": [{"type": "TEXT_DETECTION"}],
                    }
                ]
            },
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

        text_annotations = data.get("responses", [{}])[0].get("textAnnotations", [])
        if not text_annotations:
            return None

        ocr_text = text_annotations[0]["description"]
    except Exception as err:
        _LOGGER.error("Google Vision HTTP OCR failed for %s: %s", path, err)
        return None

    _LOGGER.info("%s", ocr_text)

    date = extract_date(ocr_text)
    if not date:
        _LOGGER.warning("No date detected in schedule image %s", path)
        date = datetime.now().strftime("%Y-%m-%d")

    match = re.search(
        r"5\.1\s+Черга(?P<block>[\s\S]+?)(?:\d+\.\d+\s+Черга|$)",
        ocr_text,
    )
    if not match:
        _LOGGER.warning("Block '5.1 Черга' not found in %s", path)
        return {"day": date, "hours": []}

    hours = extract_hours(match.group("block"))

    return {"day": date, "hours": hours}


async def parse_schedule_from_text(text: str) -> list[dict] | None:
    matches = list(_DATE_PATTERN.finditer(text))

    if not matches:
        _LOGGER.warning("No dates found in schedule text")
        return None

    results = []

    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)

        section = text[start:end]

        date = extract_date(section)
        if not date:
            continue

        hours = _extract_text_outage_hours(section)

        results.append({"day": date, "hours": hours})

    return results