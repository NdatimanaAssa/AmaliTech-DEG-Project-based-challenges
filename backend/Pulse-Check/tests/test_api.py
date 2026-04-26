# tests/test_api.py
# Pytest test suite for the Pulse-Check API (Watchdog Sentinel).
# Covers all user stories: registration, heartbeat, pause/unpause,
# status endpoint, and input validation.
# Each test uses a fresh Flask test client for full isolation.

import time
import pytest
from app import create_app


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    """
    Provide a Flask test client for each test.

    A fresh app instance is created per test so the in-memory MonitorStore
    starts empty — tests cannot interfere with each other.
    """
    app = create_app()
    app.config["TESTING"] = True

    with app.test_client() as test_client:
        yield test_client


# ── Helpers ───────────────────────────────────────────────────────────────────

def register(client, monitor_id, timeout=60, alert_email="ops@critmon.com"):
    """
    Convenience wrapper to POST /monitors with a standard payload.

    Parameters:
        client      : Flask test client.
        monitor_id  : The 'id' field value.
        timeout     : Countdown duration in seconds (default 60).
        alert_email : Alert recipient email (default ops@critmon.com).

    Returns:
        Flask test Response object.
    """
    return client.post("/monitors", json={
        "id": monitor_id,
        "timeout": timeout,
        "alert_email": alert_email
    })


# ════════════════════════════════════════════════════════════════════════════
# Test 1 — Register a Monitor (User Story 1 — Happy Path)
# ════════════════════════════════════════════════════════════════════════════

def test_register_monitor_returns_201(client):
    """
    Verify that registering a new monitor returns 201 Created.

    Why: This is the core registration flow. A device must be able to
    register itself and receive confirmation that its watchdog is running.
    """
    response = register(client, "solar-farm-01")

    assert response.status_code == 201

    body = response.get_json()
    assert body["id"] == "solar-farm-01"
    assert "registered" in body["message"].lower() or "monitor" in body["message"].lower()


# ════════════════════════════════════════════════════════════════════════════
# Test 2 — Duplicate Registration Returns 409 (User Story 1 — Conflict)
# ════════════════════════════════════════════════════════════════════════════

def test_duplicate_registration_returns_409(client):
    """
    Verify that registering the same monitor ID twice returns 409 Conflict.

    Why: Each device ID must be unique. Allowing duplicates would create
    two competing timers for the same device, causing unpredictable alerts.
    """
    # First registration — should succeed
    register(client, "weather-station-01")

    # Second registration with the same ID — must be rejected
    duplicate_response = register(client, "weather-station-01")

    assert duplicate_response.status_code == 409
    assert "already exists" in duplicate_response.get_json()["error"]


# ════════════════════════════════════════════════════════════════════════════
# Test 3 — Heartbeat Resets Timer (User Story 2 — Happy Path)
# ════════════════════════════════════════════════════════════════════════════

def test_heartbeat_on_existing_monitor_returns_200(client):
    """
    Verify that a heartbeat on an active monitor returns 200 OK.

    Why: The heartbeat is the core keep-alive mechanism. A device must be
    able to reset its countdown to prevent a false alert from firing.
    """
    register(client, "pump-station-01")

    heartbeat_response = client.post("/monitors/pump-station-01/heartbeat")

    assert heartbeat_response.status_code == 200
    assert "pump-station-01" in heartbeat_response.get_json()["message"]


# ════════════════════════════════════════════════════════════════════════════
# Test 4 — Heartbeat on Non-Existent Monitor Returns 404 (User Story 2)
# ════════════════════════════════════════════════════════════════════════════

def test_heartbeat_on_missing_monitor_returns_404(client):
    """
    Verify that a heartbeat for an unknown monitor ID returns 404 Not Found.

    Why: A heartbeat for an unregistered device is a client error.
    Returning 404 tells the client to register the monitor first.
    """
    response = client.post("/monitors/ghost-device-99/heartbeat")

    assert response.status_code == 404
    assert "not found" in response.get_json()["error"].lower()


# ════════════════════════════════════════════════════════════════════════════
# Test 5 — Pause an Active Monitor (Bonus)
# ════════════════════════════════════════════════════════════════════════════

def test_pause_active_monitor_returns_200(client):
    """
    Verify that pausing an active monitor returns 200 and sets status to paused.

    Why: Operators need to pause monitors during planned maintenance windows
    so the system does not fire false alerts while the device is offline.
    """
    register(client, "turbine-01")

    pause_response = client.post("/monitors/turbine-01/pause")

    assert pause_response.status_code == 200

    # Confirm the monitor is now paused via the status endpoint
    status_response = client.get("/monitors/turbine-01")
    assert status_response.get_json()["status"] == "paused"


