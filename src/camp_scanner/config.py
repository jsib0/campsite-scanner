from __future__ import annotations

import os
import ssl
from dataclasses import dataclass
from pathlib import Path


def default_log_file() -> Path:
    """Return a writable per-user log path."""
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys_platform() == "darwin":
        root = Path.home() / "Library" / "Logs"
    else:
        root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return root / "camp-scanner" / "camp-scanner.log"


def sys_platform() -> str:
    # Kept behind a function so platform-specific paths are easy to test.
    import sys

    return sys.platform


DEFAULT_LOG_FILE = default_log_file()


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


def load_env_file(path: Path | None) -> None:
    """Load KEY=VALUE settings without overriding exported environment variables."""
    if path is None:
        configured_path = os.environ.get("CAMP_SCANNER_ENV_FILE")
        if not configured_path:
            return
        path = Path(configured_path)

    path = path.expanduser()
    if not path.is_file():
        raise RuntimeError(f"Environment file does not exist: {path}")

    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise RuntimeError(
                f"Invalid environment setting at {path}:{line_number}"
            )
        name, value = line.split("=", 1)
        name = name.strip()
        if not name or not name.replace("_", "").isalnum() or name[0].isdigit():
            raise RuntimeError(
                f"Invalid environment variable name at {path}:{line_number}"
            )
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


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
