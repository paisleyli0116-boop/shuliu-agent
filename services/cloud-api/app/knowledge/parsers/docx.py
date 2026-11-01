from __future__ import annotations

from typing import BinaryIO

from .base import (
    MIME_DOCX,
    DocumentParseError,
    ParsedDocument,
    ParsedTextSection,
    ParserLimits,
    checked_document,
    read_bounded_stream,
    read_xml_part,
    validate_filename_mime,
    validated_ooxml_archive,
)


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MAIN_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"


class DocxParser:
    def __init__(self, limits: ParserLimits | None = None) -> None:
        self._limits = limits or ParserLimits()

    def parse(
        self,
        file_stream: BinaryIO | bytes | bytearray | memoryview,
        *,
        filename: str,
        mime_type: str,
    ) -> ParsedDocument:
        validate_filename_mime(filename, mime_type, "docx", MIME_DOCX)
        data = read_bounded_stream(file_stream, self._limits.max_file_size_bytes)
        sections: list[ParsedTextSection] = []
        total_characters = 0
        with validated_ooxml_archive(data, self._limits) as archive:
            names = {info.filename.casefold() for info in archive.infolist()}
            if any("vbaproject" in name for name in names):
                raise DocumentParseError("DOCUMENT_MACROS_UNSUPPORTED", "macro-enabled documents are not supported")
            content_types = read_xml_part(archive, "[Content_Types].xml", self._limits)
            content_type_values = {
                value.casefold()
                for element in content_types.iter()
                if (value := element.attrib.get("ContentType")) is not None
            }
            if any("macroenabled" in value or "vbaproject" in value for value in content_type_values):
                raise DocumentParseError("DOCUMENT_MACROS_UNSUPPORTED", "macro-enabled documents are not supported")
            if _MAIN_CONTENT_TYPE not in content_type_values:
                raise DocumentParseError("DOCUMENT_MALFORMED", "document package does not contain a DOCX main part")
            root = read_xml_part(archive, "word/document.xml", self._limits)
            if not root.tag.endswith("}document"):
                raise DocumentParseError("DOCUMENT_MALFORMED", "document XML has an invalid root")
            body = next((element for element in root if element.tag == _W + "body"), None)
            if body is None:
                raise DocumentParseError("DOCUMENT_MALFORMED", "document body is missing")

            paragraph_index = 0
            table_index = 0
            for block in body:
                if block.tag == _W + "p":
                    paragraph_index += 1
                    text = _word_paragraph_text(block)
                    total_characters += len(text)
                    if total_characters > self._limits.max_text_characters:
                        raise DocumentParseError(
                            "DOCUMENT_TEXT_LIMIT_EXCEEDED", "document text exceeds the configured limit"
                        )
                    if text.strip():
                        sections.append(
                            ParsedTextSection(text, {"part": "word/document.xml", "paragraph": paragraph_index})
                        )
                elif block.tag == _W + "tbl":
                    table_index += 1
                    for row_index, row in enumerate((item for item in block if item.tag == _W + "tr"), start=1):
                        cells: list[str] = []
                        for cell in (item for item in row if item.tag == _W + "tc"):
                            cell_text = "\n".join(
                                text for paragraph in cell.iter(_W + "p") if (text := _word_paragraph_text(paragraph))
                            )
                            cells.append(cell_text)
                        text = " | ".join(cells)
                        total_characters += len(text)
                        if total_characters > self._limits.max_text_characters:
                            raise DocumentParseError(
                                "DOCUMENT_TEXT_LIMIT_EXCEEDED", "document text exceeds the configured limit"
                            )
                        if text.strip():
                            sections.append(
                                ParsedTextSection(
                                    text,
                                    {"part": "word/document.xml", "table": table_index, "row": row_index},
                                )
                            )
        return checked_document(sections, self._limits)


def _word_paragraph_text(paragraph) -> str:
    output: list[str] = []
    for element in paragraph.iter():
        if element.tag == _W + "t" and element.text:
            output.append(element.text)
        elif element.tag == _W + "tab":
            output.append("\t")
        elif element.tag in {_W + "br", _W + "cr"}:
            output.append("\n")
    return "".join(output)
