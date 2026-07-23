import base64
import json
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from camp_scanner.config import SmsConfig
from camp_scanner.recreation_gov import REQUEST_TIMEOUT_SECONDS

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"


def send_sms(sms: SmsConfig, body: str, tls_context: ssl.SSLContext) -> str:
    """Send one SMS through Twilio and return the Twilio message SID."""
    endpoint = f"{TWILIO_API_BASE}/Accounts/{sms.account_sid}/Messages.json"
    form = urlencode(
        {"To": sms.to_number, "From": sms.from_number, "Body": body}
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
            request, timeout=REQUEST_TIMEOUT_SECONDS, context=tls_context
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
