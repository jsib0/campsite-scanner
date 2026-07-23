# Yosemite Campground Scanner

This command-line application monitors Recreation.gov for Yosemite campsite
availability. It currently checks:

- Upper Pines
- Lower Pines
- North Pines
- Camp 4

It can print openings in the terminal, show macOS notifications, and send remote
alerts through [ntfy](https://ntfy.sh/) or Twilio SMS.

> Recreation.gov's availability endpoint is not a documented public API. It may
> change or throttle requests without notice. An alert does not reserve a site;
> complete the reservation on Recreation.gov.

## Requirements

- Python 3.10 or newer
- Internet access
- An ntfy subscription or Twilio account for remote alerts

The scanner uses Python's standard library. If `certifi` is installed, its CA
certificate bundle is used automatically.

## Install

For a simple isolated installation, use
[pipx](https://pipx.pypa.io/):

```bash
pipx install git+https://github.com/jsib0/camp_scanner.git
```

This installs the `camp-scanner` command.

For development:

```bash
git clone https://github.com/jsib0/camp_scanner.git
cd camp_scanner
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

You can then use either entry point:

```bash
camp-scanner --help
python -m camp_scanner --help
```

## Quick start with ntfy

1. Install the ntfy mobile app or open [ntfy.sh](https://ntfy.sh/).
2. Choose a hard-to-guess topic name and subscribe to it.
3. Export the same topic:

   ```bash
   export NTFY_TOPIC="your-private-topic-name"
   ```

4. Send a test notification:

   ```bash
   camp-scanner --messenger ntfy --test-message
   ```

5. Start scanning:

   ```bash
   camp-scanner --messenger ntfy
   ```

Keep the terminal open while the scanner runs. Press `Ctrl+C` to stop it.

## Environment settings

The application reads secrets and settings from environment variables. Never
commit actual credentials, phone numbers, tokens, or private ntfy topics.

For local development, copy the committed template:

```bash
cp .env.example .env
```

Fill in the values you use, then load the file explicitly:

```bash
camp-scanner --env-file .env --messenger ntfy
```

The local `.env` file is excluded by `.gitignore`.

To keep settings outside the repository, set a permanent path:

```bash
export CAMP_SCANNER_ENV_FILE="$HOME/.config/camp-scanner/env"
camp-scanner --messenger ntfy
```

Explicitly exported environment variables take precedence over values loaded from
an environment file.

During development, the optional shell helper exports the project `.env` into the
current shell:

```bash
source ./load-env.sh
camp-scanner --messenger ntfy
```

## ntfy configuration

| Variable | Required | Description |
| --- | --- | --- |
| `NTFY_TOPIC` | Yes | Topic subscribed to by your ntfy clients |
| `NTFY_SERVER` | No | Server URL; defaults to `https://ntfy.sh` |
| `NTFY_TOKEN` | No | Bearer token for an authenticated topic or server |

Public ntfy topics are not inherently private. Use a difficult-to-guess topic or
protect it with ntfy authentication.

## Twilio configuration

Twilio requires all four variables:

```bash
export TWILIO_ACCOUNT_SID="your_account_sid"
export TWILIO_AUTH_TOKEN="your_auth_token"
export TWILIO_FROM_NUMBER="+15551234567"
export ALERT_TO_NUMBER="+15557654321"
```

Both phone numbers must use E.164 format, including the leading `+` and country
code.

Test the configuration:

```bash
camp-scanner --messenger twilio --test-message
```

The older `--sms` and `--test-sms` options remain available as Twilio aliases.

## Select dates

Check-in is inclusive and check-out is exclusive. This example scans for a
two-night stay on July 25 and July 26:

```bash
camp-scanner \
  --messenger ntfy \
  --check-in 2026-07-25 \
  --check-out 2026-07-27
```

The current defaults are:

- Check-in: `2026-07-25`
- Check-out: omitted
- Interval: 40 seconds per campground

When `--check-out` is omitted, the scanner alerts separately for every available
individual night from check-in through the end of that calendar month.

The minimum permitted interval is 10 seconds. The four campground checks are
staggered to avoid simultaneous requests.

*Camp 4* reports per-person inventory. The scanner alerts on any availability
reported by Recreation.gov.

## Local-only scanning

Omit `--messenger` to use terminal and macOS notifications without sending a
remote message:

```bash
camp-scanner \
  --check-in 2026-07-25 \
  --check-out 2026-07-26
```

Disable macOS Notification Center alerts with:

```bash
camp-scanner --no-mac-notifications
```

## Alert behavior

- Every match from the first successful scan is treated as newly available.
- Later alerts are sent only when a site appears after being absent from the
  previous successful scan.
- Matching scan results and openings appear yellow in the terminal.
- Each alert includes the campground, site number, number of nights, date, and
  direct Recreation.gov campsite URL.
- The scanner backs off automatically after rate limits and other request errors.

Example:

```text
Upper Pines 42 is open for 1 night on Jul 25, 2026
https://www.recreation.gov/camping/campsites/123456
```

## Logs

Logs rotate at midnight and retain seven daily backups. The default location is:

- macOS: `~/Library/Logs/camp-scanner/camp-scanner.log`
- Linux: `~/.local/state/camp-scanner/camp-scanner.log`
- Windows: `%LOCALAPPDATA%\camp-scanner\camp-scanner.log`

Override the path when starting the scanner:

```bash
camp-scanner --log-file /path/to/camp-scanner.log
```

Console colors:

- Green: successful scans with no matches and delivered alerts
- Yellow: matching scans and campsite openings
- Red: throttling, request failures, and configuration errors

File logs do not contain ANSI color codes. Use `--no-color` if the terminal does
not support colors.

## Command reference

```bash
camp-scanner --help
```

| Option | Purpose |
| --- | --- |
| `--messenger ntfy` | Send remote alerts through ntfy |
| `--messenger twilio` | Send remote alerts through Twilio SMS |
| `--test-message` | Send one test alert and exit |
| `--check-in YYYY-MM-DD` | First occupied date |
| `--check-out YYYY-MM-DD` | Departure date; omit for flexible-date mode |
| `--interval SECONDS` | Polling interval per campground; minimum 10 |
| `--env-file PATH` | Load local environment settings from a file |
| `--no-mac-notifications` | Disable local macOS notifications |
| `--ca-bundle PATH` | Use a specific TLS CA certificate bundle |
| `--log-file PATH` | Override the rotating log location |
| `--no-color` | Disable terminal colors |

## Troubleshooting

### No ntfy notification appears

- Confirm the subscription exactly matches `NTFY_TOPIC`.
- Run with `--test-message` before starting the scanner.
- Confirm `NTFY_TOKEN` is valid when using an authenticated topic.
- Confirm `NTFY_SERVER` includes `http://` or `https://`.

### The scanner reports zero matches

No campsite currently satisfies the selected dates. A successful green scan means
the response was received and processed correctly.

### Recreation.gov throttles requests

HTTP 429 responses and retry delays are logged. The affected monitor backs off
automatically instead of repeatedly sending requests.

### macOS notifications do not appear

Allow notifications for the terminal application in macOS System Settings.
Remote ntfy and Twilio alerts work independently of Notification Center.

### TLS certificate errors occur

Install the optional TLS dependency:

```bash
pipx inject camp-scanner certifi
```

You can instead set `SSL_CERT_FILE` or pass a bundle explicitly:

```bash
camp-scanner --ca-bundle /path/to/cacert.pem
```
