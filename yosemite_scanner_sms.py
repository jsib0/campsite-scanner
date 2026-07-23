#!/usr/bin/env python3
"""Poll Recreation.gov for Yosemite campsite cancellations and send alerts.

Default stay:
    check-in:  2026-07-25
    check-out: open (search each available night through the end of July)

The script checks Upper Pines, Lower Pines, North Pines, and Camp 4. It uses
Recreation.gov's website availability endpoint, which is not a documented public
API and may change or throttle requests without notice.

Remote alerts can use Twilio SMS or ntfy. Store credentials in environment
variables; do not hard-code them in this file.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import platform
import ssl
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://www.recreation.gov"
TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"
AVAILABLE_STATUS = "Available"
REQUEST_TIMEOUT_SECONDS = 15
MAX_ERROR_BACKOFF_SECONDS = 15 * 60
DEFAULT_LOG_FILE = Path(__file__).resolve().parent / "logs" / "yosemite_scanner.log"
LOGGER = logging.getLogger("yosemite_scanner")


class ColorFormatter(logging.Formatter):
    """Add ANSI colors to console records without contaminating log files."""

    COLORS = {
        logging.DEBUG: "\033[36m",  # cyan
        logging.INFO: "\033[32m",  # green
        logging.WARNING: "\033[33m",  # yellow
        logging.ERROR: "\033[31m",  # red
        logging.CRITICAL: "\033[1;31m",  # bold red
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        color = self.COLORS.get(record.levelno, "")
        return f"{color}{formatted}{self.RESET}" if color else formatted


@dataclass(frozen=True)
class Campground:
    name: str
    campground_id: str

    @property
    def booking_url(self) -> str:
        return f"{BASE_URL}/camping/campgrounds/{self.campground_id}"


@dataclass(frozen=True)
class SmsConfig:
    account_sid: str
    auth_token: str
    from_number: str
    to_number: str


@dataclass(frozen=True)
class NtfyConfig:
    server: str
    topic: str
    token: str | None


CAMPGROUNDS = (
    Campground("Upper Pines", "232447"),
    Campground("Lower Pines", "232450"),
    Campground("North Pines", "232449"),
    Campground("Camp 4", "10004152"),
)


class RateLimitedError(RuntimeError):
    def __init__(self, retry_after_seconds: float, details: str = "") -> None:
        message = f"Rate limited; retry after {retry_after_seconds:.0f}s"
        if details:
            message += f": {details}"
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
        self.details = details


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scan Upper Pines, Lower Pines, North Pines, and Camp 4 and "
            "optionally alert on newly available sites."
        )
    )
    parser.add_argument(
        "--check-in",
        type=date.fromisoformat,
        default=date(2026, 7, 25),
        help="Arrival date in YYYY-MM-DD format (default: 2026-07-25).",
    )
    parser.add_argument(
        "--check-out",
        type=date.fromisoformat,
        help=(
            "Departure date in YYYY-MM-DD format. If omitted, alert on any "
            "available individual night from check-in through month-end."
        ),
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=40.0,
        help="Seconds between checks of each campground (default: 25).",
    )
    parser.add_argument(
        "--messenger",
        choices=("twilio", "ntfy"),
        help="Remote alert service to use (default: no remote alerts).",
    )
    parser.add_argument(
        "--sms",
        action="store_true",
        help=(
            "Deprecated alias for --messenger twilio. Requires TWILIO_ACCOUNT_SID, "
            "TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, and ALERT_TO_NUMBER."
        ),
    )
    parser.add_argument(
        "--test-message",
        action="store_true",
        help="Send one test alert and exit. Requires --messenger.",
    )
    parser.add_argument(
        "--test-sms",
        action="store_true",
        help="Deprecated alias for --messenger twilio --test-message.",
    )
    parser.add_argument(
        "--no-mac-notifications",
        action="store_true",
        help="Disable macOS Notification Center alerts.",
    )
    parser.add_argument(
        "--ca-bundle",
        type=Path,
        help=(
            "Optional CA certificate bundle. Otherwise SSL_CERT_FILE, "
            "certifi, or the operating-system trust store is used."
        ),
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=DEFAULT_LOG_FILE,
        help=f"Log file path (default: {DEFAULT_LOG_FILE}).",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored console log output.",
    )
    args = parser.parse_args()

    if args.check_out and args.check_out <= args.check_in:
        parser.error("--check-out must be later than --check-in")
    if args.interval < 10:
        parser.error("--interval must be at least 10 seconds")

    if args.sms or args.test_sms:
        if args.messenger and args.messenger != "twilio":
            parser.error("--sms/--test-sms cannot be combined with --messenger ntfy")
        args.messenger = "twilio"
    if args.test_sms:
        args.test_message = True
    if args.test_message and not args.messenger:
        parser.error("--test-message requires --messenger")

    return args


def configure_logging(log_file: Path, color: bool = True) -> None:
    """Log to the terminal and rotate daily files retained for seven days."""
    try:
        log_file = log_file.expanduser().resolve()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            log_file,
            when="midnight",
            interval=1,
            backupCount=7,
            encoding="utf-8",
        )
    except OSError as exc:
        raise RuntimeError(f"Cannot open log file {log_file}: {exc}") from exc

    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S %z",
        )
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_format = "%(asctime)s %(levelname)-8s [%(threadName)s] %(message)s"
    console_formatter_class = ColorFormatter if color else logging.Formatter
    console_handler.setFormatter(
        console_formatter_class(console_format, datefmt="%Y-%m-%d %H:%M:%S %z")
    )

    LOGGER.setLevel(logging.DEBUG)
    LOGGER.handlers.clear()
    LOGGER.addHandler(file_handler)
    LOGGER.addHandler(console_handler)
    LOGGER.propagate = False
    LOGGER.debug("Logging initialized at %s", log_file)


def ssl_context(ca_bundle: Path | None) -> ssl.SSLContext:
    """Build a verified TLS context using the best available CA bundle."""
    if ca_bundle:
        if not ca_bundle.is_file():
            raise RuntimeError(f"CA bundle does not exist: {ca_bundle}")
        return ssl.create_default_context(cafile=str(ca_bundle))

    env_bundle = os.environ.get("SSL_CERT_FILE")
    if env_bundle and Path(env_bundle).is_file():
        return ssl.create_default_context(cafile=env_bundle)

    try:
        import certifi  # type: ignore[import-not-found]

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def load_sms_config(required: bool) -> SmsConfig | None:
    names = (
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_FROM_NUMBER",
        "ALERT_TO_NUMBER",
    )
    values = {name: os.environ.get(name, "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]

    if missing:
        if required:
            raise RuntimeError(
                "SMS is enabled, but these environment variables are missing: "
                + ", ".join(missing)
            )
        return None

    for name in ("TWILIO_FROM_NUMBER", "ALERT_TO_NUMBER"):
        value = values[name]
        if not value.startswith("+") or not value[1:].isdigit():
            raise RuntimeError(
                f"{name} must use E.164 format, for example +18185551234"
            )

    return SmsConfig(
        account_sid=values["TWILIO_ACCOUNT_SID"],
        auth_token=values["TWILIO_AUTH_TOKEN"],
        from_number=values["TWILIO_FROM_NUMBER"],
        to_number=values["ALERT_TO_NUMBER"],
    )


def load_ntfy_config(required: bool) -> NtfyConfig | None:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        if required:
            raise RuntimeError("ntfy is enabled, but NTFY_TOPIC is missing")
        return None

    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").strip().rstrip("/")
    if not server.startswith(("https://", "http://")):
        raise RuntimeError("NTFY_SERVER must start with https:// or http://")
    if not all(character.isalnum() or character in "-_" for character in topic):
        raise RuntimeError(
            "NTFY_TOPIC may contain only letters, numbers, hyphens, and underscores"
        )

    return NtfyConfig(
        server=server,
        topic=topic,
        token=os.environ.get("NTFY_TOKEN", "").strip() or None,
    )


def booking_nights(check_in: date, check_out: date) -> list[date]:
    """Return every occupied night; check-out is exclusive."""
    nights: list[date] = []
    current = check_in
    while current < check_out:
        nights.append(current)
        current += timedelta(days=1)
    return nights


def first_day_of_next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def month_starts_for_stay(check_in: date, check_out: date) -> list[date]:
    """Return the first day of every month touched by the occupied nights."""
    last_night = check_out - timedelta(days=1)
    current = check_in.replace(day=1)
    last_month = last_night.replace(day=1)
    months: list[date] = []

    while current <= last_month:
        months.append(current)
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)

    return months


def retry_after_seconds(value: str | None, default: float = 300.0) -> float:
    if not value:
        return default

    try:
        return max(float(value), 1.0)
    except ValueError:
        pass

    try:
        retry_time = parsedate_to_datetime(value)
        if retry_time.tzinfo is None:
            retry_time = retry_time.replace(tzinfo=timezone.utc)
        return max((retry_time - datetime.now(timezone.utc)).total_seconds(), 1.0)
    except (TypeError, ValueError, OverflowError):
        return default


def http_error_details(exc: HTTPError) -> str:
    """Extract a short, safe diagnostic from an HTTP error response."""
    try:
        raw_body = exc.read(2048)
        body = raw_body.decode("utf-8", errors="replace").strip()
    except OSError:
        return ""

    if not body:
        return ""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return " ".join(body.split())[:500]
    if isinstance(payload, dict):
        detail = payload.get("error") or payload.get("message") or payload.get("detail")
        if detail:
            return str(detail)[:500]
    return str(payload)[:500]


def fetch_month(
    campground: Campground,
    month_start: date,
    tls_context: ssl.SSLContext,
) -> dict[str, Any]:
    endpoint = (
        f"{BASE_URL}/api/camps/availability/campground/"
        f"{campground.campground_id}/month"
    )
    query = urlencode(
        {"start_date": f"{month_start.isoformat()}T00:00:00.000Z"}
    )
    request = Request(
        f"{endpoint}?{query}",
        headers={
            "Accept": "application/json",
            "User-Agent": "PersonalYosemiteAvailabilityScanner/2.0",
        },
        method="GET",
    )

    try:
        with urlopen(
            request,
            timeout=REQUEST_TIMEOUT_SECONDS,
            context=tls_context,
        ) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            raw_body = response.read()
            payload = json.loads(raw_body.decode(charset))
            if not isinstance(payload, dict):
                raise RuntimeError(
                    "Recreation.gov returned an unexpected JSON root "
                    f"({type(payload).__name__}, expected object)"
                )
            LOGGER.debug(
                "Recreation.gov HTTP %s: %s, month %s, %d response bytes",
                response.status,
                campground.name,
                month_start.isoformat(),
                len(raw_body),
            )
            return payload
    except HTTPError as exc:
        details = http_error_details(exc)
        if exc.code == 429:
            delay = retry_after_seconds(exc.headers.get("Retry-After"))
            raise RateLimitedError(delay, details) from exc
        suffix = f": {details}" if details else ""
        raise RuntimeError(
            f"Recreation.gov HTTP {exc.code} {exc.reason}{suffix}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Recreation.gov network/TLS error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("Recreation.gov request timed out") from exc
    except UnicodeDecodeError as exc:
        raise RuntimeError("Recreation.gov returned undecodable text") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Recreation.gov returned invalid JSON") from exc


def campsite_url(site_id: str) -> str:
    """Return Recreation.gov's individual campsite page."""
    return f"https://www.Recreation.gov/camping/campsites/{site_id}"


