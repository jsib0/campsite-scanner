# Yosemite Campground Scanner
> This project uses an undocumented Recreation.gov website endpoint. The endpoint
> may change or throttle requests without notice. An alert does not reserve a site;
> complete the reservation on Recreation.gov.


This command-line scanner watches Recreation.gov for campsites that are available
for every night of a requested Yosemite stay. It currently checks:

- Upper Pines
- Lower Pines
- North Pines
- Camp 4

It prints results in the terminal and can send alerts through either
[ntfy](https://ntfy.sh/) or Twilio SMS. On macOS, it also shows local Notification
Center alerts by default.


## Requirements

- Python 3.10 or newer
- Internet access
- For remote alerts, either an ntfy subscription or a Twilio account

The scanner otherwise uses only Python's standard library. If `certifi` is
installed, it will use that package's CA certificate bundle.

## Quick start with ntfy

1. Install the ntfy mobile app or open [ntfy.sh](https://ntfy.sh/).
2. Choose a hard-to-guess topic name and subscribe to it.
3. In a terminal, enter the project directory and export that same topic:

   ```bash
   cd /path/to/camp_scanner
   export NTFY_TOPIC="your-private-topic-name"
   ```

4. Send a test notification:

   ```bash
   python3 yosemite_scanner_sms.py --messenger ntfy --test-message
   ```

5. Start scanning every 25 seconds:

   ```bash
   python3 yosemite_scanner_sms.py --messenger ntfy --interval 25
   ```

Keep the terminal open while the scanner runs. Press `Ctrl+C` to stop it.

The interval defaults to 25 seconds, so `--interval 25` can be omitted.

Activity is also written to `logs/yosemite_scanner.log`. The log rotates at
midnight, and rotated files older than the seven retained daily backups are
deleted automatically.

## Select the dates

Check-in is inclusive and check-out is exclusive. For example, this scans for a
two-night stay on July 25 and July 26:

```bash
python3 yosemite_scanner_sms.py \
  --messenger ntfy \
  --check-in 2026-07-25 \
  --check-out 2026-07-27 \
  --people 2 \
  --interval 25
```

The built-in defaults are:

- Check-in: `2026-07-25`
- Check-out: omitted, so every available individual night from July 25 through
  July 31 is considered
- Interval: 25 seconds per campground

The minimum permitted interval is 10 seconds. The four campground checks are
staggered so they do not all contact Recreation.gov simultaneously.

When supplied, check-out is exclusive, just like a hotel reservation. A check-in of
July 25 and check-out of July 26 requests one occupied night: July 25, while July
25–27 requires the same site or enough Camp 4 person-spots on both nights.

When `--check-out` is omitted, the scanner uses flexible-date mode. It alerts
separately for every available individual night from check-in through the end of
that calendar month. This is the scanner's practical meaning of an "open" checkout;
Recreation.gov itself still requires a departure date when you make the reservation.

Camp 4 uses per-person inventory. Set `--people` to the number of people in your
party; an alert is sent only when every occupied night has at least that many
person-spots available. Other campgrounds continue to use site-level availability.

## ntfy configuration

The ntfy messenger supports these environment variables:

| Variable | Required | Description |
| --- | --- | --- |
| `NTFY_TOPIC` | Yes | Topic subscribed to by your ntfy clients |
| `NTFY_SERVER` | No | Server URL; defaults to `https://ntfy.sh` |
| `NTFY_TOKEN` | No | Bearer token for an authenticated topic/server |

Example with authentication:

```bash
export NTFY_SERVER="https://ntfy.sh"
export NTFY_TOPIC="your-private-topic-name"
export NTFY_TOKEN="tk_your_access_token"
python3 yosemite_scanner_sms.py --messenger ntfy
```

For the public server, notifications are visible at:

```text
https://ntfy.sh/your-private-topic-name
```

Public ntfy topics are not inherently private. Use a difficult-to-guess topic
name, or protect the topic with ntfy authentication.

## Twilio SMS configuration

Export all four Twilio variables:

```bash
export TWILIO_ACCOUNT_SID="your_account_sid"
export TWILIO_AUTH_TOKEN="your_auth_token"
export TWILIO_FROM_NUMBER="+15551234567"
export ALERT_TO_NUMBER="+15557654321"
```

Both phone numbers must use E.164 format, including the leading `+` and country
code.

Test Twilio:

```bash
python3 yosemite_scanner_sms.py --messenger twilio --test-message
```

Start scanning:

```bash
python3 yosemite_scanner_sms.py --messenger twilio
```

The older `--sms` and `--test-sms` flags remain available as Twilio aliases.

## Local-only scanning

To print openings in the terminal without ntfy or Twilio, omit `--messenger`:

```bash
python3 yosemite_scanner_sms.py \
  --check-in 2026-07-25 \
  --check-out 2026-07-26
```

On macOS, local Notification Center alerts are enabled unless you add:

```bash
--no-mac-notifications
```

There is also a local-only entry point with no remote-messenger support:

```bash
python3 yosemite_scanner.py
```

## Loading saved environment variables

The Python script reads variables from its environment; it does not automatically
load an `.env` file. If you maintain a shell settings file, load it before running
the scanner:

```bash
source settings.sh
python3 yosemite_scanner_sms.py --messenger ntfy
```

Do not commit files containing Twilio credentials, ntfy tokens, phone numbers, or
private topic names.

## How alerts behave

- On the first successful scan, every matching site is treated as newly available.
- Later alerts are sent only when a site appears that was absent from the previous
  successful scan.
- Each message includes the campground, campsite, requested dates, and a direct
  Recreation.gov campsite link.
- After HTTP rate limiting or other errors, the scanner backs off automatically and
  keeps running.

## Logs and debugging

By default, logs are stored relative to the application directory:

```text
logs/yosemite_scanner.log
```

The active log contains timestamps, severity levels, monitor thread names, scan
results, delivered alerts, errors, retry delays, and exception stack traces. It is
rotated every midnight, with seven daily backup files retained automatically.

Console log entries use this shape:

```text
2026-07-22 16:05:12 -0700 INFO     [monitor-232447] Upper Pines: Recreation.gov scan successful; 0 matching site(s)
```

Console colors make status easy to scan:

- Green: successful scans and delivered alerts
- Yellow: warnings
- Red: request failures, throttling, configuration errors, and other exceptions
- Bold red: critical failures

File logs deliberately contain no color escape codes, so they remain easy to
search and process. Use `--no-color` if the terminal does not support ANSI colors.

Watch the active log while the scanner runs:

```bash
tail -f logs/yosemite_scanner.log
```

Use a different location when starting the scanner:

```bash
python3 yosemite_scanner_sms.py \
  --messenger ntfy \
  --log-file /path/to/yosemite-scanner.log
```

The directory is created automatically if it does not exist.

## Command reference

View every supported option with:

```bash
python3 yosemite_scanner_sms.py --help
```

Common options:

| Option | Purpose |
| --- | --- |
| `--messenger ntfy` | Send remote alerts with ntfy |
| `--messenger twilio` | Send remote alerts with Twilio SMS |
| `--test-message` | Send one test alert and exit |
| `--check-in YYYY-MM-DD` | First occupied date |
| `--check-out YYYY-MM-DD` | Exact-stay departure; omit for any night through month-end |
| `--interval SECONDS` | Check frequency per campground; minimum 10 |
| `--people NUMBER` | Required Camp 4 person-spots; default 1 |
| `--no-mac-notifications` | Disable local macOS notifications |
| `--ca-bundle PATH` | Use a specific TLS CA certificate bundle |
| `--log-file PATH` | Override the default rotating log location |
| `--no-color` | Disable ANSI colors in console logs |

## Troubleshooting

**No ntfy notification appears**

- Confirm the app/browser subscription exactly matches `NTFY_TOPIC`.
- Run with `--test-message` before starting the scanner.
- If using a protected topic, confirm `NTFY_TOKEN` is valid.
- If self-hosting ntfy, confirm `NTFY_SERVER` includes `http://` or `https://`.

**The scanner reports zero matching sites**

This means no single campsite is currently available for every occupied night in
the requested range. It does not necessarily indicate an error.

**Recreation.gov rejects or throttles a request**

HTTP 429 responses are logged in red with the server's `Retry-After` delay, and
the affected campground monitor backs off automatically. Other HTTP failures,
timeouts, DNS/TLS failures, malformed JSON, and unexpected response structures are
also logged in red with exponential retry delays. Successful campground scans turn
green, confirming that a valid response was received and processed.

**macOS notifications do not appear**

Allow notifications for the terminal application in macOS System Settings. Remote
ntfy or Twilio alerts work independently of macOS Notification Center.

**TLS certificate errors occur**

Install `certifi`, set `SSL_CERT_FILE`, or pass an explicit bundle:

```bash
python3 yosemite_scanner_sms.py --ca-bundle /path/to/cacert.pem
```
