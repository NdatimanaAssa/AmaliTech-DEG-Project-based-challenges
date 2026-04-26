# run.py
# Entry point for the Idempotency Gateway application.
# Run this file to start the Flask development server.

from app import create_app

# ── App Initialization ──────────────────────────────────────────────────────
app = create_app()

if __name__ == "__main__":
    # Start the server on port 5000 with debug mode enabled for development
    app.run(debug=True, port=5000)
