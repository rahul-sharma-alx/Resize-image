"""image_resizer.py
================

Core engine of the *Bulk Image Compressor*.

It walks a folder of images and rewrites every picture so that the resulting
file is at or below a target size -- 100 KB by default, which is what the
application was built for.

How the size target is reached
------------------------------
1. The image is normalised (EXIF rotation applied, alpha channel flattened when
   the target format cannot store it, animation collapsed to the first frame).
2. If a maximum width / height was requested the image is downscaled once.
3. A binary search over ``quality`` (default 25 - 95) finds the *highest*
   quality that still fits into the byte budget -- for JPEG and WEBP.
4. When even the lowest quality is still too large, the image is downscaled by
   a factor derived from the overshoot and the search runs again, until the
   budget is met or ``min_dimension`` pixels is reached.
5. The smallest result found is written out.  Files that already satisfy the
   target are left untouched (unless ``skip_smaller`` is disabled).

Three ways to use it
--------------------
* library  -> :func:`compress_image` / :func:`compress_folder`
* GUI      -> ``python app.py``
* CLI      -> ``python image_resizer.py <folder> --max-kb 100``
"""

from __future__ import annotations

import argparse
import io
import math
import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence

from PIL import Image, ImageOps, UnidentifiedImageError

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: 100 KB -- the limit this application was written for.
DEFAULT_MAX_BYTES = 100 * 1024

KIB = 1024

#: File extensions that are picked up when a folder is scanned.
SUPPORTED_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".jpe",
    ".jfif",
    ".png",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".gif",
)

#: Pillow format name for every supported extension.
FORMAT_BY_EXTENSION = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".jpe": "JPEG",
    ".jfif": "JPEG",
    ".png": "PNG",
    ".webp": "WEBP",
    ".bmp": "BMP",
    ".tif": "TIFF",
    ".tiff": "TIFF",
    ".gif": "GIF",
}

#: Extension written for every format the engine can produce.
EXTENSION_BY_FORMAT = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
}

#: Formats offered to the user.  "original" keeps the source format.
OUTPUT_FORMATS = ("original", "JPEG", "PNG", "WEBP")

#: Formats that only know a "quality" knob (the ones we can tune lossily).
LOSSY_FORMATS = ("JPEG", "WEBP")

#: Source formats that are automatically re-encoded as JPEG, because they
#: cannot realistically be squeezed below the size limit otherwise.
_CONVERT_TO_JPEG = ("BMP", "TIFF", "GIF", "MPO")

# --------------------------------------------------------------------------- #
# Configuration & result containers
# --------------------------------------------------------------------------- #


