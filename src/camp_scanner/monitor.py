from __future__ import annotations

import platform
import ssl
import subprocess
import threading
import time
from datetime import date, timedelta

from camp_scanner.campgrounds import Campground
from camp_scanner.config import NtfyConfig, SmsConfig
from camp_scanner.logging_setup import LOGGER
from camp_scanner.messengers import send_message
from camp_scanner.recreation_gov import RateLimitedError, find_available_sites

MAX_ERROR_BACKOFF_SECONDS = 15 * 60


def alert_body(
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
                campground, check_in, check_out, tls_context
            )
            current_site_ids = {match["match_id"] for match in matches}
            newly_available_ids = current_site_ids - previous_site_ids

            log_scan_result = LOGGER.warning if matches else LOGGER.info
            log_scan_result(
                "%s: Recreation.gov scan successful; %d matching site(s)",
                campground.name,
                len(matches),
            )

            for match in (
                match
                for match in matches
                if match["match_id"] in newly_available_ids
            ):
                message = alert_body(campground, match, check_in, check_out)
                LOGGER.warning("OPENING: %s", message)
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
                0.0, interval_seconds - (time.monotonic() - cycle_started)
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
        except Exception as exc:
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