# ════════════════════════════════════════════════════════════════════════════
# Test 6 — Pause an Already-Paused Monitor (Bonus — Idempotent Pause)
# ════════════════════════════════════════════════════════════════════════════

def test_pause_already_paused_monitor_returns_200_with_message(client):
    """
    Verify that pausing an already-paused monitor returns 200 with the
    correct informational message (not an error).

    Why: Pause must be idempotent — calling it twice should not crash or
    return an error. The operator may not know the current state.
    """
    register(client, "sensor-grid-01")

    # First pause
    client.post("/monitors/sensor-grid-01/pause")

    # Second pause — must return 200 with the specific message
    second_pause = client.post("/monitors/sensor-grid-01/pause")

    assert second_pause.status_code == 200
    assert second_pause.get_json()["message"] == "Monitor is already paused"


# ════════════════════════════════════════════════════════════════════════════
# Test 7 — Heartbeat on Paused Monitor Un-Pauses It (Bonus)
# ════════════════════════════════════════════════════════════════════════════

def test_heartbeat_on_paused_monitor_unpauses_it(client):
    """
    Verify that sending a heartbeat to a paused monitor automatically
    un-pauses it, restarts the timer, and returns 200 OK.

    Why: When a device comes back online after maintenance, it should be
    able to resume normal monitoring with a single heartbeat — no separate
    un-pause call required.
    """
    register(client, "relay-node-01")

    # Pause the monitor
    client.post("/monitors/relay-node-01/pause")

    # Confirm it is paused
    paused_status = client.get("/monitors/relay-node-01").get_json()
    assert paused_status["status"] == "paused"

    # Send a heartbeat — should un-pause and restart the timer
    heartbeat_response = client.post("/monitors/relay-node-01/heartbeat")
    assert heartbeat_response.status_code == 200

    # Confirm the monitor is now active again
    active_status = client.get("/monitors/relay-node-01").get_json()
    assert active_status["status"] == "active"


# ════════════════════════════════════════════════════════════════════════════
# Test 8 — GET /monitors/{id} Returns Correct Fields (Developer's Choice)
# ════════════════════════════════════════════════════════════════════════════

def test_get_monitor_returns_correct_fields(client):
    """
    Verify that GET /monitors/{id} returns all required fields with
    correct values including a positive time_remaining for an active monitor.

    Why: The status endpoint is the operator's window into the system.
    All fields must be present and accurate for dashboards and alerting tools.
    """
    register(client, "grid-sensor-01", timeout=60, alert_email="ops@critmon.com")

    response = client.get("/monitors/grid-sensor-01")

    assert response.status_code == 200

    body = response.get_json()

    # All required fields must be present
    assert body["id"] == "grid-sensor-01"
    assert body["status"] == "active"
    assert body["timeout"] == 60
    assert body["alert_email"] == "ops@critmon.com"
    assert body["registered_at"] is not None

    # time_remaining must be positive for a freshly registered monitor
    assert body["time_remaining"] > 0
    assert body["time_remaining"] <= 60


# ════════════════════════════════════════════════════════════════════════════
# Test 9 — GET /monitors/{id} on Non-Existent Monitor Returns 404
# ════════════════════════════════════════════════════════════════════════════

def test_get_nonexistent_monitor_returns_404(client):
    """
    Verify that GET /monitors/{id} returns 404 for an unknown monitor ID.

    Why: Querying a monitor that was never registered is a client error.
    A clear 404 tells the operator the ID is wrong or not yet registered.
    """
    response = client.get("/monitors/does-not-exist-99")

    assert response.status_code == 404
    assert "not found" in response.get_json()["error"].lower()


# ════════════════════════════════════════════════════════════════════════════
# Test 10 — Missing Required Fields Returns 400
# ════════════════════════════════════════════════════════════════════════════

def test_missing_required_fields_returns_400(client):
    """
    Verify that registering a monitor without required fields returns 400.

    Why: Partial registrations must be rejected before any timer is started.
    A monitor without a timeout or email is not actionable.
    """
    # Missing 'timeout' field
    no_timeout = client.post("/monitors", json={
        "id": "incomplete-device",
        "alert_email": "ops@critmon.com"
    })
    assert no_timeout.status_code == 400

    # Missing 'alert_email' field
    no_email = client.post("/monitors", json={
        "id": "incomplete-device-2",
        "timeout": 30
    })
    assert no_email.status_code == 400

    # Missing 'id' field
    no_id = client.post("/monitors", json={
        "timeout": 30,
        "alert_email": "ops@critmon.com"
    })
    assert no_id.status_code == 400