@dataclass
class CompressionSettings:
    """Every knob that controls how hard an image is squeezed.

    ``max_size_bytes`` is the important one: the byte budget of a single file.
    """

    #: Hard upper bound for one compressed file (default 100 KB).
    max_size_bytes: int = DEFAULT_MAX_BYTES
    #: Optional downscale targets, ``None`` keeps the original dimensions.
    max_width: int | None = None
    max_height: int | None = None
    #: "original" (keep source format) or one of OUTPUT_FORMATS.
    output_format: str = "original"
    #: Lowest JPEG/WEBP quality that may be used before downscaling starts.
    quality_floor: int = 25
    #: Highest quality that is tried first (keeps quality when it already fits).
    quality_ceiling: int = 95
    #: Never shrink below this many pixels on the shorter side.
    min_dimension: int = 160
    #: Leave files that are already smaller than the limit alone.
    skip_smaller: bool = True
    #: Keep EXIF / ICC data when it is small enough not to blow the budget.
    keep_metadata: bool = True
    #: Replace the source files instead of writing into an output folder.
    overwrite: bool = False
    #: Suffix used when no output folder and no overwrite was requested.
    name_suffix: str = "_compressed"

    def __post_init__(self) -> None:
        fmt = str(self.output_format or "").strip().upper()
        if fmt in ("", "ORIGINAL", "KEEP", "SAME", "SOURCE"):
            fmt = "original"
        if fmt not in OUTPUT_FORMATS:
            raise ValueError(
                "output_format must be one of "
                f"{OUTPUT_FORMATS!r}, got {self.output_format!r}"
            )
        self.output_format = fmt

        self.max_size_bytes = int(self.max_size_bytes)
        if self.max_size_bytes < KIB:
            raise ValueError("max_size_bytes must be at least 1024 (1 KB)")

        self.quality_floor = int(min(max(int(self.quality_floor), 1), 100))
        self.quality_ceiling = int(
            min(max(int(self.quality_ceiling), self.quality_floor), 100)
        )
        self.min_dimension = max(16, int(self.min_dimension))

        for attribute in ("max_width", "max_height"):
            value = getattr(self, attribute)
            if value:
                setattr(self, attribute, max(16, int(value)))
            else:
                setattr(self, attribute, None)

    # ---- convenience ---------------------------------------------------- #

    @property
    def max_size_kib(self) -> float:
        """The budget expressed in KB (what the GUI shows)."""
        return self.max_size_bytes / KIB

    def box(self) -> tuple[int, int]:
        """The bounding box used for the optional first downscale."""
        return (self.max_width or 10 ** 7, self.max_height or 10 ** 7)


