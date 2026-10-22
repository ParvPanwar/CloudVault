"""Bounded image/PDF processing; the worker invokes this module in a subprocess."""

import argparse
import io
import math
import subprocess
import sys
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path

MAX_INPUT_BYTES = 50 * 1024 * 1024
MAX_OUTPUT_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_PDF_PAGES = 100


class InvalidFile(ValueError):
    pass


class ProcessingTimeout(RuntimeError):
    pass


@dataclass
class Artifacts:
    body: bytes
    extension: str
    content_type: str
    thumbnail: bytes | None = None


def process_image(data, content_type):
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as source:
            expected = {"image/png": "PNG", "image/jpeg": "JPEG"}[content_type]
            if source.format != expected or source.width * source.height > MAX_IMAGE_PIXELS:
                raise InvalidFile("Invalid image type or pixel dimensions")
            source.load()
            source = ImageOps.exif_transpose(source).convert("RGBA")
            source.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
            image = Image.new("RGB", source.size, "white")
            image.paste(source, mask=source.getchannel("A"))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=max(10, min(30, image.width // 35)))
    label = "CloudVault / Processed"
    box = draw.textbbox((0, 0), label, font=font)
    width, height = box[2] - box[0], box[3] - box[1]
    x, y = max(4, image.width - width - 22), max(4, image.height - height - 24)
    draw.rounded_rectangle(
        (x - 7, y - 6, x + width + 7, y + height + 8), radius=5, fill=(25, 40, 58)
    )
    draw.text((x, y), label, fill="white", font=font)
    output = io.BytesIO()
    image.save(output, "JPEG", quality=85, optimize=True, progressive=True, exif=b"")
    image.thumbnail((360, 360), Image.Resampling.LANCZOS)
    thumbnail = io.BytesIO()
    image.save(thumbnail, "JPEG", quality=78, optimize=True, exif=b"")
    return Artifacts(output.getvalue(), "jpg", "image/jpeg", thumbnail.getvalue())


def process_pdf(data):
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject
    from reportlab.pdfgen import canvas

    if not data.startswith(b"%PDF-"):
        raise InvalidFile("Invalid PDF header")
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted or not 1 <= len(reader.pages) <= MAX_PDF_PAGES:
        raise InvalidFile("Encrypted PDFs or PDFs above 100 pages are not supported")
    writer = PdfWriter()
    for page in reader.pages:
        page = writer.add_page(page)
        page.transfer_rotation_to_content()
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        if not all(math.isfinite(n) and 0 < n <= 14400 for n in [width, height]):
            raise InvalidFile("Unsupported PDF page dimensions")
        overlay = io.BytesIO()
        stamp = canvas.Canvas(overlay, pagesize=(width, height), invariant=1)
        stamp.setFillColorRGB(0.30, 0.38, 0.48)
        stamp.setFont("Helvetica", min(9, width / 40))
        stamp.drawRightString(width - 16, 14, "CloudVault / Processed")
        stamp.save()
        page.merge_page(PdfReader(overlay).pages[0])
        page.pop(NameObject("/AA"), None)
        page.pop(NameObject("/Annots"), None)
        page.compress_content_streams()
    writer.add_metadata({"/Producer": "CloudVault", "/Title": "CloudVault processed document"})
    output = io.BytesIO()
    writer.write(output)
    return Artifacts(output.getvalue(), "pdf", "application/pdf")


def process(data, content_type):
    if not 0 < len(data) <= MAX_INPUT_BYTES:
        raise InvalidFile("File exceeds processing limits")
    result = (
        process_pdf(data)
        if content_type == "application/pdf"
        else process_image(data, content_type)
    )
    if len(result.body) > MAX_OUTPUT_BYTES:
        raise InvalidFile("Processed output exceeds limits")
    return result


def process_isolated(data, content_type, timeout=60):
    extension = "pdf" if content_type == "application/pdf" else "jpg"
    with tempfile.TemporaryDirectory(prefix="cloudvault-process-") as directory:
        root = Path(directory)
        (root / "input").write_bytes(data)
        try:
            child = subprocess.run(
                [sys.executable, "-m", "worker.processor", str(root), content_type, str(timeout)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProcessingTimeout("Processing exceeded its time limit") from exc
        if child.returncode:
            raise InvalidFile("File is invalid, unsupported, or exceeds processing limits")
        main = root / ("processed." + extension)
        if main.stat().st_size > MAX_OUTPUT_BYTES:
            raise InvalidFile("Processed output exceeds limits")
        thumbnail = root / "thumbnail.jpg"
        return Artifacts(
            main.read_bytes(),
            extension,
            "application/pdf" if extension == "pdf" else "image/jpeg",
            thumbnail.read_bytes() if thumbnail.exists() else None,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("content_type", choices=["application/pdf", "image/png", "image/jpeg"])
    parser.add_argument("timeout", type=int)
    args = parser.parse_args()
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (args.timeout, args.timeout + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    try:
        result = process((args.directory / "input").read_bytes(), args.content_type)
        (args.directory / ("processed." + result.extension)).write_bytes(result.body)
        if result.thumbnail:
            (args.directory / "thumbnail.jpg").write_bytes(result.thumbnail)
    except Exception:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
