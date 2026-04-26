# run.py
# Entry point for the Pulse-Check API (Watchdog Sentinel).
# Run this file to start the Flask development server for CritMon Servers Inc.

from app import create_app

# ── App Initialization ───────────────────────────────────────────────────────
app = create_app()

if __name__ == "__main__":
    # Start the server on port 5001 with debug=False to prevent the reloader
    # from spawning duplicate background threads in development mode
    app.run(debug=False, port=5001)
