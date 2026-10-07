# DTEK Outages

Custom Home Assistant integration that listens for DTEK outage schedule updates
on Telegram and exposes the current and next day's schedule as sensor
attributes.

## Features

- Listens to configured Telegram channels using Telethon.
- Parses text schedule updates marked with `ОНОВЛЕННЯ ГРАФІКА`.
- Persists parsed schedules in Home Assistant's `.storage` directory.
- Exposes `schedule_today` and `schedule_tomorrow` on `sensor.dtek_outages`.
- Starts a reauthentication flow on the existing config entry if the Telegram
  session is no longer authorized.

## Requirements

- A Telegram account with access to the target channel or group.
- Telegram API ID and API hash from [my.telegram.org](https://my.telegram.org).
- Another device where that account is signed in to Telegram, to scan the login
  QR code, and its two-step verification password if enabled.

The integration's Python dependencies are installed by Home Assistant from
`manifest.json`.

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant.
2. Click the three dots in the top right corner and select **Custom repositories**.
3. Add `https://github.com/Tipasha/dtek-outages-ha` with category **Integration**.
4. Click **Download** on the integration card.
5. Restart Home Assistant.
6. Go to **Settings → Devices & Services → Add Integration**.
7. Search for **DTEK Outages** and follow the setup flow.

### Manual

1. Copy the `dtek_outages` folder to `/config/custom_components/` on your Home
  Assistant instance.
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration**.
4. Search for **DTEK Outages** and follow the setup flow.

Enter your Telegram API ID, API hash, and optional comma-separated channel
usernames or numeric IDs. Then, in Telegram on another signed-in device, open
**Settings → Devices → Link Desktop Device**, scan the QR code shown by Home
Assistant within 30 seconds, and select **Submit**. Enter the two-step
verification password if prompted.

If no channels are entered, the integration uses the default channel list in
`const.py`.

## Reauthentication

If the saved Telegram session becomes unauthorized, Home Assistant starts a
reauthentication flow for the existing DTEK Outages entry. Review or replace the
saved Telegram credentials, then scan a new QR code and enter the two-step
verification password if prompted. The existing integration entry and schedule
data are preserved.

## Sensor

The integration provides `sensor.dtek_outages`. Its state is `idle` between
updates and briefly changes to `new` while a schedule message is processed.

| Attribute | Description |
| --- | --- |
| `schedule_today` | Parsed schedule for the current date, or `null` if unavailable. |
| `schedule_tomorrow` | Parsed schedule for the next date, or `null` if unavailable. |
| `images` | Image URLs retained by the integration's image-processing path. |

Each schedule contains a `day` date string and an `hours` list of outage
intervals, for example `00:00-02:00`.

## Credentials and session data

Telegram API credentials are stored in the Home Assistant config entry. The
Telethon session is stored under the Home Assistant configuration directory;
neither belongs in this repository. Do not commit Telegram credentials,
Telethon session files, or Google service-account keys.