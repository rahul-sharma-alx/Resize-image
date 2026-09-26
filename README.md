# Image Resizer & Compressor — Web Version

A Flask web interface around the existing Pillow-based `image_resizer.py` engine.

## Run locally

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python web_app.py
```

Open http://127.0.0.1:5000

## Deploy to Render

1. Push this folder to a GitHub repository.
2. In Render, create **New → Web Service** and connect the repository.
3. Runtime: Python.
4. Build command: `pip install -r requirements.txt`
5. Start command: `gunicorn web_app:app`
6. Select the **Free** instance.

The app does not require a database or persistent storage. Uploaded files are temporary and deleted after the download response is prepared.

## Notes

The free Render service can sleep after inactivity and can take about a minute to wake. Its filesystem is ephemeral, which is fine for this stateless image-processing application.
