import asyncio
import io
import json
import logging
import os
import shutil
from datetime import datetime, timedelta

from telethon import TelegramClient, events

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import STORAGE_DIR

from .const import DEFAULT_CHANNELS, SAVE_DIR_NAME
from .helper import async_read_json, async_write_json
from .schedule_parser import parse_schedule_from_image, parse_schedule_from_text

_LOGGER = logging.getLogger(__name__)


class DtekOutagesSensor(SensorEntity):
    def __init__(self, hass: HomeAssistant, client):
        self.hass = hass
        self._client = client
        self._state = "unknown"
        self._images = []

        self._save_dir = hass.config.path(f"www/{SAVE_DIR_NAME}")
        os.makedirs(self._save_dir, exist_ok=True)

        self._storage_dir = hass.config.path(os.path.join(STORAGE_DIR))
        os.makedirs(self._storage_dir, exist_ok=True)

        self._attr_name = "DTEK Outages"
        self._attr_unique_id = "dtek_outages_sensor"

        self._schedule_today = None
        self._schedule_tomorrow = None

    @property
    def state(self):
        return self._state

    @property
    def extra_state_attributes(self):
        return {
            "images": self._images,
            "schedule_today": self._schedule_today,
            "schedule_tomorrow": self._schedule_tomorrow,
        }

    async def async_added_to_hass(self):
        """Called when entity is added to HA; restore saved schedules."""
        today_dt = datetime.now()
        today = today_dt.strftime("%Y-%m-%d")
        tomorrow = (today_dt + timedelta(days=1)).strftime("%Y-%m-%d")

        self._schedule_today = await self._load_schedule(today)
        self._schedule_tomorrow = await self._load_schedule(tomorrow)
        self._state = "idle"
        self.async_write_ha_state()

    async def _load_schedule(self, day: str) -> dict | None:
        path = os.path.join(self._storage_dir, f"dtek_schedule_{day}.json")
        if not os.path.exists(path):
            return None

        try:
            return await async_read_json(path)
        except Exception as err:
            _LOGGER.error("Failed to load schedule for %s: %s", day, err)
            return None

    async def _save_schedule(self, schedule: dict):
        day = schedule["day"]
        path = os.path.join(self._storage_dir, f"dtek_schedule_{day}.json")

        try:
            await async_write_json(path, schedule)

            def cleanup_old_files():
                for file in os.listdir(self._storage_dir):
                    if file.startswith("dtek_schedule_") and not file.endswith(
                        f"{day}.json"
                    ):
                        file_date = file[len("dtek_schedule_") : -len(".json")]
                        if (
                            datetime.now() - datetime.strptime(file_date, "%Y-%m-%d")
                        ).days > 30:
                            os.remove(os.path.join(self._storage_dir, file))

            await self.hass.async_add_executor_job(cleanup_old_files)

            _LOGGER.info("Saved schedule for %s", day)
        except Exception as err:
            _LOGGER.error("Failed to save schedule: %s", err)

    async def _download_to_file(self, media, target_path):
        buf = io.BytesIO()

        await self._client.download_media(media, file=buf)

        buf.seek(0)

        def write_file():
            with open(target_path, "wb") as file:
                file.write(buf.read())

        await self.hass.async_add_executor_job(write_file)

        return target_path

    async def _handle_new_message(self, event):
        text = getattr(event.message, "message", "") or ""
        if "Київщина: графіки" not in text:
            return

        if os.path.exists(self._save_dir):
            try:
                await self.hass.async_add_executor_job(shutil.rmtree, self._save_dir)
            except Exception as err:
                _LOGGER.exception("Failed to clear save dir: %s", err)
        os.makedirs(self._save_dir, exist_ok=True)

        messages = [event.message]

        if event.message.grouped_id:
            try:
                async for msg in self._client.iter_messages(
                    entity=event.chat_id,
                    limit=10,
                ):
                    if msg.grouped_id == event.message.grouped_id:
                        messages.append(msg)
            except Exception as err:
                _LOGGER.exception("Failed to fetch album messages: %s", err)

        unique_messages = {message.id: message for message in messages}.values()
        messages = sorted(unique_messages, key=lambda message: message.id)

        images = []
        index = 1

        for message in messages:
            media = message.media
            if not media:
                continue

            try:
                target = os.path.join(self._save_dir, f"{index}.png")
                path = await self._download_to_file(message.media, target)

                if not path:
                    _LOGGER.warning("No media was downloaded to: %s", target)
                    continue

                images.append(f"/local/{SAVE_DIR_NAME}/{os.path.basename(path)}")
                index += 1
            except Exception as err:
                _LOGGER.exception("Failed to download media: %s", err)

        self._state = "new"
        self._images = images

        for image in images:
            img_path = os.path.join(self._save_dir, image.split("/")[-1])
            schedule = await parse_schedule_from_image(self.hass, img_path)

            if schedule:
                await self._save_schedule(schedule)
                await self._update_current_and_tomorrow()

                if len(schedule["hours"]) > 0:
                    break

        self.async_write_ha_state()

        await asyncio.sleep(1)
        self._state = "idle"
        self.async_write_ha_state()

    async def _handle_new_message_v2(self, event):
        text = getattr(event.message, "message", "") or ""
        if "ОНОВЛЕННЯ ГРАФІКА" not in text:
            return

        _LOGGER.info(
            "Message ID %s contains schedule update text. Processing...",
            event.message.id,
        )
        _LOGGER.info("Message: %s", text)

        try:
            self._state = "new"

            schedule_dates = await parse_schedule_from_text(text)

            _LOGGER.info("Parsed dates: %s", schedule_dates)

            if schedule_dates:
                for schedule in schedule_dates:
                    await self._save_schedule(schedule)
                    await self._update_current_and_tomorrow()

            self.async_write_ha_state()

            await asyncio.sleep(1)
            self._state = "idle"
            self.async_write_ha_state()
        except Exception as err:
            _LOGGER.exception("Failed to process new message: %s", err)

    async def _update_current_and_tomorrow(self):
        today_dt = datetime.now()
        today = today_dt.strftime("%Y-%m-%d")
        tomorrow = (today_dt + timedelta(days=1)).strftime("%Y-%m-%d")

        self._schedule_today = await self._load_schedule(today)
        self._schedule_tomorrow = await self._load_schedule(tomorrow)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
):
    api_id = entry.data["api_id"]
    api_hash = entry.data["api_hash"]
    session_file = entry.data["session_file"]
    channels = entry.data.get("channel") or DEFAULT_CHANNELS

    if isinstance(channels, str):
        parsed = []
        for channel in channels.split(","):
            channel = channel.strip()
            if channel.startswith("-100") or channel.lstrip("-").isdigit():
                parsed.append(int(channel))
            else:
                parsed.append(channel)
        channels = parsed

    session_path = hass.config.path(session_file)

    _LOGGER.info("session_path: %s", session_path)

    if not os.path.exists(session_path + ".session"):
        _LOGGER.warning("Telethon session not found. Complete config flow first.")
        return

    client = TelegramClient(session_path, api_id, api_hash, sequential_updates=True)

    sensor = DtekOutagesSensor(hass, client)

    @client.on(events.NewMessage(chats=channels))
    async def _on_new_message(event):
        await sensor._handle_new_message_v2(event)

    async def _start_client():
        try:
            await client.connect()
            if not await client.is_user_authorized():
                _LOGGER.warning(
                    "Telethon client is not authorized; starting reauthentication"
                )
                await client.disconnect()
                entry.async_start_reauth(hass)
                return
            _LOGGER.info("Telethon client connected for dtek_outages")
            await client.run_until_disconnected()
        except Exception as err:
            _LOGGER.exception("Telethon client error: %s", err)

    hass.loop.create_task(_start_client())
    async_add_entities([sensor], True)