@dataclass
class CompressionResult:
    """Outcome of compressing a single file."""

    source: Path
    destination: Path | None = None
    original_size: int = 0
    compressed_size: int = 0
    width: int = 0
    height: int = 0
    format: str = ""
    quality: int | None = None
    skipped: bool = False
    note: str = ""
    error: str = ""

    # ---- derived helpers ------------------------------------------------ #

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def saved_bytes(self) -> int:
        """Bytes saved by this file; a failed file saved nothing."""
        if not self.ok:
            return 0
        return max(0, self.original_size - self.compressed_size)

    @property
    def reduction_percent(self) -> float:
        if not self.original_size:
            return 0.0
        return 100.0 * self.saved_bytes / self.original_size

    def summary(self) -> str:
        """One-line description, handy for log windows and consoles."""
        name = self.source.name
        if self.error:
            return f"[FAILED] {name} -> {self.error}"
        if self.skipped:
            return f"[SKIP]   {name} ({human_size(self.original_size)}) - {self.note}"
        details = self.format
        if self.quality is not None:
            details += f" q={self.quality}"
        if self.width and self.height:
            details += f" {self.width}x{self.height}"
        line = (
            f"[OK]     {name} {human_size(self.original_size)} -> "
            f"{human_size(self.compressed_size)} (-{self.reduction_percent:.1f}%)"
            f" [{details}]"
        )
        if self.note:
            line += f" - {self.note}"
        return line


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def human_size(num_bytes: float) -> str:
    """Format a byte count the way a human likes it (1.2 MB, 96.4 KB, 512 B)."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size) < KIB or unit == "GB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= KIB
    return f"{size:.1f} GB"  # pragma: no cover - unreachable


def _resolve_quietly(path: Path) -> Path:
    """``Path.resolve()`` that never explodes on odd paths."""
    try:
        return path.resolve()
    except OSError:  # pragma: no cover - defensive
        return path


def _is_within(path: str | os.PathLike[str], folder: str | os.PathLike[str]) -> bool:
    """True when *path* is *folder* itself or lives somewhere underneath it."""
    resolved_path = _resolve_quietly(Path(path))
    resolved_folder = _resolve_quietly(Path(folder))
    return resolved_path == resolved_folder or resolved_folder in resolved_path.parents


def _walk_images(
    root: Path, recursive: bool, wanted: set[str], skip: Path | None = None
) -> Iterator[Path]:
    """Yield every supported image below *root*.

    ``os.scandir`` is used instead of ``Path.glob("**/*")`` because the entries
    already carry their type, so no image has to be ``stat``-ed twice.  Folder
    symlinks are not followed (that keeps circular links harmless) and *skip* --
    the output folder -- is never entered at all, which makes a repeated run on a
    folder full of results much faster.
    """
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        if recursive and not (skip and _is_within(entry.path, skip)):
                            stack.append(Path(entry.path))
                    elif entry.is_file() and Path(entry.name).suffix.lower() in wanted:
                        yield Path(entry.path)
        except OSError:  # unreadable folder -> simply keep going
            continue


def iter_images(
    folder: str | os.PathLike[str],
    recursive: bool = True,
    extensions: Sequence[str] = SUPPORTED_EXTENSIONS,
    exclude_dir: str | os.PathLike[str] | None = None,
) -> list[Path]:
    """Return every supported image inside *folder* (sorted, deterministic).

    *exclude_dir* is left out completely, including its sub folders.
    """
    root = Path(folder)
    wanted = {extension.lower() for extension in extensions}

    if root.is_file():
        found = [root] if root.suffix.lower() in wanted else []
    elif root.is_dir():
        found = list(_walk_images(root, recursive, wanted, Path(exclude_dir) if exclude_dir else None))
    else:
        found = []

    if exclude_dir is not None:
        found = [path for path in found if not _is_within(path, exclude_dir)]
    return sorted(found, key=lambda item: str(item).lower())


def ignored_output_dir(
    folder: str | os.PathLike[str],
    output_dir: str | os.PathLike[str] | None = None,
    overwrite: bool = False,
) -> Path | None:
    """The output folder that has to be skipped while scanning, if any.

    Only an output folder that really lives *inside* the scanned folder has to be
    hidden, otherwise a second run would compress its own results again.  An
    output folder that is a **parent** of the images folder (or a completely
    unrelated folder) must never hide anything -- doing so used to make a batch
    report "0 images processed" without telling anyone why.
    """
    if output_dir is None or overwrite:
        return None
    folder_path = Path(folder)
    base_root = folder_path if folder_path.is_dir() else folder_path.parent
    candidate = Path(output_dir)
    if _resolve_quietly(candidate) == _resolve_quietly(base_root):
        return None  # same folder -> an in-place run, nothing to hide
    return candidate if _is_within(candidate, base_root) else None


def collect_images(
    folder: str | os.PathLike[str],
    recursive: bool = True,
    output_dir: str | os.PathLike[str] | None = None,
    overwrite: bool = False,
) -> list[Path]:
    """The images a batch would process right now.

    This is the single place that decides which files a run touches, so the GUI
    can show the very same number the engine is going to work on.
    """
    return iter_images(
        folder,
        recursive=recursive,
        exclude_dir=ignored_output_dir(folder, output_dir, overwrite),
    )


def effective_format(
    source: str | os.PathLike[str], settings: CompressionSettings
) -> str:
    """The format a file will be written in, given the current settings."""
    if settings.output_format != "original":
        return settings.output_format
    source_format = FORMAT_BY_EXTENSION.get(Path(source).suffix.lower(), "JPEG")
    if source_format in _CONVERT_TO_JPEG or source_format not in EXTENSION_BY_FORMAT:
        return "JPEG"
    return source_format


def build_destination(
    source: str | os.PathLike[str],
    input_root: str | os.PathLike[str] | None,
    output_dir: str | os.PathLike[str] | None,
    settings: CompressionSettings,
) -> Path:
    """Work out where the compressed version of *source* has to be written."""
    source_path = Path(source)

    if output_dir is None:
        return source_path.with_name(
            f"{source_path.stem}{settings.name_suffix}{source_path.suffix}"
        )

    root = Path(input_root) if input_root is not None else source_path.parent
    try:
        relative = source_path.relative_to(root)
    except ValueError:
        relative = Path(source_path.name)

    target = Path(output_dir) / relative
    target_format = effective_format(source_path, settings)
    source_format = FORMAT_BY_EXTENSION.get(source_path.suffix.lower())
    if target_format != source_format:
        extension = EXTENSION_BY_FORMAT.get(target_format)
        if extension:
            target = target.with_suffix(extension)
    return target


# --------------------------------------------------------------------------- #
# Image preparation / encoding
# --------------------------------------------------------------------------- #


def has_alpha(image: Image.Image) -> bool:
    """True when the image carries transparency information."""
    if image.mode in ("RGBA", "LA", "PA"):
        return True
    if image.mode == "P":
        return "transparency" in image.info
    return False


def flatten_alpha(
    image: Image.Image, background: tuple[int, int, int] = (255, 255, 255)
) -> Image.Image:
    """Composite a transparent image onto a solid background -> RGB."""
    if image.mode == "P":
        image = image.convert("RGBA")
    if image.mode in ("RGBA", "LA"):
        canvas = Image.new("RGB", image.size, background)
        canvas.paste(image, mask=image.split()[-1])
        return canvas
    if image.mode == "PA":
        image = image.convert("RGBA")
        canvas = Image.new("RGB", image.size, background)
        canvas.paste(image, mask=image.split()[-1])
        return canvas
    return image.convert("RGB")


def prepare_image(
    image: Image.Image, image_format: str, background: tuple[int, int, int] = (255, 255, 255)
) -> Image.Image:
    """Return a copy of *image* that can safely be saved as *image_format*.

    Animated images are collapsed to their first frame and 16 bit / CMYK /
    palette modes are converted so that the encoder never refuses the data.
    """
    if getattr(image, "is_animated", False) and getattr(image, "n_frames", 1) > 1:
        image.seek(0)
        image = image.copy()

    if image_format == "JPEG":
        if has_alpha(image):
            image = flatten_alpha(image, background)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        return image

    if image_format == "WEBP":
        if image.mode in ("P", "PA"):
            image = image.convert("RGBA")
        elif image.mode not in ("RGB", "RGBA", "L", "LA"):
            image = image.convert("RGBA" if has_alpha(image) else "RGB")
        return image

    if image_format == "PNG":
        if image.mode.startswith("I;") or image.mode in ("I", "F"):
            image = image.convert("L")
        elif image.mode in ("CMYK", "YCbCr"):
            image = image.convert("RGB")
        return image

    # BMP / TIFF / GIF and anything else Pillow can write.
    if image.mode not in ("RGB", "L", "RGBA", "P"):
        image = image.convert("RGBA" if has_alpha(image) else "RGB")
    return image


def _metadata_kwargs(
    image: Image.Image, image_format: str, settings: CompressionSettings
) -> dict:
    """Collect EXIF/ICC blobs, but only when they are cheap enough.

    A 60 KB EXIF block would eat the whole budget, so metadata is dropped when
    it takes more than 10 % of the allowed size.
    """
    if not settings.keep_metadata:
        return {}

    limit = max(KIB, settings.max_size_bytes // 10)
    kwargs: dict = {}
    info = getattr(image, "info", {}) or {}

    exif = info.get("exif")
    if exif and image_format in ("JPEG", "WEBP", "TIFF") and len(exif) <= limit:
        kwargs["exif"] = exif

    icc_profile = info.get("icc_profile")
    if icc_profile and image_format in ("JPEG", "WEBP", "PNG") and len(icc_profile) <= limit:
        kwargs["icc_profile"] = icc_profile

    return kwargs


def encode_image(
    image: Image.Image,
    image_format: str,
    quality: int | None = None,
    metadata: dict | None = None,
    fast: bool = False,
) -> bytes:
    """Encode *image* in memory and return the raw bytes.

    *fast* switches the encoder to its quickest mode.  The size search uses that
    for every trial encode and only re-encodes the winning setting carefully: the
    quick settings never produce a *smaller* file than the careful ones, so a
    quality that fits while searching also fits afterwards.
    """
    buffer = io.BytesIO()
    kwargs = dict(metadata or {})

    if image_format == "JPEG":
        kwargs.update(quality=int(quality if quality is not None else 85),
                      optimize=not fast, progressive=not fast)
    elif image_format == "WEBP":
        # 'method' trades encoding time for a few percent of size.  Measured on a
        # 1024x1536 RGBA photo (quality 60): method 2 needs 0.55 s for 116 KB,
        # method 5 needs 1.2 s for 103 KB and method 6 needs 17.5 s for 99 KB.
        # Trials therefore run at method 2 and only the winning setting is
        # re-encoded at method 5 -- searching at method 6 used to make a single
        # photo take minutes.
        kwargs.update(quality=int(quality if quality is not None else 85),
                      method=2 if fast else 5)
    elif image_format == "PNG":
        kwargs.update(optimize=not fast, compress_level=6 if fast else 9)
    elif image_format == "TIFF":
        kwargs.update(compression="tiff_deflate")
    elif image_format == "GIF":
        kwargs.update(optimize=True)

    image.save(buffer, format=image_format, **kwargs)
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# The size-hitting algorithm
# --------------------------------------------------------------------------- #


def _search_quality(
    image: Image.Image,
    image_format: str,
    settings: CompressionSettings,
    metadata: dict,
) -> tuple[bytes | None, int | None]:
    """Find the best quality that keeps the encoded image within the budget.

    Returns ``(payload, quality)`` or ``(None, None)`` when even
    ``quality_floor`` is too large (the caller then downscales).
    """
    target = settings.max_size_bytes
    quality: int | None = settings.quality_ceiling

    if image_format not in LOSSY_FORMATS:
        quality = None

    payload = encode_image(image, image_format, quality, metadata, fast=True)

    # Fast path: the highest quality already fits, nothing else to do.
    if len(payload) <= target:
        return payload, quality

    if image_format not in LOSSY_FORMATS:
        return None, None

    # Binary search between floor and ceiling-1 for the best fitting quality.
    low = settings.quality_floor
    high = settings.quality_ceiling - 1
    best: tuple[bytes | None, int | None] = (None, None)

    while low <= high:
        middle = (low + high) // 2
        candidate = encode_image(image, image_format, middle, metadata, fast=True)
        if len(candidate) <= target:
            best = (candidate, middle)
            low = middle + 1  # try to gain quality
        else:
            high = middle - 1  # too big, go lower
    return best


def _downscale(image: Image.Image, factor: float) -> Image.Image | None:
    """Resize by *factor*; returns ``None`` when nothing would change."""
    width, height = image.size
    new_size = (max(1, int(round(width * factor))), max(1, int(round(height * factor))))
    if new_size == image.size:
        return None
    return image.resize(new_size, Image.LANCZOS)


def shrink_to_target(
    image: Image.Image,
    image_format: str,
    settings: CompressionSettings,
    metadata: dict | None = None,
) -> tuple[bytes, int | None, tuple[int, int], str]:
    """Encode *image* so that it fits into ``settings.max_size_bytes``.

    Returns ``(payload, quality, size, note)``.  ``quality`` is ``None`` for
    formats without a quality knob, ``note`` carries a remark when the target
    could not be reached exactly.
    """
    metadata = metadata or {}
    working = image
    best_effort: tuple[bytes, int | None] | None = None
    note = ""
    payload: bytes | None = None
    quality: int | None = None

    while True:
        payload, quality = _search_quality(working, image_format, settings, metadata)
        if payload is not None:
            break

        # Even the lowest quality is too big: remember it and shrink further.
        floor_quality = (
            settings.quality_floor if image_format in LOSSY_FORMATS else None
        )
        floor_payload = encode_image(working, image_format, floor_quality, metadata, fast=True)
        if best_effort is None or len(floor_payload) < len(best_effort[0]):
            best_effort = (floor_payload, settings.quality_floor)

        if min(working.size) <= settings.min_dimension:
            note = "best effort - minimum size reached, target not met"
            break

        # How much smaller must each side become?  The area shrinks with the
        # square of the linear factor, hence the square root.
        ratio = math.sqrt(settings.max_size_bytes / max(1, len(floor_payload)))
        factor = min(0.85, max(0.4, ratio * 0.92))
        downscaled = _downscale(working, factor)
        if downscaled is None:
            note = "best effort - cannot shrink any further"
            break
        working = downscaled

    # Everything above ran with the quick encoder settings.  One careful encode
    # of the winning setting produces the smallest file; it is only used when it
    # still respects the budget (it always does in practice, the quick settings
    # are the conservative ones).
    if payload is None:
        assert best_effort is not None  # guaranteed by the loop above
        payload, quality = best_effort
        careful = encode_image(working, image_format, quality, metadata)
        if len(careful) <= settings.max_size_bytes:
            payload, note = careful, ""
    else:
        careful = encode_image(working, image_format, quality, metadata)
        if len(careful) <= settings.max_size_bytes:
            payload = careful

    return payload, quality, working.size, note


def _write_payload(payload: bytes, destination: Path, source: Path) -> None:
    """Write *payload* to *destination* (atomically when it replaces *source*)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.resolve() == source.resolve():
        temporary = destination.with_name(destination.name + ".resizer.tmp")
        try:
            temporary.write_bytes(payload)
            os.replace(temporary, destination)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - defensive
                pass
    else:
        destination.write_bytes(payload)