def find_available_sites(
    campground: Campground,
    check_in: date,
    check_out: date | None,
    tls_context: ssl.SSLContext,
) -> list[dict[str, str]]:
    """Return sites available for every occupied night of the requested stay."""
    combined_sites: dict[str, dict[str, Any]] = {}

    search_end = check_out or first_day_of_next_month(check_in)
    for month_start in month_starts_for_stay(check_in, search_end):
        payload = fetch_month(campground, month_start, tls_context)
        if "campsites" not in payload:
            raise RuntimeError(
                "Unexpected Recreation.gov response: missing 'campsites' field"
            )
        campsites = payload["campsites"]

        if not isinstance(campsites, dict):
            raise RuntimeError(
                "Unexpected Recreation.gov response: 'campsites' is not an object"
            )

        for site_id, site_data in campsites.items():
            if not isinstance(site_data, dict):
                raise RuntimeError(
                    "Unexpected Recreation.gov response: campsite data is not an object"
                )

            existing = combined_sites.setdefault(
                str(site_id),
                {
                    "site_id": str(site_id),
                    "site": str(site_data.get("site") or site_id),
                    "campsite_type": str(site_data.get("campsite_type") or "Unknown"),
                    "availabilities": {},
                    "quantities": {},
                },
            )

            availabilities = site_data.get("availabilities") or {}
            if not isinstance(availabilities, dict):
                raise RuntimeError(
                    "Unexpected Recreation.gov response: 'availabilities' is not an object"
                )
            existing["availabilities"].update(availabilities)

            quantities = site_data.get("quantities") or {}
            if not isinstance(quantities, dict):
                raise RuntimeError(
                    "Unexpected Recreation.gov response: 'quantities' is not an object"
                )
            existing["quantities"].update(quantities)

    wanted_nights = booking_nights(check_in, search_end)
    matches: list[dict[str, str]] = []

    for site in combined_sites.values():
        statuses_by_day = {
            timestamp[:10]: status
            for timestamp, status in site["availabilities"].items()
            if isinstance(timestamp, str)
        }

        quantity_by_day = {
            timestamp[:10]: quantity
            for timestamp, quantity in site["quantities"].items()
            if isinstance(timestamp, str) and isinstance(quantity, (int, float))
        }
        has_quantity_inventory = bool(site["quantities"])
        available_nights = [
            night
            for night in wanted_nights
            if statuses_by_day.get(night.isoformat()) == AVAILABLE_STATUS
        ]
        qualifying_stays = (
            [wanted_nights]
            if check_out is not None and len(available_nights) == len(wanted_nights)
            else [[night] for night in available_nights] if check_out is None else []
        )

        for stay_nights in qualifying_stays:
            minimum_quantity = (
                min(quantity_by_day[night.isoformat()] for night in stay_nights)
                if has_quantity_inventory
                else None
            )
            available_date = stay_nights[0] if check_out is None else None
            matches.append(
                {
                    "site_id": site["site_id"],
                    "match_id": (
                        f"{site['site_id']}:{available_date.isoformat()}"
                        if available_date
                        else site["site_id"]
                    ),
                    "site": site["site"],
                    "campsite_type": site["campsite_type"],
                    "url": campsite_url(site["site_id"]),
                    "quantity": (
                        str(int(minimum_quantity))
                        if minimum_quantity is not None
                        else ""
                    ),
                    "available_date": (
                        available_date.isoformat() if available_date else ""
                    ),
                }
            )

    return sorted(matches, key=lambda item: (item["available_date"], item["site"]))


