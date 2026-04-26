# app/routes.py
# All API endpoints and core idempotency logic for the Idempotency Gateway.
# Handles the /health check and the main /process-payment endpoint.
# Implements all three user stories plus the bonus race-condition guard.

import time
from flask import Blueprint, request, jsonify
from .store import IdempotencyStore
from .utils import hash_body, generate_transaction_id, utc_now


# ── Blueprint & Shared Store ─────────────────────────────────────────────────
# One Blueprint groups all routes; one store instance is shared across requests
payment_blueprint = Blueprint("payment", __name__)
idempotency_store = IdempotencyStore()


# ── Constants ────────────────────────────────────────────────────────────────
PROCESSING_DELAY_SECONDS = 2   # Simulated payment processing time
IN_FLIGHT_TIMEOUT_SECONDS = 10 # Max seconds a duplicate request will wait


# ════════════════════════════════════════════════════════════════════════════
# Health Check Endpoint
# ════════════════════════════════════════════════════════════════════════════

@payment_blueprint.route("/health", methods=["GET"])
def health_check():
    """
    Simple liveness probe endpoint.

    Returns a 200 OK response so load balancers and monitoring tools
    can confirm the service is running.

    Returns:
        Response: JSON {"status": "ok"} with HTTP 200.
    """
    return jsonify({"status": "ok"}), 200


# ════════════════════════════════════════════════════════════════════════════
# Process Payment Endpoint
# ════════════════════════════════════════════════════════════════════════════

@payment_blueprint.route("/process-payment", methods=["POST"])
def process_payment():
    """
    Main payment processing endpoint with full idempotency protection.

    Flow:
        1. Validate that the Idempotency-Key header is present.
        2. Validate the request body (amount, currency).
        3. Hash the request body for comparison.
        4. Look up the key in the store:
           a. Not found          → process as a new payment (Happy Path).
           b. Found, in-flight   → wait for the first request to finish (Race Condition).
           c. Found, same body   → return cached response (Duplicate Request).
           d. Found, diff body   → return 409 Conflict (Fraud/Error Check).

    Returns:
        Response: A Flask JSON response with the appropriate status code and headers.
    """

    # ── Step 1: Validate Idempotency-Key Header ──────────────────────────────
    idempotency_key = request.headers.get("Idempotency-Key")

    if not idempotency_key:
        # The header is mandatory — reject the request immediately
        return jsonify({"error": "Missing required header: Idempotency-Key"}), 400

    # ── Step 2: Validate Request Body ───────────────────────────────────────
    request_body = request.get_json(silent=True)

    if not request_body:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    amount = request_body.get("amount")
    currency = request_body.get("currency")

    if amount is None or currency is None:
        # Both fields are required for a payment to make sense
        return jsonify({"error": "Request body must include 'amount' and 'currency'"}), 400

    if not isinstance(amount, (int, float)) or amount <= 0:
        # A payment of zero or negative value is not a valid financial transaction
        return jsonify({"error": "'amount' must be a positive number"}), 400

    # ── Step 3: Hash the Request Body ───────────────────────────────────────
    # SHA-256 hash lets us compare bodies without storing raw data
    incoming_body_hash = hash_body(request_body)

    # ── Step 4: Store Lookup ─────────────────────────────────────────────────
    existing_entry = idempotency_store.get(idempotency_key)

    # ── Case A: Key Not Found — New Request ─────────────────────────────────
    if existing_entry is None:
        return _handle_new_payment(idempotency_key, incoming_body_hash, amount, currency)

    # ── Case B: Key Found but Still In-Flight (Race Condition) ───────────────
    if existing_entry["response"] is None:
        return _handle_inflight_request(idempotency_key, incoming_body_hash, existing_entry)

    # ── Case C: Key Found, Response Exists — Check Body Hash ────────────────
    if existing_entry["body_hash"] == incoming_body_hash:
        # Same key + same body → return the cached response (no reprocessing)
        return _build_cached_response(existing_entry["response"])

    # ── Case D: Same Key, Different Body — Conflict ──────────────────────────
    return jsonify({
        "error": "Idempotency key already used for a different request body."
    }), 409