def compress_image(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str] | None = None,
    settings: CompressionSettings | None = None,
) -> CompressionResult:
    """Compress one image down to ``settings.max_size_bytes``.

    When *destination* is ``None`` (or equals *source*) the file is replaced in
    place, but only if the result is actually smaller.
    """
    settings = settings or CompressionSettings()
    source_path = Path(source)
    destination_path = Path(destination) if destination is not None else source_path
    result = CompressionResult(source=source_path, destination=destination_path)

    try:
        result.original_size = source_path.stat().st_size
    except OSError as exc:
        result.error = f"cannot read file: {exc}"
        return result
    original_size = result.original_size

    try:
        with Image.open(source_path) as opened:
            opened.load()
            source_format = (opened.format or "").upper()
            if source_format == "MPO":
                source_format = "JPEG"
            image_format = effective_format(source_path, settings)
            metadata = _metadata_kwargs(opened, image_format, settings)

            image = ImageOps.exif_transpose(opened)
            if image is None:  # pragma: no cover - signature safety net
                image = opened
            image = prepare_image(image, image_format)
            result.width, result.height = image.size

            resized = False
            if settings.max_width or settings.max_height:
                before = image.size
                image.thumbnail(settings.box(), Image.LANCZOS)
                resized = image.size != before

            # Nothing to do when the file already satisfies every constraint.
            if (
                settings.skip_smaller
                and original_size <= settings.max_size_bytes
                and image_format == source_format
                and not resized
            ):
                result.skipped = True
                result.format = source_format
                result.compressed_size = original_size
                result.note = "already below the limit"
                return result

            payload, quality, size, note = shrink_to_target(
                image, image_format, settings, metadata
            )
            result.width, result.height = size
            result.format = image_format
            result.quality = quality
            result.note = note

            # Never replace a smaller original with a bigger result.
            if (
                destination_path.resolve() == source_path.resolve()
                and len(payload) >= original_size
            ):
                result.skipped = True
                result.compressed_size = original_size
                result.format = source_format
                result.note = "original file is smaller - kept unchanged"
                return result

            _write_payload(payload, destination_path, source_path)
            result.destination = destination_path
            result.compressed_size = len(payload)
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        result.error = f"{type(exc).__name__}: {exc}"

    return result


