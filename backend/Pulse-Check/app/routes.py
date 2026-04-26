# app/routes.py
# All API endpoints and request handling for the Pulse-Check API.
# Implements monitor registration, heartbeat, pause, and status endpoints.
# Uses a shared MonitorStore and WatchdogTimer instance across all requests.

from flask import Blueprint, request, jsonify
from .monitor_store import MonitorStore
from .watchdog import WatchdogTimer
from .utils import format_monitor_response


# ── Blueprint & Shared Instances ─────────────────────────────────────────────
# One store and one watchdog are shared for the lifetime of the application
monitor_blueprint = Blueprint("monitor", __name__)
monitor_store = MonitorStore()
watchdog = WatchdogTimer(monitor_store)


# ════════════════════════════════════════════════════════════════════════════
# Health Check
# ════════════════════════════════════════════════════════════════════════════

@monitor_blueprint.route("/health", methods=["GET"])
def health_check():
    """
    Simple liveness probe so load balancers can confirm the service is up.

    Returns:
        Response: JSON {"status": "ok"} with HTTP 200.
    """
    return jsonify({"status": "ok"}), 200


# ════════════════════════════════════════════════════════════════════════════
# User Story 1 — Register a Monitor
# ════════════════════════════════════════════════════════════════════════════

@monitor_blueprint.route("/monitors", methods=["POST"])
def register_monitor():
    """
    Register a new monitor and start its countdown timer.

    Accepts a JSON body with id, timeout, and alert_email.
    Starts a background WatchdogTimer that will fire an alert if no
    heartbeat is received before the timeout expires.

    Returns:
        Response: 201 Created on success, 409 if ID already exists,
                  400 if required fields are missing.
    """

    # ── Validation ───────────────────────────────────────────────────────────
    request_body = request.get_json(silent=True)

    if not request_body:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    monitor_id = request_body.get("id")
    timeout = request_body.get("timeout")
    alert_email = request_body.get("alert_email")

    # All three fields are required to register a meaningful monitor
    if not monitor_id or timeout is None or not alert_email:
        return jsonify({"error": "Required fields: 'id', 'timeout', 'alert_email'"}), 400

    if not isinstance(timeout, (int, float)) or timeout <= 0:
        return jsonify({"error": "'timeout' must be a positive number"}), 400

    # ── Duplicate Check ──────────────────────────────────────────────────────
    if monitor_store.exists(monitor_id):
        return jsonify({"error": f"Monitor '{monitor_id}' already exists."}), 409

    # ── Start the Countdown Timer ────────────────────────────────────────────
    # Create the timer first so we can store it alongside the monitor entry
    countdown_timer = watchdog.create_timer(monitor_id, timeout)

    # Save the monitor and its timer to the store
    monitor_store.create(monitor_id, timeout, alert_email, countdown_timer)

    return jsonify({
        "message": f"Monitor '{monitor_id}' registered. Countdown started for {timeout}s.",
        "id": monitor_id
    }), 201


# ════════════════════════════════════════════════════════════════════════════
# User Story 2 — Heartbeat (Reset Timer)
# ════════════════════════════════════════════════════════════════════════════

@monitor_blueprint.route("/monitors/<string:monitor_id>/heartbeat", methods=["POST"])
def heartbeat(monitor_id):
    """
    Reset the countdown timer for an existing monitor.

    Cancels the current timer and starts a fresh one from the full timeout.
    If the monitor was paused, this call automatically un-pauses it.

    Parameters:
        monitor_id (str): The unique device identifier from the URL path.

    Returns:
        Response: 200 OK on success, 404 if monitor not found.
    """

    # ── Step 1: Check Monitor Exists ─────────────────────────────────────────
    monitor_entry = monitor_store.get(monitor_id)

    if monitor_entry is None:
        return jsonify({"error": f"Monitor '{monitor_id}' not found."}), 404

    # ── Step 2: Cancel the Existing Timer ────────────────────────────────────
    # Must cancel before creating a new one to avoid double-firing
    watchdog.cancel_timer(monitor_id)

    # ── Step 3: Start a Fresh Countdown Timer ────────────────────────────────
    new_timer = watchdog.create_timer(monitor_id, monitor_entry["timeout"])

    # ── Step 4: Update Store (resets deadline, last_heartbeat, status) ───────
    monitor_store.update_on_heartbeat(monitor_id, new_timer)

    was_paused = monitor_entry["status"] == "paused"
    action_message = "un-paused and timer reset" if was_paused else "timer reset"

    return jsonify({
        "message": f"Monitor '{monitor_id}' heartbeat received — {action_message}.",
        "id": monitor_id
    }), 200


# ════════════════════════════════════════════════════════════════════════════
# Bonus — Pause Monitor
# ════════════════════════════════════════════════════════════════════════════

@monitor_blueprint.route("/monitors/<string:monitor_id>/pause", methods=["POST"])
def pause_monitor(monitor_id):
    """
    Pause a monitor's countdown timer so no alert will fire while paused.

    Cancels the active timer and sets the monitor status to "paused".
    If the monitor is already paused, returns 200 with an informational message.

    Parameters:
        monitor_id (str): The unique device identifier from the URL path.

    Returns:
        Response: 200 OK in all valid cases, 404 if monitor not found.
    """

    # ── Step 1: Check Monitor Exists ─────────────────────────────────────────
    monitor_entry = monitor_store.get(monitor_id)

    if monitor_entry is None:
        return jsonify({"error": f"Monitor '{monitor_id}' not found."}), 404

    # ── Step 2: Handle Already-Paused Case ───────────────────────────────────
    if monitor_entry["status"] == "paused":
        return jsonify({
            "message": "Monitor is already paused",
            "id": monitor_id
        }), 200

    # ── Step 3: Cancel the Active Timer ──────────────────────────────────────
    # Stop the countdown — no alert should fire while the monitor is paused
    watchdog.cancel_timer(monitor_id)

    # ── Step 4: Update Store to Paused State ─────────────────────────────────
    monitor_store.update_on_pause(monitor_id)

    return jsonify({
        "message": f"Monitor '{monitor_id}' paused. No alerts will fire.",
        "id": monitor_id
    }), 200


# ════════════════════════════════════════════════════════════════════════════
# Developer's Choice — Get Monitor Status
# ════════════════════════════════════════════════════════════════════════════

@monitor_blueprint.route("/monitors/<string:monitor_id>", methods=["GET"])
def get_monitor(monitor_id):
    """
    Return the current state of a monitor including live time_remaining.

    Provides operators with a real-time snapshot of a monitor's status,
    countdown, and metadata — essential for dashboards and debugging.

    Parameters:
        monitor_id (str): The unique device identifier from the URL path.

    Returns:
        Response: 200 OK with monitor state dict, 404 if not found.
    """

    # ── Step 1: Fetch Monitor from Store ─────────────────────────────────────
    monitor_entry = monitor_store.get(monitor_id)

    if monitor_entry is None:
        return jsonify({"error": f"Monitor '{monitor_id}' not found."}), 404

    # ── Step 2: Format and Return the Response ───────────────────────────────
    # format_monitor_response calculates live time_remaining at call time
    response_data = format_monitor_response(monitor_entry)

    return jsonify(response_data), 200
