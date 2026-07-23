from __future__ import annotations

import json
import ssl
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from camp_scanner.campgrounds import BASE_URL, Campground
from camp_scanner.logging_setup import LOGGER

AVAILABLE_STATUS = "Available"
REQUEST_TIMEOUT_SECONDS = 15


class RateLimitedError(RuntimeError):
    def __init__(self, retry_after_seconds: float, details: str = "") -> None:
        message = f"Rate limited; retry after {retry_after_seconds:.0f}s"
        if details:
            message += f": {details}"
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
        self.details = details


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
        current = first_day_of_next_month(current)
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
        body = exc.read(2048).decode("utf-8", errors="replace").strip()
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
    return f"{BASE_URL}/camping/campsites/{site_id}"


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
                    "Unexpected Recreation.gov response: "
                    "'availabilities' is not an object"
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
