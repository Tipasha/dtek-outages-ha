import asyncio
import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from telethon import TelegramClient
from telethon.errors import (
    ApiIdInvalidError,
    PasswordHashInvalidError,
    SessionPasswordNeededError,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("api_id"): int,
        vol.Required("api_hash"): str,
        vol.Optional("channel", default=""): str,
    }
)


class DtekOutagesFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    def __init__(self):
        self._api_id = None
        self._api_hash = None
        self._channel = None
        self._reauth_entry = None
        self._session_file = f"{DOMAIN}_session"
        self._client = None
        self._qr_login = None
        self._qr_task = None

    async def async_step_user(self, user_input=None):
        if user_input is None:
            return self._credentials_form("user")

        self._store_credentials(user_input)
        return await self._async_begin_login("user")

    async def async_step_reauth(self, entry_data):
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        if self._reauth_entry is None:
            return self.async_abort(reason="reauth_entry_missing")

        self._store_credentials(entry_data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        if user_input is None:
            return self._credentials_form("reauth_confirm")

        self._store_credentials(user_input)
        return await self._async_begin_login("reauth_confirm")

    async def async_step_qr(self, user_input=None):
        if user_input is None:
            return self._show_qr()

        status = await self._async_check_qr()
        if status == "done":
            return await self._async_finish()
        if status == "2fa":
            return await self.async_step_2fa()
        if status == "qr_failed":
            return self.async_abort(reason="qr_login_failed")
        if status == "qr_expired":
            try:
                await self._async_renew_qr()
            except Exception:
                _LOGGER.exception("Could not refresh the Telegram QR code")
                return self.async_abort(reason="qr_login_failed")

        return self._show_qr({"base": status})

    async def async_step_2fa(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                await self._client.sign_in(password=user_input["password"])
            except PasswordHashInvalidError:
                errors["base"] = "invalid_password"
            except Exception:
                _LOGGER.exception("Telegram two-step verification failed")
                return self.async_abort(reason="2fa_failed")
            else:
                return await self._async_finish()

        return self.async_show_form(
            step_id="2fa",
            data_schema=vol.Schema({vol.Required("password"): str}),
            errors=errors,
        )

    def _store_credentials(self, data):
        self._api_id = data.get("api_id")
        self._api_hash = data.get("api_hash")
        self._channel = data.get("channel") or None

    def _credentials_form(self, step_id, errors=None):
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA,
                {
                    "api_id": self._api_id,
                    "api_hash": self._api_hash,
                    "channel": self._channel or "",
                },
            ),
            errors=errors or {},
        )

    async def _async_begin_login(self, form_step):
        try:
            if await self._async_start_qr():
                return await self._async_finish()
        except ApiIdInvalidError:
            return self._credentials_form(form_step, {"base": "invalid_api"})
        except Exception:
            _LOGGER.exception("Could not start Telegram QR login")
            return self.async_abort(reason="qr_login_failed")

        return self._show_qr()

    async def _async_start_qr(self):
        """Return True when the session is already authorized."""
        await self._async_close_client()
        self._client = TelegramClient(
            self.hass.config.path(self._session_file), self._api_id, self._api_hash
        )
        await self._client.connect()
        if await self._client.is_user_authorized():
            return True

        self._qr_login = await self._client.qr_login()
        self._qr_task = self._watch(self._qr_login.wait())
        return False

    async def _async_renew_qr(self):
        await self._qr_login.recreate()
        self._qr_task = self._watch(self._qr_login.wait())

    def _watch(self, coro):
        task = self.hass.async_create_task(coro)
        # Retrieve the exception so an abandoned flow does not log it as unhandled.
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
        return task

    async def _async_check_qr(self):
        task = self._qr_task
        if task is None:
            return "qr_expired"

        await asyncio.wait({task}, timeout=3)
        if not task.done():
            return "qr_not_scanned"

        try:
            task.result()
        except SessionPasswordNeededError:
            return "2fa"
        except TimeoutError:
            return "qr_expired"
        except Exception:
            _LOGGER.exception("Telegram QR login failed")
            return "qr_failed"
        return "done"

    def _show_qr(self, errors=None):
        return self.async_show_form(
            step_id="qr",
            data_schema=vol.Schema(
                {
                    vol.Optional("qr_code"): selector.QrCodeSelector(
                        selector.QrCodeSelectorConfig(
                            data=self._qr_login.url,
                            scale=6,
                            error_correction_level=selector.QrErrorCorrectionLevel.QUARTILE,
                        )
                    )
                }
            ),
            errors=errors or {},
        )

    async def _async_finish(self):
        await self._async_close_client()
        return self._create_entry()

    async def _async_close_client(self):
        if self._qr_task is not None and not self._qr_task.done():
            self._qr_task.cancel()
        self._qr_task = None
        client, self._client = self._client, None
        if client is not None:
            await client.disconnect()

    @callback
    def async_remove(self):
        if self._qr_task is not None and not self._qr_task.done():
            self._qr_task.cancel()
        if self._client is not None:
            self.hass.async_create_task(self._client.disconnect())

    @callback
    def _create_entry(self):
        data = {
            "api_id": self._api_id,
            "api_hash": self._api_hash,
            "session_file": self._session_file,
            "channel": self._channel or "",
        }
        if self._reauth_entry is not None:
            return self.async_update_reload_and_abort(
                self._reauth_entry, data_updates=data
            )
        return self.async_create_entry(title="DTEK Outages", data=data)