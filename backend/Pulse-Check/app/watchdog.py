# app/watchdog.py
# WatchdogTimer class for the Pulse-Check API.
# Wraps Python's threading.Timer to provide a clean interface for
# creating, cancelling, and firing countdown timers per monitor.
# When a timer fires (reaches zero), it triggers the alert and marks
# the monitor as "down" in the store.

import threading
from .utils import log_alert


class WatchdogTimer:
    """
    Manages background countdown timers for registered monitors.

    Each monitor gets one WatchdogTimer. If the device does not send
    a heartbeat before the timeout expires, the timer fires and calls
    the alert function — the "Dead Man's Switch" behaviour.

    Uses Python's threading.Timer which runs the callback in a separate
    daemon thread, so it does not block the main Flask request thread.

    Parameters:
        store (MonitorStore): The shared monitor store instance used to
                              update monitor status when the timer fires.
    """

    def __init__(self, store):
        """
        Initialise the WatchdogTimer with a reference to the monitor store.

        Parameters:
            store (MonitorStore): Shared store for reading/writing monitor state.
        """
        # Keep a reference to the store so the alert callback can update status
        self._store = store

    # ── Public Methods ───────────────────────────────────────────────────────

    def create_timer(self, monitor_id, timeout):
        """
        Create and start a new threading.Timer for the given monitor.

        The timer will call _on_expiry() after `timeout` seconds unless
        it is cancelled first by a heartbeat or pause operation.

        Parameters:
            monitor_id (str): The unique device identifier.
            timeout    (int): Number of seconds before the timer fires.

        Returns:
            threading.Timer: The started timer object. The caller must store
                             this in the MonitorStore so it can be cancelled later.
        """
        # Create a daemon timer — it will not prevent the process from exiting
        countdown_timer = threading.Timer(
            interval=timeout,
            function=self._on_expiry,
            args=[monitor_id]
        )

        # Mark as daemon so it dies with the main thread (important for tests)
        countdown_timer.daemon = True

        # Start the countdown immediately
        countdown_timer.start()

        return countdown_timer

    def cancel_timer(self, monitor_id):
        """
        Cancel the active timer for a monitor, if one exists.

        Safe to call even if the timer has already fired or was never set.
        Retrieves the timer from the store and calls .cancel() on it.

        Parameters:
            monitor_id (str): The unique device identifier.
        """
        # Fetch the current timer object from the store
        existing_timer = self._store.get_timer(monitor_id)

        if existing_timer is not None:
            # Cancel stops the timer from firing if it hasn't already
            existing_timer.cancel()

    # ── Private Callback ─────────────────────────────────────────────────────

    def _on_expiry(self, monitor_id):
        """
        Callback fired by threading.Timer when the countdown reaches zero.

        Updates the monitor status to "down" in the store and logs the alert
        to both the console and the alerts.log file.

        This method runs in a background thread spawned by threading.Timer.

        Parameters:
            monitor_id (str): The unique device identifier of the expired monitor.
        """
        # ── Step 1: Update Monitor Status to Down ────────────────────────────
        # Check the monitor still exists (it could have been removed in tests)
        monitor_entry = self._store.get(monitor_id)

        if monitor_entry is None:
            # Monitor was removed before the timer fired — nothing to do
            return

        if monitor_entry["status"] == "paused":
            # Timer should have been cancelled on pause — safety guard
            return

        # Mark the monitor as down in the store
        self._store.update_on_expiry(monitor_id)

        # ── Step 2: Fire the Alert ───────────────────────────────────────────
        # Log the structured JSON alert to console and alerts.log
        log_alert(monitor_id)
