from __future__ import annotations

from io import BytesIO

import pytest
from pypdf import PdfWriter

from app.knowledge.parsers.base import DocumentParseError, ParserLimits
from app.knowledge.parsers.pdf import PdfParser
from app.knowledge.parsers import parse_document


def _pdf_with_page(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Resources "
        b"<< /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = f"BT /F1 12 Tf 20 100 Td ({escaped}) Tj ET".encode("ascii")
    objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    body = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body.extend(f"{number} 0 obj\n".encode())
        body.extend(obj)
        body.extend(b"\nendobj\n")
    xref_offset = len(body)
    body.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        body.extend(f"{offset:010d} 00000 n \n".encode())
    body.extend(
        f"trailer\n<< /Root 1 0 R /Size {len(offsets)} >>\nstartxref\n{xref_offset}\n%%EOF\n".encode()
    )
    return bytes(body)


def test_pdf_parser_extracts_text_with_page_source_reference() -> None:
    parsed = PdfParser().parse(
        BytesIO(_pdf_with_page("Hello knowledge")),
        filename="guide.pdf",
        mime_type="application/pdf",
    )

    assert len(parsed.text_sections) == 1
    assert parsed.text_sections[0].text == "Hello knowledge"
    assert parsed.text_sections[0].source_reference == {"page": 1}
    assert parsed.warnings == ()


def test_pdf_parser_rejects_encrypted_files_and_scans_without_text() -> None:
    parser = PdfParser()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.encrypt("test-password")
    encrypted_bytes = BytesIO()
    writer.write(encrypted_bytes)

    with pytest.raises(DocumentParseError) as encrypted:
        parser.parse(
            BytesIO(encrypted_bytes.getvalue()),
            filename="locked.pdf",
            mime_type="application/pdf",
        )
    assert encrypted.value.code == "DOCUMENT_ENCRYPTED"

    with pytest.raises(DocumentParseError) as scanned:
        parser.parse(
            BytesIO(_pdf_with_page("")),
            filename="scan.pdf",
            mime_type="application/pdf",
        )
    assert scanned.value.code == "DOCUMENT_TEXT_UNAVAILABLE"


def test_pdf_parser_returns_safe_errors_for_mime_mismatch_and_malformed_pdf() -> None:
    parser = PdfParser()
    with pytest.raises(DocumentParseError) as mismatch:
        parser.parse(
            BytesIO(_pdf_with_page("Hello")),
            filename="guide.pdf",
            mime_type="text/plain",
        )
    assert mismatch.value.code == "DOCUMENT_MIME_MISMATCH"

    with pytest.raises(DocumentParseError) as malformed:
        parser.parse(
            BytesIO(b"%PDF-1.4\nnot a valid pdf"),
            filename="broken.pdf",
            mime_type="application/pdf",
        )
    assert malformed.value.code == "DOCUMENT_MALFORMED"

    with pytest.raises(DocumentParseError) as unsupported:
        parse_document(
            BytesIO(_pdf_with_page("Hello")),
            filename="guide.doc",
            mime_type="application/pdf",
        )
    assert unsupported.value.code == "DOCUMENT_TYPE_UNSUPPORTED"


def test_pdf_parser_enforces_page_limit_before_extraction() -> None:
    parser = PdfParser(ParserLimits(max_pdf_pages=0))

    with pytest.raises(DocumentParseError) as too_many:
        parser.parse(
            BytesIO(_pdf_with_page("Hello")),
            filename="guide.pdf",
            mime_type="application/pdf",
        )

    assert too_many.value.code == "DOCUMENT_PAGE_LIMIT_EXCEEDED"