def send_sms(
    sms: SmsConfig,
    body: str,
    tls_context: ssl.SSLContext,
) -> str:
    """Send one SMS through Twilio and return the Twilio message SID."""
    endpoint = f"{TWILIO_API_BASE}/Accounts/{sms.account_sid}/Messages.json"
    form = urlencode(
        {
            "To": sms.to_number,
            "From": sms.from_number,
            "Body": body,
        }
    ).encode("utf-8")
    credentials = base64.b64encode(
        f"{sms.account_sid}:{sms.auth_token}".encode("utf-8")
    ).decode("ascii")
    request = Request(
        endpoint,
        data=form,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "PersonalYosemiteAvailabilityScanner/2.0",
        },
        method="POST",
    )

    try:
        with urlopen(
            request,
            timeout=REQUEST_TIMEOUT_SECONDS,
            context=tls_context,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict) or not payload.get("sid"):
                raise RuntimeError("Twilio returned a success response without a SID")
            return str(payload["sid"])
    except HTTPError as exc:
        details = ""
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            details = str(payload.get("message") or payload.get("detail") or "")
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        suffix = f": {details}" if details else ""
        raise RuntimeError(f"Twilio HTTP {exc.code}{suffix}") from exc
    except URLError as exc:
        raise RuntimeError(f"Twilio network error: {exc.reason}") from exc
    except (TimeoutError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"Twilio returned an unusable response: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Twilio returned invalid JSON") from exc


