import ssl

from camp_scanner.config import NtfyConfig, SmsConfig
from camp_scanner.messengers.ntfy import send_ntfy
from camp_scanner.messengers.twilio import send_sms

MessengerConfig = SmsConfig | NtfyConfig


def send_message(
    messenger: MessengerConfig,
    body: str,
    tls_context: ssl.SSLContext,
) -> tuple[str, str]:
    """Send an alert and return its service name and message ID."""
    if isinstance(messenger, SmsConfig):
        return "Twilio", send_sms(messenger, body, tls_context)
    return "ntfy", send_ntfy(messenger, body, tls_context)


__all__ = [
    "MessengerConfig",
    "NtfyConfig",
    "SmsConfig",
    "send_message",
]
