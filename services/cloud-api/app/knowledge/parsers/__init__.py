from __future__ import annotations

from typing import BinaryIO

from .base import DocumentParseError, DocumentParser, ParsedDocument, ParsedTextSection, ParserLimits
from .docx import DocxParser
from .pdf import PdfParser
from .xlsx import XlsxParser


def parse_document(
    file_stream: BinaryIO | bytes | bytearray | memoryview,
    *,
    filename: str,
    mime_type: str,
    limits: ParserLimits | None = None,
) -> ParsedDocument:
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    parsers: dict[str, DocumentParser] = {
        "pdf": PdfParser(limits),
        "docx": DocxParser(limits),
        "xlsx": XlsxParser(limits),
    }
    parser = parsers.get(extension)
    if parser is None:
        raise DocumentParseError("DOCUMENT_TYPE_UNSUPPORTED", "document type is not supported")
    return parser.parse(file_stream, filename=filename, mime_type=mime_type)


__all__ = [
    "DocxParser",
    "DocumentParseError",
    "DocumentParser",
    "ParsedDocument",
    "ParsedTextSection",
    "ParserLimits",
    "PdfParser",
    "XlsxParser",
    "parse_document",
]
