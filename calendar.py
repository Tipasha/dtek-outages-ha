"""Calendar platform for DTEK outage schedules."""

import logging
import os
from datetime import date, datetime, time, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.storage import STORAGE_DIR
from homeassistant.util import dt as dt_util

from .const import SIGNAL_SCHEDULE_UPDATED
from .helper import async_read_json

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(minutes=15)


class DtekOutagesCalendar(CalendarEntity):
    """Expose the parsed DTEK outage intervals as calendar events."""

    _attr_name = "DTEK Outages"
    _attr_unique_id = "dtek_outages_calendar"

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._attr_event: CalendarEvent | None = None
        self._storage_dir = hass.config.path(os.path.join(STORAGE_DIR))

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next upcoming or currently active outage."""
        return self._attr_event

    async def async_added_to_hass(self) -> None:
        """Refresh when new outage schedules are stored."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_SCHEDULE_UPDATED, self._schedule_updated
            )
        )

    @callback
    def _schedule_updated(self) -> None:
        self.async_schedule_update_ha_state(force_refresh=True)

    async def async_update(self) -> None:
        """Refresh the calendar's next-event state."""
        now = dt_util.now()
        events = await self.async_get_events(self.hass, now, now + timedelta(days=2))
        self._attr_event = next((event for event in events if event.end > now), None)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return outage intervals overlapping the requested time range."""
        timezone = dt_util.now().tzinfo
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone)

        day = start_date.astimezone(timezone).date() - timedelta(days=1)
        last_day = end_date.astimezone(timezone).date()
        events = []

        while day <= last_day:
            path = os.path.join(self._storage_dir, f"dtek_schedule_{day}.json")
            if os.path.exists(path):
                try:
                    schedule = await async_read_json(path)
                except (OSError, ValueError) as err:
                    _LOGGER.warning(
                        "Unable to read DTEK schedule for %s: %s", day, err
                    )
                else:
                    if not isinstance(schedule, dict):
                        _LOGGER.warning("Invalid DTEK schedule data for %s", day)
                    elif schedule.get("day") == day.isoformat():
                        events.extend(self._events_for_day(schedule, day))
            day += timedelta(days=1)

        return sorted(
            (
                event
                for event in events
                if event.start < end_date and event.end > start_date
            ),
            key=lambda event: event.start,
        )

    def _events_for_day(self, schedule: dict, day: date) -> list[CalendarEvent]:
        """Convert a stored day's outage intervals to calendar events."""
        hours = schedule.get("hours", [])
        if not isinstance(hours, list):
            _LOGGER.warning("Invalid outage intervals for %s: expected a list", day)
            return []

        events = []
        timezone = dt_util.now().tzinfo
        for index, interval in enumerate(hours):
            try:
                start_text, end_text = interval.split("-", maxsplit=1)
                start_time = datetime.strptime(start_text, "%H:%M").time()
                if end_text == "24:00":
                    end = datetime.combine(
                        day + timedelta(days=1), time.min, tzinfo=timezone
                    )
                else:
                    end_time = datetime.strptime(end_text, "%H:%M").time()
                    end = datetime.combine(day, end_time, tzinfo=timezone)

                start = datetime.combine(day, start_time, tzinfo=timezone)
                if end_text != "24:00" and end <= start:
                    end += timedelta(days=1)
            except (AttributeError, TypeError, ValueError):
                _LOGGER.warning(
                    "Ignoring invalid DTEK outage interval for %s: %r", day, interval
                )
                continue

            events.append(
                CalendarEvent(
                    summary="DTEK outage (queue 5.1)",
                    start=start,
                    end=end,
                    uid=f"dtek-outage-{day}-{index}",
                )
            )

        return events


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up the DTEK outage calendar."""
    async_add_entities([DtekOutagesCalendar(hass)], True)
