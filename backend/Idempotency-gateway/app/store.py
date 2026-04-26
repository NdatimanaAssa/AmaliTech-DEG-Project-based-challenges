# app/store.py
# Thread-safe in-memory IdempotencyStore for the Idempotency Gateway.
# Stores processed payment results keyed by Idempotency-Key.
# Supports 24-hour TTL expiry and in-flight (race condition) tracking
# using threading.Event so concurrent duplicate requests block safely.

import threading
import time


# ── Constants ───────────────────────────────────────────────────────────────
TTL_SECONDS = 24 * 60 * 60  # 24 hours in seconds


class IdempotencyStore:
    """
    A thread-safe in-memory store that tracks idempotency keys.

    Each entry holds:
        - body_hash   : SHA-256 hash of the original request body
        - response    : The saved response dict (status_code + json body)
        - created_at  : Unix timestamp of when the entry was first created
        - event       : threading.Event used to block duplicate in-flight requests

    The store uses a single threading.Lock to protect all read/write operations,
    preventing race conditions when multiple requests arrive simultaneously.
    """

    def __init__(self):
        """
        Initialise an empty store and a reentrant lock for thread safety.
        """
        # Internal dictionary: idempotency_key -> entry dict
        self._store = {}

        # Lock ensures only one thread modifies the store at a time
        self._lock = threading.Lock()

    # ── Public Methods ───────────────────────────────────────────────────────

    def get(self, idempotency_key):
        """
        Retrieve an entry by its idempotency key.

        Automatically deletes and returns None if the entry has expired (TTL).

        Parameters:
            idempotency_key (str): The unique key sent by the client.

        Returns:
            dict | None: The stored entry dict, or None if not found / expired.
        """
        with self._lock:
            entry = self._store.get(idempotency_key)

            if entry is None:
                # Key does not exist in the store
                return None

            # ── TTL Check ──────────────────────────────────────────────────
            age_in_seconds = time.time() - entry["created_at"]
            if age_in_seconds > TTL_SECONDS:
                # Entry has expired — remove it and treat as a brand-new request
                del self._store[idempotency_key]
                return None

            return entry

    def create_inflight(self, idempotency_key, body_hash):
        """
        Mark a key as 'in-flight' (currently being processed).

        Creates a new entry with no response yet and an unset threading.Event.
        Other threads that call get() will see this entry and know to wait.

        Parameters:
            idempotency_key (str): The unique key sent by the client.
            body_hash       (str): SHA-256 hash of the request body.
        """
        with self._lock:
            self._store[idempotency_key] = {
                "body_hash": body_hash,
                "response": None,          # Will be filled once processing completes
                "created_at": time.time(), # Unix timestamp for TTL calculation
                "event": threading.Event() # Other threads wait on this event
            }

    def save_response(self, idempotency_key, response_data):
        """
        Save the final response for a completed payment and signal waiting threads.

        Parameters:
            idempotency_key (str): The unique key sent by the client.
            response_data   (dict): Dict containing 'status_code' and 'body'.
        """
        with self._lock:
            entry = self._store.get(idempotency_key)
            if entry:
                # Store the completed response payload
                entry["response"] = response_data

                # ── Signal Waiting Threads ─────────────────────────────────
                # Any thread blocked on event.wait() will now be unblocked
                entry["event"].set()

    def delete(self, idempotency_key):
        """
        Remove an entry from the store entirely.

        Used when processing fails so the key can be retried cleanly.

        Parameters:
            idempotency_key (str): The unique key to remove.
        """
        with self._lock:
            self._store.pop(idempotency_key, None)