# --------------------------------------------------------------------------- #
# Folder pipeline
# --------------------------------------------------------------------------- #

ProgressCallback = Callable[[int, int, "CompressionResult"], None]


def compress_folder(
    folder: str | os.PathLike[str],
    output_dir: str | os.PathLike[str] | None = None,
    settings: CompressionSettings | None = None,
    recursive: bool = True,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    files: Iterable[str | os.PathLike[str]] | None = None,
) -> list[CompressionResult]:
    """Compress every image inside *folder*.

    * *output_dir* is ``None`` -> files are written next to the originals with
      the ``name_suffix`` appended.
    * ``settings.overwrite is True`` -> originals are replaced in place.
    * Files that are already small enough are copied to *output_dir* so the
      output tree stays complete.
    * *files* may hold a list that was already collected with
      :func:`collect_images`; the folder is then not scanned again (the GUI uses
      this so the scan happens in the worker thread instead of the UI thread).

    *progress* is called as ``progress(index, total, result)`` after each file
    and *cancel* is checked between files, which makes the GUI's Cancel button
    work without killing the thread.
    """
    settings = settings or CompressionSettings()
    folder_path = Path(folder)
    output_root = Path(output_dir) if output_dir is not None else None
    base_root = folder_path if folder_path.is_dir() else folder_path.parent

    # Never re-process our own output, but only when the output folder really
    # sits inside the scanned folder (see :func:`ignored_output_dir`).
    hidden = ignored_output_dir(folder_path, output_root, settings.overwrite)
    if files is None:
        files = iter_images(folder_path, recursive=recursive, exclude_dir=hidden)
    else:
        files = [Path(item) for item in files]
        if hidden is not None:
            files = [item for item in files if not _is_within(item, hidden)]

    total = len(files)
    results: list[CompressionResult] = []

    for index, source in enumerate(files, start=1):
        if cancel is not None and cancel.is_set():
            break

        if settings.overwrite:
            destination = source
        else:
            destination = build_destination(source, base_root, output_root, settings)

        result = compress_image(source, destination, settings)

        # Keep the output folder complete: copy untouched files across.
        if (
            result.skipped
            and output_root is not None
            and not settings.overwrite
            and result.destination is not None
            and result.destination.resolve() != source.resolve()
        ):
            try:
                result.destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, result.destination)
                result.compressed_size = result.destination.stat().st_size
            except OSError as exc:
                result.error = f"{type(exc).__name__}: {exc}"
        elif result.skipped:
            result.destination = None

        results.append(result)
        if progress is not None:
            progress(index, total, result)

    return results


