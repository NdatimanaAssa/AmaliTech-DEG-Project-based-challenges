# app/monitor_store.py
# MonitorStore class for the Pulse-Check API.
# Acts as the single source of truth for all monitor state.
# All reads and writes are protected by a threading.Lock to prevent
# race conditions when multiple heartbeat requests arrive simultaneously.

import threading
import time
from .utils import utc_now


class MonitorStore:
    """
    Thread-safe in-memory store for all registered monitors.

    Each monitor entry is a dictionary containing:
        id            (str)   : Unique device identifier
        timeout       (int)   : Countdown duration in seconds
        alert_email   (str)   : Email address to notify on expiry
        status        (str)   : One of "active", "paused", "down"
        registered_at (str)   : ISO 8601 UTC timestamp of registration
        last_heartbeat(str)   : ISO 8601 UTC timestamp of last heartbeat (or None)
        deadline      (float) : Unix timestamp when the timer will expire
        timer         (obj)   : The threading.Timer object for this monitor

    A single threading.Lock protects all operations so concurrent requests
    cannot corrupt the store state.
    """

    def __init__(self):
        """
        Initialise an empty monitor store and a thread lock.
        """
        # Internal dict: monitor_id (str) -> monitor entry (dict)
        self._monitors = {}

        # Lock ensures only one thread reads or writes at a time
        self._lock = threading.Lock()

    # ── Read Operations ──────────────────────────────────────────────────────

    def get(self, monitor_id):
        """
        Retrieve a monitor entry by its ID.

        Parameters:
            monitor_id (str): The unique device identifier.

        Returns:
            dict | None: The monitor entry dict, or None if not found.
        """
        with self._lock:
            return self._monitors.get(monitor_id)

    def exists(self, monitor_id):
        """
        Check whether a monitor ID is already registered.

        Parameters:
            monitor_id (str): The unique device identifier.

        Returns:
            bool: True if the monitor exists, False otherwise.
        """
        with self._lock:
            return monitor_id in self._monitors

    # ── Write Operations ─────────────────────────────────────────────────────

    def create(self, monitor_id, timeout, alert_email, timer):
        """
        Register a new monitor and store its initial state.

        Parameters:
            monitor_id  (str)            : Unique device identifier.
            timeout     (int)            : Countdown duration in seconds.
            alert_email (str)            : Email address for alerts.
            timer       (threading.Timer): The background countdown timer.
        """
        with self._lock:
            self._monitors[monitor_id] = {
                "id": monitor_id,
                "timeout": timeout,
                "alert_email": alert_email,
                "status": "active",
                "registered_at": utc_now(),
                "last_heartbeat": None,
                # Deadline is the Unix timestamp when the timer will fire
                "deadline": time.time() + timeout,
                "timer": timer
            }

    def update_on_heartbeat(self, monitor_id, new_timer):
        """
        Reset a monitor's countdown after a heartbeat is received.

        Updates the timer object, deadline, last_heartbeat timestamp,
        and ensures the status is set back to "active" (handles un-pause).

        Parameters:
            monitor_id (str)            : The unique device identifier.
            new_timer  (threading.Timer): A freshly created countdown timer.
        """
        with self._lock:
            monitor_entry = self._monitors.get(monitor_id)
            if monitor_entry:
                # Replace the old timer with the new one
                monitor_entry["timer"] = new_timer

                # Recalculate the deadline from now
                monitor_entry["deadline"] = time.time() + monitor_entry["timeout"]

                # Record when this heartbeat arrived
                monitor_entry["last_heartbeat"] = utc_now()

                # Ensure status is active (covers the paused → active transition)
                monitor_entry["status"] = "active"

    def update_on_pause(self, monitor_id):
        """
        Mark a monitor as paused and clear its deadline.

        The timer has already been cancelled by the caller before this is called.

        Parameters:
            monitor_id (str): The unique device identifier.
        """
        with self._lock:
            monitor_entry = self._monitors.get(monitor_id)
            if monitor_entry:
                monitor_entry["status"] = "paused"

                # Clear the deadline — no countdown is running while paused
                monitor_entry["deadline"] = None

                # Clear the timer reference — it has been cancelled
                monitor_entry["timer"] = None

    def update_on_expiry(self, monitor_id):
        """
        Mark a monitor as down when its countdown timer fires.

        Parameters:
            monitor_id (str): The unique device identifier.
        """
        with self._lock:
            monitor_entry = self._monitors.get(monitor_id)
            if monitor_entry:
                monitor_entry["status"] = "down"
                monitor_entry["deadline"] = None
                monitor_entry["timer"] = None

    def get_timer(self, monitor_id):
        """
        Retrieve the active threading.Timer object for a monitor.

        Used by the heartbeat and pause handlers to cancel the existing timer
        before creating a new one.

        Parameters:
            monitor_id (str): The unique device identifier.

        Returns:
            threading.Timer | None: The timer object, or None if not set.
        """
        with self._lock:
            monitor_entry = self._monitors.get(monitor_id)
            if monitor_entry:
                return monitor_entry.get("timer")
            return None
