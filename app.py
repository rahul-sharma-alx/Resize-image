from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

from flask import Flask, after_this_request, render_template, request, send_file
from werkzeug.utils import secure_filename

from image_resizer import CompressionSettings, compress_image

app = Flask(__name__)
# Vercel Functions reject request bodies larger than 4.5 MB.
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024

ALLOWED_EXTENSIONS = {
    ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".webp", ".bmp",
    ".tif", ".tiff", ".gif",
}

FORMAT_CHOICES = {
    "original": "Keep original format",
    "JPEG": "JPEG",
    "WEBP": "WebP",
    "PNG": "PNG",
}


def parse_optional_int(value: str, name: str, minimum: int = 16) -> int | None:
    value = (value or "").strip()
    if not value:
        return None
    number = int(value)
    if number < minimum:
        raise ValueError(f"{name} must be at least {minimum}.")
    return number


def cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


@app.get("/")
def index():
    return render_template("index.html", formats=FORMAT_CHOICES)


@app.post("/compress")
def compress():
    files = [f for f in request.files.getlist("images") if f and f.filename]
    if not files:
        return render_template("index.html", formats=FORMAT_CHOICES, error="Please select at least one image."), 400

    try:
        max_kb = int((request.form.get("max_kb") or "100").strip())
        # Keep generated responses comfortably below Vercel's 4.5 MB response limit.
        if max_kb < 1 or max_kb > 3500:
            raise ValueError("Maximum size must be between 1 KB and 3,500 KB on this Vercel version.")

        max_width = parse_optional_int(request.form.get("max_width", ""), "Maximum width")
        max_height = parse_optional_int(request.form.get("max_height", ""), "Maximum height")
        quality_floor = int((request.form.get("quality") or "25").strip())
        if not 1 <= quality_floor <= 95:
            raise ValueError("Lowest quality must be between 1 and 95.")

        output_format = request.form.get("output_format", "original")
        if output_format not in FORMAT_CHOICES:
            raise ValueError("Invalid output format.")

        skip_smaller = request.form.get("skip_smaller") == "on"
        keep_metadata = request.form.get("keep_metadata") == "on"

        settings = CompressionSettings(
            max_size_bytes=max_kb * 1024,
            max_width=max_width,
            max_height=max_height,
            output_format=output_format,
            quality_floor=quality_floor,
            quality_ceiling=95,
            min_dimension=160,
            skip_smaller=skip_smaller,
            keep_metadata=keep_metadata,
            overwrite=False,
            name_suffix="_compressed",
        )
    except (ValueError, TypeError) as exc:
        return render_template("index.html", formats=FORMAT_CHOICES, error=str(exc)), 400

    work_dir = Path(tempfile.mkdtemp(prefix="image-resizer-"))
    input_dir = work_dir / "input"
    output_dir = work_dir / "output"
    input_dir.mkdir()
    output_dir.mkdir()

    results = []
    used_names: set[str] = set()

    try:
        for index, uploaded in enumerate(files, start=1):
            original_name = secure_filename(uploaded.filename or f"image_{index}.jpg")
            suffix = Path(original_name).suffix.lower()
            if suffix not in ALLOWED_EXTENSIONS:
                continue

            stem = Path(original_name).stem or f"image_{index}"
            candidate = original_name
            counter = 2
            while candidate.lower() in used_names:
                candidate = f"{stem}_{counter}{suffix}"
                counter += 1
            used_names.add(candidate.lower())

            source = input_dir / candidate
            uploaded.save(source)
            destination = output_dir / candidate
            results.append(compress_image(source, destination, settings))

        if not results:
            return render_template("index.html", formats=FORMAT_CHOICES, error="None of the uploaded files are supported images."), 400

        successful = [r for r in results if r.ok and r.destination and r.destination.exists()]
        failed = [r for r in results if not r.ok]
        if not successful:
            details = "; ".join(r.error for r in failed[:3]) or "No image could be processed."
            return render_template("index.html", formats=FORMAT_CHOICES, error=details), 400

        if len(successful) == 1:
            result = successful[0]
            download_path = result.destination
            response = send_file(download_path, as_attachment=True, download_name=download_path.name, mimetype="application/octet-stream")

            @after_this_request
            def remove_work(response):
                cleanup(work_dir)
                return response

            return response

        zip_path = work_dir / "compressed_images.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for result in successful:
                archive.write(result.destination, arcname=result.destination.name)

        # Vercel also limits function responses to 4.5 MB.
        if zip_path.stat().st_size > 4 * 1024 * 1024:
            cleanup(work_dir)
            return render_template(
                "index.html",
                formats=FORMAT_CHOICES,
                error="The ZIP result is too large for Vercel's serverless response limit. Use fewer/smaller images or a smaller target size.",
            ), 413

        response = send_file(zip_path, as_attachment=True, download_name="compressed_images.zip", mimetype="application/zip")

        @after_this_request
        def remove_work(response):
            cleanup(work_dir)
            return response

        return response
    except Exception:
        cleanup(work_dir)
        raise


@app.errorhandler(413)
def too_large(_error):
    return render_template(
        "index.html",
        formats=FORMAT_CHOICES,
        error="Upload is too large. Vercel allows about 4.5 MB per request, so keep the total upload below 4 MB.",
    ), 413
