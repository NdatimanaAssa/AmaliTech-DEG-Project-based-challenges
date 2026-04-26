# app/__init__.py
# Flask application factory for the Pulse-Check API (Watchdog Sentinel).
# Creates and configures the Flask app instance and registers all blueprints.

from flask import Flask
from .routes import monitor_blueprint


def create_app():
    """
    Application factory that creates and configures the Flask app.

    Registers the monitor blueprint which contains all API endpoints.
    Using a factory pattern makes the app easy to test in isolation —
    each test can spin up a fresh app instance with a clean state.

    Returns:
        Flask: A fully configured Flask application instance.
    """
    app = Flask(__name__)

    # ── Blueprint Registration ───────────────────────────────────────────────
    # All monitor-related routes are grouped under the monitor blueprint
    app.register_blueprint(monitor_blueprint)

    return app