def summarise(results: Iterable[CompressionResult], elapsed: float | None = None) -> str:
    """Human readable report for a finished batch."""
    results = list(results)
    compressed = [item for item in results if item.ok and not item.skipped]
    skipped = [item for item in results if item.ok and item.skipped]
    failed = [item for item in results if not item.ok]
    before = sum(item.original_size for item in results)
    after = 0
    for item in results:
        if item.ok:
            after += item.compressed_size if not item.skipped else item.original_size
        else:
            after += item.original_size

    lines = [
        f"Files found      : {len(results)}",
        f"Compressed       : {len(compressed)}",
        f"Left unchanged   : {len(skipped)}",
        f"Failed           : {len(failed)}",
        f"Size before      : {human_size(before)}",
        f"Size after       : {human_size(after)}",
    ]
    if before:
        lines.append(f"Saved            : {human_size(before - after)} "
                     f"({100.0 * (before - after) / before:.1f} %)")
    if elapsed is not None:
        lines.append(f"Time             : {elapsed:.1f} s")
    if failed:
        lines.append("Failures:")
        lines.extend(f"  - {item.source.name}: {item.error}" for item in failed)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Command line interface
# --------------------------------------------------------------------------- #


def _format_argument(value: str) -> str:
    """Accept ``jpeg`` / ``jpg`` / ``JPG`` just as happily as ``JPEG``."""
    cleaned = str(value).strip().upper()
    aliases = {
        "JPG": "JPEG",
        "JPE": "JPEG",
        "JFIF": "JPEG",
        "KEEP": "original",
        "SAME": "original",
        "SOURCE": "original",
    }
    return aliases.get(cleaned, cleaned)


