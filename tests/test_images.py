import asyncio
import io

import pytest
from fastapi import UploadFile

from app.services.images import ImageValidationError, save_upload, to_base64_data_url

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def make_upload(data: bytes, content_type: str, filename: str = "f.bin") -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=filename, headers={"content-type": content_type})


def run(coro):
    return asyncio.run(coro)


def test_save_png_ok():
    path = run(save_upload(make_upload(PNG_BYTES, "image/png", "a.png")))
    assert path.suffix == ".png"
    assert path.exists()
    data_url, b64 = to_base64_data_url(path)
    assert data_url.startswith("data:image/png;base64,")
    assert b64


def test_save_jpg_ok():
    path = run(save_upload(make_upload(JPG_BYTES, "image/jpeg", "a.jpg")))
    assert path.suffix == ".jpg"


def test_reject_bad_type():
    with pytest.raises(ImageValidationError):
        run(save_upload(make_upload(b"GIF89a....", "image/gif", "a.gif")))


def test_reject_magic_mismatch():
    with pytest.raises(ImageValidationError):
        run(save_upload(make_upload(b"not a png at all....", "image/png", "a.png")))


def test_reject_oversize():
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (5 * 1024 * 1024)
    with pytest.raises(ImageValidationError):
        run(save_upload(make_upload(big, "image/png", "big.png")))


def test_reject_empty():
    with pytest.raises(ImageValidationError):
        run(save_upload(make_upload(b"", "image/png", "e.png")))
