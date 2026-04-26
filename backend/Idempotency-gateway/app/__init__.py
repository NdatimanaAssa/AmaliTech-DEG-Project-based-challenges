# app/__init__.py
# Flask application factory for the Idempotency Gateway.
# Responsible for creating and configuring the Flask app instance
# and registering all route blueprints.

from flask import Flask
from .routes import payment_blueprint


def create_app():
    """
    Application factory function.

    Creates and configures the Flask application, then registers
    the payment blueprint that contains all API endpoints.

    Returns:
        Flask: A fully configured Flask application instance.
    """
    app = Flask(__name__)

    # ── Blueprint Registration ──────────────────────────────────────────────
    # Register the payment routes under the root URL prefix
    app.register_blueprint(payment_blueprint)

    return app
