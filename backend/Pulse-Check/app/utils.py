# app/utils.py
# Utility helpers for the Pulse-Check API.
# Contains pure functions for timestamps, alert logging, and formatting
# monitor state into a clean API response dict.
# Kept separate so they are easy to test and reuse across the codebase.

import json
import logging
from datetime import datetime, timezone


# ── Alert Logger Setup ───────────────────────────────────────────────────────
# Configure a dedicated logger that writes to both console and alerts.log file
alert_logger = logging.getLogger("pulse_check.alerts")
alert_logger.setLevel(logging.INFO)

# Console handler — prints alert JSON to stdout so operators see it live
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# File handler — persists every alert to alerts.log for audit trail
file_handler = logging.FileHandler("alerts.log")
file_handler.setLevel(logging.INFO)

# Plain formatter — no extra logging metadata, just the raw message
plain_formatter = logging.Formatter("%(message)s")
console_handler.setFormatter(plain_formatter)
file_handler.setFormatter(plain_formatter)

# Attach both handlers to the logger (avoid duplicate handlers on re-import)
if not alert_logger.handlers:
    alert_logger.addHandler(console_handler)
    alert_logger.addHandler(file_handler)


def utc_now():
    """
    Return the current UTC time as an ISO 8601 formatted string.

    Returns:
        str: UTC timestamp, e.g. "2025-07-10T14:32:00.123456+00:00".
    """
    return datetime.now(timezone.utc).isoformat()


def log_alert(monitor_id):
    """
    Log a JSON-formatted alert when a monitor's countdown reaches zero.

    Writes the alert to both the console (stdout) and the alerts.log file.
    The JSON format makes it easy to ingest into log aggregation tools
    like CloudWatch, Datadog, or ELK Stack in a production environment.

    Parameters:
        monitor_id (str): The ID of the monitor that has gone down.
    """
    # Build the structured alert payload
    alert_payload = {
        "ALERT": f"Device {monitor_id} is down!",
        "time": utc_now()
    }

    # Serialise to a compact JSON string for logging
    alert_message = json.dumps(alert_payload)

    # Write to console and file via the configured logger
    alert_logger.info(alert_message)


def format_monitor_response(monitor_entry):
    """
    Convert a raw monitor store entry into a clean API response dictionary.

    Calculates the live time_remaining by comparing the stored deadline
    against the current time, so the value is always accurate at call time.

    Parameters:
        monitor_entry (dict): A monitor entry dict from the MonitorStore.

    Returns:
        dict: A response-ready dict with all public monitor fields.
    """
    import time

    # ── Calculate Time Remaining ─────────────────────────────────────────────
    # deadline is a Unix timestamp set when the timer was last started/reset
    deadline = monitor_entry.get("deadline")
    status = monitor_entry.get("status")

    if status == "paused" or deadline is None:
        # Paused monitors have no active countdown — time remaining is zero
        time_remaining = 0
    elif status == "down":
        # Expired monitors have no time left
        time_remaining = 0
    else:
        # Active monitors: calculate seconds left until the deadline
        seconds_left = deadline - time.time()
        time_remaining = max(0, round(seconds_left, 2))

    return {
        "id": monitor_entry["id"],
        "status": monitor_entry["status"],
        "timeout": monitor_entry["timeout"],
        "alert_email": monitor_entry["alert_email"],
        "time_remaining": time_remaining,
        "registered_at": monitor_entry["registered_at"],
        "last_heartbeat": monitor_entry.get("last_heartbeat")
    }
