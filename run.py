import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    # Debug is off by default. For local auto-reload run:
    #   $env:FLASK_DEBUG="1"; python run.py
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )
