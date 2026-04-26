# tests/test_api.py
# Pytest test suite for the Idempotency Gateway API.
# Covers all user stories: happy path, duplicate detection, conflict detection,
# input validation, and the health check endpoint.
# Each test is self-contained and uses a fresh Flask test client.

import pytest
from app import create_app


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    """
    Provide a Flask test client for each test.

    Creates a fresh app instance in testing mode so tests are isolated
    and do not share state with each other via the module-level store.
    """
    app = create_app()
    app.config["TESTING"] = True

    with app.test_client() as test_client:
        yield test_client


# ── Helper ───────────────────────────────────────────────────────────────────

def post_payment(client, idempotency_key, body):
    """
    Convenience wrapper to POST to /process-payment with JSON body and key header.

    Parameters:
        client          : Flask test client.
        idempotency_key : Value for the Idempotency-Key header (or None to omit).
        body            : Dict to send as the JSON request body.

    Returns:
        Flask test Response object.
    """
    headers = {"Content-Type": "application/json"}

    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key

    return client.post("/process-payment", json=body, headers=headers)


# ════════════════════════════════════════════════════════════════════════════
# Test 1 — Health Check
# ════════════════════════════════════════════════════════════════════════════

def test_health_check(client):
    """
    Verify that GET /health returns 200 OK with {"status": "ok"}.

    Why: Confirms the server is running and the health endpoint is reachable,
    which is required by load balancers and monitoring systems.
    """
    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


# ════════════════════════════════════════════════════════════════════════════
# Test 2 — Happy Path (User Story 1)
# ════════════════════════════════════════════════════════════════════════════

def test_first_payment_returns_201_and_cache_miss(client):
    """
    Verify that a brand-new payment request returns 201 Created with the
    correct response body and X-Cache-Hit: false header.

    Why: This is the core happy path — a legitimate first-time payment
    must be processed and return a success response.
    """
    response = post_payment(client, "key-test-001", {"amount": 100, "currency": "GHS"})

    assert response.status_code == 201

    body = response.get_json()
    assert body["message"] == "Charged 100 GHS"
    assert body["status"] == "success"
    assert "transaction_id" in body
    assert "timestamp" in body

    # X-Cache-Hit must be false for a fresh request
    assert response.headers.get("X-Cache-Hit") == "false"


# ════════════════════════════════════════════════════════════════════════════
# Test 3 — Duplicate Request (User Story 2)
# ════════════════════════════════════════════════════════════════════════════

def test_duplicate_request_returns_cached_response(client):
    """
    Verify that sending the same Idempotency-Key and body a second time
    returns the exact same response with X-Cache-Hit: true.

    Why: This is the core idempotency guarantee — retries must never
    trigger a second charge. The cached response must be replayed.
    """
    key = "key-duplicate-001"
    body = {"amount": 250, "currency": "GHS"}

    # First request — processes the payment
    first_response = post_payment(client, key, body)
    assert first_response.status_code == 201
    first_body = first_response.get_json()

    # Second request — must return the cached result immediately
    second_response = post_payment(client, key, body)
    assert second_response.status_code == 201
    second_body = second_response.get_json()

    # The transaction_id must be identical — proving no reprocessing occurred
    assert first_body["transaction_id"] == second_body["transaction_id"]

    # Cache hit header must be true on the second request
    assert second_response.headers.get("X-Cache-Hit") == "true"


# ════════════════════════════════════════════════════════════════════════════
# Test 4 — Same Key, Different Body (User Story 3)
# ════════════════════════════════════════════════════════════════════════════

def test_same_key_different_body_returns_409(client):
    """
    Verify that reusing an Idempotency-Key with a different request body
    returns 409 Conflict with the correct error message.

    Why: This prevents fraud or accidental misuse where a client tries to
    process a different payment amount using an already-used key.
    """
    key = "key-conflict-001"

    # First request — establishes the key with amount 100
    post_payment(client, key, {"amount": 100, "currency": "GHS"})

    # Second request — same key but different amount (potential fraud)
    conflict_response = post_payment(client, key, {"amount": 500, "currency": "GHS"})

    assert conflict_response.status_code == 409
    assert conflict_response.get_json()["error"] == (
        "Idempotency key already used for a different request body."
    )


# ════════════════════════════════════════════════════════════════════════════
# Test 5 — Missing Idempotency-Key Header
# ════════════════════════════════════════════════════════════════════════════

def test_missing_idempotency_key_returns_400(client):
    """
    Verify that a request without the Idempotency-Key header returns 400 Bad Request.

    Why: The Idempotency-Key is mandatory. Without it, the server cannot
    guarantee idempotency, so the request must be rejected.
    """
    # Pass None as the key so the helper omits the header entirely
    response = post_payment(client, None, {"amount": 100, "currency": "GHS"})

    assert response.status_code == 400
    assert "Idempotency-Key" in response.get_json()["error"]


# ════════════════════════════════════════════════════════════════════════════
# Test 6 — Negative or Zero Amount
# ════════════════════════════════════════════════════════════════════════════

def test_invalid_amount_returns_400(client):
    """
    Verify that a payment with a zero or negative amount returns 400 Bad Request.

    Why: A payment of 0 or less is not a valid financial transaction.
    The API must reject it before any processing occurs.
    """
    # Test zero amount
    zero_response = post_payment(client, "key-zero-001", {"amount": 0, "currency": "GHS"})
    assert zero_response.status_code == 400

    # Test negative amount
    negative_response = post_payment(client, "key-neg-001", {"amount": -50, "currency": "GHS"})
    assert negative_response.status_code == 400


# ════════════════════════════════════════════════════════════════════════════
# Test 7 — Missing Amount or Currency
# ════════════════════════════════════════════════════════════════════════════

def test_missing_body_fields_returns_400(client):
    """
    Verify that a request body missing 'amount' or 'currency' returns 400 Bad Request.

    Why: Both fields are required to construct a valid payment. Partial
    bodies must be rejected with a clear error before any processing.
    """
    # Missing currency field
    no_currency = post_payment(client, "key-nocur-001", {"amount": 100})
    assert no_currency.status_code == 400

    # Missing amount field
    no_amount = post_payment(client, "key-noamt-001", {"currency": "GHS"})
    assert no_amount.status_code == 400
