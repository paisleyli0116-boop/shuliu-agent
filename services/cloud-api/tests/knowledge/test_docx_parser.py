from __future__ import annotations

from io import BytesIO
import struct
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.knowledge.parsers.base import DocumentParseError, ParserLimits
from app.knowledge.parsers.docx import DocxParser


CONTENT_TYPES = b'''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml" />
</Types>'''


def _docx(document_xml: bytes, extra_entries: tuple[tuple[str, bytes], ...] = ()) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("word/document.xml", document_xml)
        for name, payload in extra_entries:
            archive.writestr(name, payload)
    return buffer.getvalue()


def _document_xml(body: bytes) -> bytes:
    return (
        b'''<?xml version="1.0" encoding="UTF-8"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>'''
        + body
        + b"</w:body></w:document>"
    )


def test_docx_parser_extracts_paragraph_and_table_with_source_references() -> None:
    document = _docx(
        _document_xml(
            b"""<w:p><w:r><w:t>Company policy</w:t></w:r></w:p>
            <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Department</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>Finance</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"""
        )
    )

    parsed = DocxParser().parse(
        BytesIO(document),
        filename="policy.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert [section.text for section in parsed.text_sections] == ["Company policy", "Department | Finance"]
    assert parsed.text_sections[0].source_reference == {"part": "word/document.xml", "paragraph": 1}
    assert parsed.text_sections[1].source_reference == {
        "part": "word/document.xml", "table": 1, "row": 1
    }


def test_docx_parser_rejects_dtd_entities_and_macro_enabled_documents() -> None:
    parser = DocxParser()
    unsafe = _docx(
        b'''<!DOCTYPE w:document [<!ENTITY secret "expanded">]>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:body><w:p><w:r><w:t>&secret;</w:t></w:r></w:p></w:body></w:document>'''
    )

    with pytest.raises(DocumentParseError) as xml_error:
        parser.parse(
            BytesIO(unsafe),
            filename="unsafe.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    assert xml_error.value.code == "DOCUMENT_UNSAFE_XML"

    with pytest.raises(DocumentParseError) as macro_error:
        parser.parse(
            BytesIO(_docx(_document_xml(b"<w:p><w:r><w:t>text</w:t></w:r></w:p>"))),
            filename="macro.docm",
            mime_type="application/vnd.ms-word.document.macroEnabled.12",
        )
    assert macro_error.value.code == "DOCUMENT_TYPE_UNSUPPORTED"

    with pytest.raises(DocumentParseError) as encrypted_error:
        parser.parse(
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1encrypted office payload",
            filename="locked.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    assert encrypted_error.value.code == "DOCUMENT_ENCRYPTED"


def test_docx_parser_rejects_embedded_macros_and_unsafe_archive_paths() -> None:
    valid_document = _document_xml(b"<w:p><w:r><w:t>safe</w:t></w:r></w:p>")
    parser = DocxParser()

    with pytest.raises(DocumentParseError) as macro_error:
        parser.parse(
            _docx(valid_document, (("word/vbaProject.bin", b"macro"),)),
            filename="report.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    assert macro_error.value.code == "DOCUMENT_MACROS_UNSUPPORTED"

    with pytest.raises(DocumentParseError) as path_error:
        parser.parse(
            _docx(valid_document, (("../outside.txt", b"outside"),)),
            filename="path.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    assert path_error.value.code == "DOCUMENT_MALFORMED"


def test_docx_parser_enforces_archive_entry_and_total_expansion_limits() -> None:
    document = _docx(
        _document_xml(b"<w:p><w:r><w:t>bounded</w:t></w:r></w:p>"),
        (("custom.xml", b"bounded"),),
    )
    with pytest.raises(DocumentParseError) as entry_limit:
        DocxParser(ParserLimits(max_archive_entries=2)).parse(
            document,
            filename="entries.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    assert entry_limit.value.code == "DOCUMENT_ARCHIVE_LIMIT_EXCEEDED"

    with pytest.raises(DocumentParseError) as expansion_limit:
        DocxParser(ParserLimits(max_uncompressed_archive_bytes=128)).parse(
            document,
            filename="expanded.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    assert expansion_limit.value.code == "DOCUMENT_UNCOMPRESSED_SIZE_EXCEEDED"


def test_docx_parser_maps_corrupt_deflate_stream_to_a_safe_error_code() -> None:
    payload = bytearray(_docx(_document_xml(b"<w:p><w:r><w:t>safe text</w:t></w:r></w:p>")))
    with ZipFile(BytesIO(payload), "r") as archive:
        info = archive.getinfo("word/document.xml")
        name_length, extra_length = struct.unpack_from("<HH", payload, info.header_offset + 26)
        compressed_data_offset = info.header_offset + 30 + name_length + extra_length
    payload[compressed_data_offset] ^= 0xFF

    with pytest.raises(DocumentParseError) as malformed:
        DocxParser().parse(
            bytes(payload),
            filename="corrupt.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    assert malformed.value.code == "DOCUMENT_MALFORMED"


def test_docx_parser_enforces_archive_expansion_and_file_size_limits() -> None:
    repetitive_xml = _document_xml(
        b"<w:p><w:r><w:t>" + (b"repeated text " * 500) + b"</w:t></w:r></w:p>"
    )
    parser = DocxParser(ParserLimits(max_compression_ratio=2.0))

    with pytest.raises(DocumentParseError) as expansion:
        parser.parse(
            BytesIO(_docx(repetitive_xml)),
            filename="large.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    assert expansion.value.code == "DOCUMENT_COMPRESSION_RATIO_EXCEEDED"

    with pytest.raises(DocumentParseError) as size:
        DocxParser(ParserLimits(max_file_size_bytes=8)).parse(
            BytesIO(_docx(_document_xml(b"<w:p/>"))),
            filename="large.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    assert size.value.code == "DOCUMENT_TOO_LARGE"
