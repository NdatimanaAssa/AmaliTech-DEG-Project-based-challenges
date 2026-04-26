# app/utils.py
# Utility helpers for the Idempotency Gateway.
# Contains pure functions for hashing request bodies, generating
# unique transaction IDs, and producing UTC timestamps.
# These are kept separate to make them easy to test and reuse.

import hashlib
import json
import uuid
from datetime import datetime, timezone


def hash_body(request_body):
    """
    Produce a deterministic SHA-256 hex digest of a JSON request body dict.

    The dict is serialised with sort_keys=True so that key ordering differences
    (e.g. {"amount":100,"currency":"GHS"} vs {"currency":"GHS","amount":100})
    produce the same hash — preventing false conflict errors.

    Parameters:
        request_body (dict): The parsed JSON body from the incoming request.

    Returns:
        str: A 64-character lowercase hex string (SHA-256 digest).
    """
    # Serialise with sorted keys so key order never affects the hash
    serialised_body = json.dumps(request_body, sort_keys=True)

    # Encode to bytes then compute the SHA-256 digest
    body_hash = hashlib.sha256(serialised_body.encode("utf-8")).hexdigest()

    return body_hash


def generate_transaction_id():
    """
    Generate a unique transaction ID using UUID4 (random).

    Returns:
        str: A UUID4 string, e.g. "3f2504e0-4f89-11d3-9a0c-0305e82c3301".
    """
    return str(uuid.uuid4())


def utc_now():
    """
    Return the current UTC time as an ISO 8601 formatted string.

    Returns:
        str: UTC timestamp, e.g. "2025-07-10T14:32:00.123456+00:00".
    """
    return datetime.now(timezone.utc).isoformat()
