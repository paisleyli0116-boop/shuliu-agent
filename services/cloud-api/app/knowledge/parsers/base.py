from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, fields
from io import BytesIO
import math
import stat
from typing import BinaryIO, Iterator, Protocol
import unicodedata
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import ParseError, Element


MIME_PDF = "application/pdf"
MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class DocumentParseError(ValueError):
    """A safe, stable error for user-supplied document parsing."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ParserLimits:
    max_file_size_bytes: int = 16 * 1024 * 1024
    max_pdf_pages: int = 500
    max_pdf_decoded_stream_bytes: int = 16 * 1024 * 1024
    max_pdf_decoding_work_bytes: int = 64 * 1024 * 1024
    max_xlsx_sheets: int = 50
    max_archive_entries: int = 2048
    max_uncompressed_archive_bytes: int = 64 * 1024 * 1024
    max_compression_ratio: float = 100.0
    max_xml_part_bytes: int = 16 * 1024 * 1024
    max_xml_elements: int = 500_000
    max_xlsx_rows: int = 100_000
    max_xlsx_cells: int = 500_000
    max_document_sections: int = 100_000
    max_text_characters: int = 5_000_000
    max_chunk_characters: int = 4000
    max_chunks: int = 10_000

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name == "max_compression_ratio":
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                    raise ValueError("document parser limits are invalid")
            elif (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < (0 if field.name == "max_pdf_pages" else 1)
            ):
                raise ValueError("document parser limits are invalid")


@dataclass(frozen=True, slots=True)
class ParsedTextSection:
    text: str
    source_reference: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    text_sections: tuple[ParsedTextSection, ...]
    warnings: tuple[str, ...] = ()


class DocumentParser(Protocol):
    def parse(
        self,
        file_stream: BinaryIO | bytes | bytearray | memoryview,
        *,
        filename: str,
        mime_type: str,
    ) -> ParsedDocument: ...


def read_bounded_stream(
    file_stream: BinaryIO | bytes | bytearray | memoryview,
    max_bytes: int,
) -> bytes:
    if isinstance(file_stream, bytes):
        data = file_stream[: max_bytes + 1]
    elif isinstance(file_stream, (bytearray, memoryview)):
        data = bytes(file_stream[: max_bytes + 1])
    elif callable(getattr(file_stream, "read", None)):
        collected = bytearray()
        try:
            while len(collected) <= max_bytes:
                chunk = file_stream.read(max_bytes + 1 - len(collected))
                if not isinstance(chunk, bytes):
                    raise DocumentParseError("DOCUMENT_READ_FAILED", "document could not be read")
                if not chunk:
                    break
                collected.extend(chunk)
            data = bytes(collected)
        except DocumentParseError:
            raise
        except Exception:
            raise DocumentParseError("DOCUMENT_READ_FAILED", "document could not be read") from None
    else:
        raise DocumentParseError("DOCUMENT_READ_FAILED", "document could not be read")
    if not isinstance(data, bytes):
        raise DocumentParseError("DOCUMENT_READ_FAILED", "document could not be read")
    if len(data) > max_bytes:
        raise DocumentParseError("DOCUMENT_TOO_LARGE", "document exceeds the configured size limit")
    if not data:
        raise DocumentParseError("DOCUMENT_MALFORMED", "document is empty or malformed")
    return data


def validate_filename_mime(filename: str, mime_type: str, expected_extension: str, expected_mime: str) -> None:
    if not isinstance(filename, str) or not filename.strip() or not isinstance(mime_type, str):
        raise DocumentParseError("DOCUMENT_TYPE_UNSUPPORTED", "document type is not supported")
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension != expected_extension:
        raise DocumentParseError("DOCUMENT_TYPE_UNSUPPORTED", "document type is not supported")
    if mime_type.strip().lower() != expected_mime:
        raise DocumentParseError("DOCUMENT_MIME_MISMATCH", "document content type does not match its file type")


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value.replace("\r\n", "\n").replace("\r", "\n"))
    normalized = "".join(
        char
        for char in value
        if char in "\n\t" or unicodedata.category(char) not in {"Cc", "Cf", "Cs", "Co", "Cn"}
    )
    return normalized.strip()


def checked_document(sections: list[ParsedTextSection], limits: ParserLimits) -> ParsedDocument:
    kept: list[ParsedTextSection] = []
    total_characters = 0
    for section in sections:
        text = normalize_text(section.text)
        if not text:
            continue
        total_characters += len(text)
        if len(kept) >= limits.max_document_sections or total_characters > limits.max_text_characters:
            raise DocumentParseError("DOCUMENT_TEXT_LIMIT_EXCEEDED", "document text exceeds the configured limit")
        kept.append(ParsedTextSection(text=text, source_reference=dict(section.source_reference)))
    if not kept:
        raise DocumentParseError("DOCUMENT_TEXT_UNAVAILABLE", "document contains no extractable text")
    return ParsedDocument(tuple(kept))


def checked_zip_path(name: str) -> bool:
    if not name or "\\" in name or "\x00" in name or name.startswith("/"):
        return False
    parts = name.rstrip("/").split("/")
    return all(part not in {"", ".", ".."} for part in parts)


@contextmanager
def validated_ooxml_archive(data: bytes, limits: ParserLimits) -> Iterator[ZipFile]:
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise DocumentParseError("DOCUMENT_ENCRYPTED", "encrypted Office documents are not supported")
    try:
        archive = ZipFile(BytesIO(data), "r")
    except (BadZipFile, OSError, ValueError):
        raise DocumentParseError("DOCUMENT_MALFORMED", "document package is malformed") from None
    try:
        infos = archive.infolist()
        if len(infos) > limits.max_archive_entries:
            raise DocumentParseError("DOCUMENT_ARCHIVE_LIMIT_EXCEEDED", "document package has too many entries")
        names: set[str] = set()
        total_size = 0
        for info in infos:
            if not checked_zip_path(info.filename) or info.filename.casefold() in names:
                raise DocumentParseError("DOCUMENT_MALFORMED", "document package is malformed")
            names.add(info.filename.casefold())
            if info.flag_bits & 0x1:
                raise DocumentParseError("DOCUMENT_ENCRYPTED", "encrypted document packages are not supported")
            if info.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
                raise DocumentParseError("DOCUMENT_MALFORMED", "document package uses an unsupported compression method")
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(unix_mode):
                raise DocumentParseError("DOCUMENT_MALFORMED", "document package is malformed")
            total_size += info.file_size
            if total_size > limits.max_uncompressed_archive_bytes:
                raise DocumentParseError(
                    "DOCUMENT_UNCOMPRESSED_SIZE_EXCEEDED", "document package exceeds the expanded size limit"
                )
            ratio = info.file_size / max(info.compress_size, 1)
            if info.file_size and ratio > limits.max_compression_ratio:
                raise DocumentParseError(
                    "DOCUMENT_COMPRESSION_RATIO_EXCEEDED", "document package exceeds the compression ratio limit"
                )
    except DocumentParseError:
        archive.close()
        raise
    except RuntimeError:
        archive.close()
        raise DocumentParseError("DOCUMENT_ENCRYPTED", "encrypted document packages are not supported") from None
    except (BadZipFile, OSError, ValueError):
        archive.close()
        raise DocumentParseError("DOCUMENT_MALFORMED", "document package is malformed") from None
    try:
        yield archive
    finally:
        archive.close()


def read_xml_part(archive: ZipFile, name: str, limits: ParserLimits) -> Element:
    try:
        info = archive.getinfo(name)
    except KeyError:
        raise DocumentParseError("DOCUMENT_MALFORMED", "required document content is missing") from None
    if info.file_size > limits.max_xml_part_bytes:
        raise DocumentParseError("DOCUMENT_XML_LIMIT_EXCEEDED", "document XML exceeds the configured limit")
    try:
        payload = archive.read(name)
        root = ElementTree.fromstring(payload, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException:
        raise DocumentParseError("DOCUMENT_UNSAFE_XML", "document XML contains unsupported declarations") from None
    except (BadZipFile, OSError, RuntimeError):
        raise DocumentParseError("DOCUMENT_MALFORMED", "document package is malformed") from None
    except (ParseError, ValueError, UnicodeError):
        raise DocumentParseError("DOCUMENT_MALFORMED", "document XML is malformed") from None
    except Exception:
        raise DocumentParseError("DOCUMENT_MALFORMED", "document package could not be safely decoded") from None
    if sum(1 for _ in root.iter()) > limits.max_xml_elements:
        raise DocumentParseError("DOCUMENT_XML_LIMIT_EXCEEDED", "document XML has too many elements")
    return root


def check_text_limit(current: int, added: str, limits: ParserLimits) -> int:
    total = current + len(added)
    if total > limits.max_text_characters:
        raise DocumentParseError("DOCUMENT_TEXT_LIMIT_EXCEEDED", "document text exceeds the configured limit")
    return total
