import logging
import threading
import time
from pathlib import Path


LOGGER = logging.getLogger("__main__")



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