def build_parser() -> argparse.ArgumentParser:
    """Argument parser for ``python image_resizer.py --help``."""
    parser = argparse.ArgumentParser(
        prog="image_resizer",
        description=(
            "Shrink every image of a folder so that no file is bigger than the "
            "given limit (100 KB by default)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("folder", help="folder with the images (a single file works too)")
    parser.add_argument(
        "-o", "--output", default=None,
        help="folder for the compressed images (default: <folder>/compressed)",
    )
    parser.add_argument(
        "-s", "--max-kb", type=float, default=DEFAULT_MAX_BYTES / KIB,
        help="maximum size of a single image in KB",
    )
    parser.add_argument(
        "-f", "--format", default="original", type=_format_argument,
        choices=list(OUTPUT_FORMATS), metavar="{original,jpeg,png,webp}",
        help="output format, 'original' keeps the source format",
    )
    parser.add_argument("-w", "--max-width", type=int, default=None, help="downscale to this width")
    parser.add_argument("--max-height", type=int, default=None, help="downscale to this height")
    parser.add_argument("--min-quality", type=int, default=25, help="lowest quality allowed")
    parser.add_argument("--no-recursive", action="store_true", help="do not walk sub folders")
    parser.add_argument("--overwrite", action="store_true", help="replace the originals in place")
    parser.add_argument(
        "--keep-all", action="store_true",
        help="re-compress files that are already smaller than the limit",
    )
    parser.add_argument("--no-metadata", action="store_true", help="strip EXIF/ICC data")
    parser.add_argument("-q", "--quiet", action="store_true", help="only print the final report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of the CLI.  Returns the process exit code."""
    args = build_parser().parse_args(argv)

    folder = Path(args.folder)
    if not folder.exists():
        print(f"error: '{folder}' does not exist", file=sys.stderr)
        return 2

    output_dir = Path(args.output) if args.output else None
    if output_dir is None and not args.overwrite:
        output_dir = (folder if folder.is_dir() else folder.parent) / "compressed"

    try:
        settings = CompressionSettings(
            max_size_bytes=int(round(args.max_kb * KIB)),
            max_width=args.max_width,
            max_height=args.max_height,
            output_format=args.format,
            quality_floor=args.min_quality,
            skip_smaller=not args.keep_all,
            keep_metadata=not args.no_metadata,
            overwrite=args.overwrite,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    def on_progress(index: int, total: int, result: CompressionResult) -> None:
        if not args.quiet:
            print(f"[{index}/{total}] {result.summary()}", flush=True)

    started = time.perf_counter()
    recursive = not args.no_recursive
    hidden = ignored_output_dir(folder, output_dir, settings.overwrite)
    if not args.quiet:
        print(f"Scanning {folder} for images ...", flush=True)
    files = collect_images(
        folder, recursive=recursive, output_dir=output_dir, overwrite=settings.overwrite
    )
    if not args.quiet:
        if hidden is not None:
            print(f"Not scanning the output folder: {hidden}", flush=True)
        print(
            f"Found {len(files)} image(s) in {time.perf_counter() - started:.1f} s",
            flush=True,
        )

    if not files:
        print()
        print(f"No supported images found in: {folder}")
        if hidden is not None:
            print(f"(the output folder '{hidden}' is not scanned)")
        return 0

    results = compress_folder(
        folder, output_dir=output_dir, settings=settings,
        recursive=recursive, progress=on_progress, files=files,
    )
    elapsed = time.perf_counter() - started

    print()
    print(summarise(results, elapsed))
    if output_dir is not None and not settings.overwrite:
        print(f"Output folder    : {output_dir}")
    return 1 if any(not item.ok for item in results) else 0


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(main())
