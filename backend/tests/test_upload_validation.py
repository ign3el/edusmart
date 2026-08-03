"""Upload gate: extension allowlist, size cap, magic bytes, decompression bombs.

Regression cover for the 2026-08-03 audit finding that _validate_upload trusted
the filename extension and never looked at the bytes, and had no guard against
a ZIP archive that expands to gigabytes in memory.
"""
import io
import zipfile

import pytest
from fastapi import HTTPException

from main import (
    _validate_upload,
    _MAX_UPLOAD_SIZE_BYTES,
    _MAX_UNCOMPRESSED_ARCHIVE_BYTES,
)

PDF_MAGIC = b"%PDF-1.7\n"
ZIP_MAGIC = b"PK\x03\x04"


def _expect_block(filename, content, status):
    with pytest.raises(HTTPException) as exc:
        _validate_upload(filename, content)
    assert exc.value.status_code == status
    return exc.value


class TestExtensionAllowlist:
    def test_rejects_executable(self):
        _expect_block("payload.exe", b"MZ\x90\x00", 400)

    def test_rejects_no_extension(self):
        _expect_block("payload", b"anything", 400)

    @pytest.mark.parametrize("name", ["a.pdf", "a.docx", "a.pptx", "a.txt", "a.md", "a.csv"])
    def test_allows_supported_types(self, name):
        content = PDF_MAGIC if name.endswith(".pdf") else (
            _minimal_zip() if name.endswith((".docx", ".pptx")) else b"plain text content"
        )
        _validate_upload(name, content)  # must not raise


class TestSizeCap:
    def test_rejects_oversized(self):
        _expect_block("big.pdf", PDF_MAGIC + b"\0" * (_MAX_UPLOAD_SIZE_BYTES + 1), 413)


class TestMagicBytes:
    """Extension is a claim by the uploader; the parsers downstream trust the
    type they are handed. These assert the claim is checked against the bytes."""

    def test_rejects_pdf_renamed_as_docx(self):
        exc = _expect_block("spoofed.docx", PDF_MAGIC + b"body", 400)
        assert "does not match" in exc.detail

    def test_rejects_zip_renamed_as_pdf(self):
        _expect_block("spoofed.pdf", _minimal_zip(), 400)

    def test_accepts_genuine_pdf(self):
        _validate_upload("real.pdf", PDF_MAGIC + b"body")

    def test_text_formats_are_not_magic_checked(self):
        # No reliable signature exists for these and they are decoded as text,
        # so there is nothing to spoof into. Arbitrary bytes must pass.
        _validate_upload("notes.txt", b"\x00\x01\x02 whatever")


class TestDecompressionBomb:
    """The audit flagged this as plausible-not-proven; it was then confirmed
    with a real 498KB archive expanding to 500MB, which passed the size cap."""

    def test_blocks_bomb(self):
        bomb = _zip_with_declared_size(_MAX_UNCOMPRESSED_ARCHIVE_BYTES + (10 * 1024 * 1024))
        assert len(bomb) <= _MAX_UPLOAD_SIZE_BYTES, "bomb must pass the size cap to be a real test"
        exc = _expect_block("bomb.docx", bomb, 413)
        assert "expands to" in exc.detail

    def test_allows_normal_archive(self):
        ordinary = _zip_with_declared_size(1024 * 1024)  # 1MB uncompressed
        _validate_upload("lesson.docx", ordinary)

    def test_rejects_corrupt_archive(self):
        # Valid ZIP magic, garbage after it - must fail cleanly, not crash.
        _expect_block("broken.docx", ZIP_MAGIC + b"not really a zip", 400)


def _minimal_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", b"<Types/>")
    return buf.getvalue()


def _zip_with_declared_size(uncompressed_bytes: int) -> bytes:
    """A well-formed archive whose contents decompress to `uncompressed_bytes`.

    Highly compressible zeros, so the archive itself stays small - which is
    exactly what makes a decompression bomb dangerous.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", b"\0" * uncompressed_bytes)
    return buf.getvalue()