# ════════════════════════════════════════════════════════════════════════════
# Private Helper Functions
# ════════════════════════════════════════════════════════════════════════════

def _handle_new_payment(idempotency_key, body_hash, amount, currency):
    """
    Process a brand-new payment request.

    Marks the key as in-flight, simulates the 2-second processing delay,
    saves the result, and returns a 201 Created response.

    Parameters:
        idempotency_key (str): The unique key from the request header.
        body_hash       (str): SHA-256 hash of the request body.
        amount          (int|float): Payment amount.
        currency        (str): Payment currency code (e.g. "GHS").

    Returns:
        Response: Flask JSON response with HTTP 201 and X-Cache-Hit: false.
    """
    # Mark this key as in-flight so concurrent duplicates know to wait
    idempotency_store.create_inflight(idempotency_key, body_hash)

    try:
        # ── Simulate Payment Processing ────────────────────────────────────
        # In a real system this would call a payment provider SDK
        time.sleep(PROCESSING_DELAY_SECONDS)

        # ── Build the Success Response Payload ────────────────────────────
        response_payload = {
            "message": f"Charged {amount} {currency}",
            "status": "success",
            "transaction_id": generate_transaction_id(),
            "timestamp": utc_now()
        }

        # Save the completed response so future duplicates can replay it
        idempotency_store.save_response(idempotency_key, {
            "status_code": 201,
            "body": response_payload
        })

        # Return the fresh response with X-Cache-Hit: false
        response = jsonify(response_payload)
        response.status_code = 201
        response.headers["X-Cache-Hit"] = "false"
        return response

    except Exception:
        # ── Delete on Failure ──────────────────────────────────────────────
        # If processing fails, remove the key so the client can safely retry
        idempotency_store.delete(idempotency_key)
        return jsonify({"error": "Payment processing failed. Please retry."}), 500


def _handle_inflight_request(idempotency_key, incoming_body_hash, existing_entry):
    """
    Handle a duplicate request that arrives while the original is still processing.

    Validates the body hash matches (to catch fraud), then blocks on the
    threading.Event until the first request finishes, and returns its result.

    Parameters:
        idempotency_key    (str):  The unique key from the request header.
        incoming_body_hash (str):  SHA-256 hash of this request's body.
        existing_entry     (dict): The in-flight entry from the store.

    Returns:
        Response: Flask JSON response — either the completed result or a 409/504.
    """
    # ── Validate Body Hash Before Waiting ─────────────────────────────────
    # If the body is different, reject immediately — don't wait
    if existing_entry["body_hash"] != incoming_body_hash:
        return jsonify({
            "error": "Idempotency key already used for a different request body."
        }), 409

    # ── Block Until the First Request Completes ────────────────────────────
    # event.wait() releases the GIL and sleeps this thread efficiently
    first_request_finished = existing_entry["event"].wait(timeout=IN_FLIGHT_TIMEOUT_SECONDS)

    if not first_request_finished:
        # The first request took too long — return a gateway timeout
        return jsonify({"error": "Request timed out waiting for in-flight payment."}), 504

    # ── Fetch the Now-Completed Entry ──────────────────────────────────────
    completed_entry = idempotency_store.get(idempotency_key)

    if not completed_entry or not completed_entry["response"]:
        # The first request failed and deleted the key — client should retry
        return jsonify({"error": "Original request failed. Please retry."}), 500

    # Return the same result as the first request, flagged as a cache hit
    return _build_cached_response(completed_entry["response"])


def _build_cached_response(saved_response):
    """
    Construct a Flask response from a previously saved response payload.

    Adds the X-Cache-Hit: true header to signal this is a replayed response.

    Parameters:
        saved_response (dict): Dict with keys 'status_code' and 'body'.

    Returns:
        Response: Flask JSON response with the original status code and cache header.
    """
    response = jsonify(saved_response["body"])
    response.status_code = saved_response["status_code"]

    # Signal to the client that this response was served from the cache
    response.headers["X-Cache-Hit"] = "true"

    return response
