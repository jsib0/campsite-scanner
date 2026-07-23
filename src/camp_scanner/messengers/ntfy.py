import json
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from camp_scanner.config import NtfyConfig
from camp_scanner.recreation_gov import REQUEST_TIMEOUT_SECONDS


def send_ntfy(ntfy: NtfyConfig, body: str, tls_context: ssl.SSLContext) -> str:
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
            request, timeout=REQUEST_TIMEOUT_SECONDS, context=tls_context
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