def send_ntfy(
    ntfy: NtfyConfig,
    body: str,
    tls_context: ssl.SSLContext,
) -> str:
    """Publish one ntfy notification and return its message ID."""
    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Title": "Yosemite campsite opening",
        "Tags": "tent,rotating_light",
        "User-Agent": "PersonalYosemiteAvailabilityScanner/2.0",
    }
    if ntfy.token:
        headers["Authorization"] = f"Bearer {ntfy.token}"

    request = Request(
        f"{ntfy.server}/{ntfy.topic}",
        data=body.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(
            request,
            timeout=REQUEST_TIMEOUT_SECONDS,
            context=tls_context,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict) or not payload.get("id"):
                raise RuntimeError("ntfy returned a success response without an ID")
            return str(payload["id"])
    except HTTPError as exc:
        details = ""
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            details = str(payload.get("error") or payload.get("message") or "")
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        suffix = f": {details}" if details else ""
        raise RuntimeError(f"ntfy HTTP {exc.code}{suffix}") from exc
    except URLError as exc:
        raise RuntimeError(f"ntfy network error: {exc.reason}") from exc
    except (TimeoutError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"ntfy returned an unusable response: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("ntfy returned invalid JSON") from exc


def send_message(
    messenger: SmsConfig | NtfyConfig,
    body: str,
    tls_context: ssl.SSLContext,
) -> tuple[str, str]:
    """Send an alert and return its service name and message ID."""
    if isinstance(messenger, SmsConfig):
        return "Twilio", send_sms(messenger, body, tls_context)
    return "ntfy", send_ntfy(messenger, body, tls_context)


def sms_body(
    campground: Campground,
    match: dict[str, str],
    check_in: date,
    check_out: date | None,
) -> str:
    if match.get("available_date"):
        message_check_in = date.fromisoformat(match["available_date"])
        message_check_out = message_check_in + timedelta(days=1)
    else:
        assert check_out is not None
        message_check_in = check_in
        message_check_out = check_out
    night_count = (message_check_out - message_check_in).days
    night_label = "night" if night_count == 1 else "nights"
    available_date = message_check_in.strftime("%b %d, %Y").replace(" 0", " ")
    site_label = match["site"].lstrip("0") or match["site"]
    return (
        f"{campground.name} {site_label} is open for {night_count} "
        f"{night_label} on {available_date}\n{match['url']}"
    )


def mac_notification(title: str, message: str, enabled: bool) -> None:
    if not enabled or platform.system() != "Darwin":
        return

    def escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    script = f'display notification "{escape(message)}" with title "{escape(title)}"'
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        LOGGER.exception("Could not show macOS notification")


def monitor_campground(
    campground: Campground,
    check_in: date,
    check_out: date | None,
    interval_seconds: float,
    initial_offset_seconds: float,
    notifications_enabled: bool,
    messenger: SmsConfig | NtfyConfig | None,
    tls_context: ssl.SSLContext,
    stop_event: threading.Event,
) -> None:
    """Continuously monitor one campground, backing off after failures."""
    if stop_event.wait(initial_offset_seconds):
        return

    previous_site_ids: set[str] = set()
    consecutive_errors = 0

    while not stop_event.is_set():
        cycle_started = time.monotonic()

        try:
            matches = find_available_sites(
                campground,
                check_in,
                check_out,
                tls_context,
            )
            current_site_ids = {match["match_id"] for match in matches}
            newly_available_ids = current_site_ids - previous_site_ids

            log_scan_result = LOGGER.warning if matches else LOGGER.info
            log_scan_result(
                "%s: Recreation.gov scan successful; %d matching site(s)",
                campground.name,
                len(matches),
            )

            if newly_available_ids:
                new_matches = [
                    match
                    for match in matches
                    if match["match_id"] in newly_available_ids
                ]

                for match in new_matches:
                    message = sms_body(campground, match, check_in, check_out)
                    LOGGER.info("OPENING: %s", message)

                    mac_notification(
                        title=f"Yosemite opening: {campground.name}",
                        message=message,
                        enabled=notifications_enabled,
                    )

                    if messenger:
                        service, message_id = send_message(
                            messenger, message, tls_context
                        )
                        destination = (
                            messenger.to_number
                            if isinstance(messenger, SmsConfig)
                            else messenger.topic
                        )
                        LOGGER.info(
                            "Alert sent via %s to %s (message ID ending %s)",
                            service,
                            destination,
                            message_id[-6:],
                        )

            previous_site_ids = current_site_ids
            consecutive_errors = 0
            sleep_for = max(
                0.0,
                interval_seconds - (time.monotonic() - cycle_started),
            )

        except RateLimitedError as exc:
            consecutive_errors += 1
            sleep_for = min(
                max(exc.retry_after_seconds, interval_seconds * 2),
                MAX_ERROR_BACKOFF_SECONDS,
            )
            LOGGER.error(
                "%s: Recreation.gov throttled the request (HTTP 429); "
                "backing off for %.0fs%s",
                campground.name,
                sleep_for,
                f"; response: {exc.details}" if exc.details else "",
            )

        except Exception as exc:  # Keeps the long-running monitor alive.
            consecutive_errors += 1
            sleep_for = min(
                max(interval_seconds * (2**consecutive_errors), 60.0),
                MAX_ERROR_BACKOFF_SECONDS,
            )
            LOGGER.exception(
                "%s: %s; retrying in %.0fs",
                campground.name,
                exc,
                sleep_for,
            )

        stop_event.wait(sleep_for)


def main() -> None:
    args = parse_args()

    try:
        configure_logging(args.log_file, color=not args.no_color)
        tls_context = ssl_context(args.ca_bundle)
        messenger: SmsConfig | NtfyConfig | None = None
        if args.messenger == "twilio":
            messenger = load_sms_config(required=True)
        elif args.messenger == "ntfy":
            messenger = load_ntfy_config(required=True)
    except RuntimeError as exc:
        if LOGGER.handlers:
            LOGGER.error("Configuration error: %s", exc)
        raise SystemExit(f"Configuration error: {exc}") from exc

    if args.test_message:
        assert messenger is not None
        test_body = (
            "Yosemite scanner test successful. "
            f"Upper Pines: {CAMPGROUNDS[0].booking_url}"
        )
        try:
            service, message_id = send_message(messenger, test_body, tls_context)
        except RuntimeError as exc:
            LOGGER.exception("Test alert failed")
            raise SystemExit(f"Test alert failed: {exc}") from exc
        destination = (
            messenger.to_number
            if isinstance(messenger, SmsConfig)
            else messenger.topic
        )
        LOGGER.info(
            "Test alert sent via %s to %s (message ID ending %s)",
            service,
            destination,
            message_id[-6:],
        )
        return

    notifications_enabled = not args.no_mac_notifications
    stop_event = threading.Event()

    LOGGER.info(
        "Scanning %s\n"
        f"Dates: {args.check_in.isoformat()} to "
        f"{args.check_out.isoformat() if args.check_out else 'month-end (any night)'}\n"
        f"Interval: every {args.interval:g} seconds per campground\n"
        f"Remote alerts: {args.messenger or 'disabled'}\n"
        "The four monitors are staggered to avoid simultaneous requests.\n"
        "Log file: %s\n"
        "Press Ctrl+C to stop.",
        ", ".join(campground.name for campground in CAMPGROUNDS),
        args.log_file.expanduser().resolve(),
    )

    threads: list[threading.Thread] = []
    spacing = args.interval / len(CAMPGROUNDS)

    for index, campground in enumerate(CAMPGROUNDS):
        thread = threading.Thread(
            target=monitor_campground,
            name=f"monitor-{campground.campground_id}",
            daemon=True,
            kwargs={
                "campground": campground,
                "check_in": args.check_in,
                "check_out": args.check_out,
                "interval_seconds": args.interval,
                "initial_offset_seconds": index * spacing,
                "notifications_enabled": notifications_enabled,
                "messenger": messenger,
                "tls_context": tls_context,
                "stop_event": stop_event,
            },
        )
        thread.start()
        threads.append(thread)

    try:
        while any(thread.is_alive() for thread in threads):
            time.sleep(1)
    except KeyboardInterrupt:
        LOGGER.info("Stopping scanner...")
        stop_event.set()
        for thread in threads:
            thread.join(timeout=2)


if __name__ == "__main__":
    main()
