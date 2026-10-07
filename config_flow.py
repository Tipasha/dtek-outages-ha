import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("phone"): str,
        vol.Required("api_id"): int,
        vol.Required("api_hash"): str,
        vol.Optional("channel", default=""): str,
    }
)


class DtekOutagesFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    def __init__(self):
        self._phone = None
        self._api_id = None
        self._api_hash = None
        self._channel = None
        self._sent = None
        self._reauth_entry = None
        self._session_file = f"{DOMAIN}_session_temp"

    async def async_step_user(self, user_input=None):
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        self._phone = user_input["phone"]
        self._api_id = user_input["api_id"]
        self._api_hash = user_input["api_hash"]
        self._channel = user_input.get("channel") or None

        return await self.async_step_send_code()

    async def async_step_reauth(self, entry_data):
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        if self._reauth_entry is None:
            return self.async_abort(reason="reauth_entry_missing")

        self._phone = entry_data.get("phone")
        self._api_id = entry_data.get("api_id")
        self._api_hash = entry_data.get("api_hash")
        self._channel = entry_data.get("channel") or None
        self._session_file = entry_data.get(
            "session_file", f"{DOMAIN}_session_temp"
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        if user_input is not None:
            self._phone = user_input["phone"]
            self._api_id = user_input["api_id"]
            self._api_hash = user_input["api_hash"]
            self._channel = user_input.get("channel") or None
            return await self.async_step_send_code()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required("phone", default=self._phone): str,
                    vol.Required("api_id", default=self._api_id): int,
                    vol.Required("api_hash", default=self._api_hash): str,
                    vol.Optional("channel", default=self._channel or ""): str,
                }
            ),
        )

    async def async_step_send_code(self, user_input=None):
        from telethon import TelegramClient

        client = TelegramClient(self._session_file, self._api_id, self._api_hash)
        await client.connect()

        try:
            if not await client.is_user_authorized():
                self._sent = await client.send_code_request(
                    self._phone, force_sms=False
                )
                return self.async_show_form(
                    step_id="verify_code",
                    data_schema=vol.Schema({vol.Required("code"): str}),
                )
            return self._create_entry()
        except Exception as exc:
            _LOGGER.exception("Error while sending code: %s", exc)
            return self.async_abort(reason="send_code_failed")
        finally:
            await client.disconnect()

    async def async_step_verify_code(self, user_input=None):
        from telethon import TelegramClient
        from telethon.errors import (
            PhoneCodeExpiredError,
            PhoneCodeInvalidError,
            SessionPasswordNeededError,
        )

        client = TelegramClient(self._session_file, self._api_id, self._api_hash)
        await client.connect()

        try:
            if user_input is None:
                return self.async_show_form(
                    step_id="verify_code",
                    data_schema=vol.Schema({vol.Required("code"): str}),
                )

            code = user_input["code"]

            try:
                await client.sign_in(
                    self._phone,
                    code,
                    phone_code_hash=self._sent.phone_code_hash,
                )
            except SessionPasswordNeededError:
                return self.async_show_form(
                    step_id="2fa",
                    data_schema=vol.Schema({vol.Required("password"): str}),
                )
            except PhoneCodeInvalidError:
                return self.async_show_form(
                    step_id="verify_code",
                    data_schema=vol.Schema({vol.Required("code"): str}),
                    errors={"base": "invalid_code"},
                )
            except PhoneCodeExpiredError:
                return self.async_show_form(
                    step_id="send_code", errors={"base": "code_expired"}
                )

        except Exception as exc:
            _LOGGER.exception("Error during sign_in: %s", exc)
            return self.async_abort(reason="sign_in_failed")
        finally:
            await client.disconnect()

        return self._create_entry()

    async def async_step_2fa(self, user_input=None):
        from telethon import TelegramClient
        from telethon.errors import PasswordHashInvalidError

        client = TelegramClient(self._session_file, self._api_id, self._api_hash)
        await client.connect()

        try:
            if user_input is None:
                return self.async_show_form(
                    step_id="2fa",
                    data_schema=vol.Schema({vol.Required("password"): str}),
                )

            password = user_input["password"]

            try:
                await client.sign_in(password=password)
            except PasswordHashInvalidError:
                return self.async_show_form(
                    step_id="2fa",
                    data_schema=vol.Schema({vol.Required("password"): str}),
                    errors={"base": "invalid_password"},
                )
        except Exception as exc:
            _LOGGER.exception("Error during 2FA sign_in: %s", exc)
            return self.async_abort(reason="2fa_failed")
        finally:
            await client.disconnect()

        return self._create_entry()

    @callback
    def _create_entry(self):
        data = {
            "api_id": self._api_id,
            "api_hash": self._api_hash,
            "phone": self._phone,
            "session_file": self._session_file,
            "channel": self._channel or "",
        }
        if self._reauth_entry is not None:
            return self.async_update_reload_and_abort(
                self._reauth_entry, data_updates=data
            )
        return self.async_create_entry(title="DTEK Outages", data=data)