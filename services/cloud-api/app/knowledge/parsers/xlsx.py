from __future__ import annotations

import posixpath
import re
from typing import BinaryIO

from .base import (
    MIME_XLSX,
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


_MAIN_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_CELL_REFERENCE = re.compile(r"^([A-Z]{1,3})([1-9][0-9]{0,6})$")


class XlsxParser:
    def __init__(self, limits: ParserLimits | None = None) -> None:
        self._limits = limits or ParserLimits()

    def parse(
        self,
        file_stream: BinaryIO | bytes | bytearray | memoryview,
        *,
        filename: str,
        mime_type: str,
    ) -> ParsedDocument:
        validate_filename_mime(filename, mime_type, "xlsx", MIME_XLSX)
        data = read_bounded_stream(file_stream, self._limits.max_file_size_bytes)
        sections: list[ParsedTextSection] = []
        total_characters = 0
        total_rows = 0
        total_cells = 0

        with validated_ooxml_archive(data, self._limits) as archive:
            names = {info.filename.casefold() for info in archive.infolist()}
            if any("vbaproject" in name for name in names):
                raise DocumentParseError("DOCUMENT_MACROS_UNSUPPORTED", "macro-enabled documents are not supported")
            if any(name.startswith("xl/externallinks/") for name in names):
                raise DocumentParseError(
                    "DOCUMENT_EXTERNAL_REFERENCE_UNSUPPORTED", "workbooks with external references are not supported"
                )
            content_types = read_xml_part(archive, "[Content_Types].xml", self._limits)
            content_type_values = {
                value.casefold()
                for element in content_types.iter()
                if (value := element.attrib.get("ContentType")) is not None
            }
            if any("macroenabled" in value or "vbaproject" in value for value in content_type_values):
                raise DocumentParseError("DOCUMENT_MACROS_UNSUPPORTED", "macro-enabled documents are not supported")
            if _MAIN_CONTENT_TYPE not in content_type_values:
                raise DocumentParseError("DOCUMENT_MALFORMED", "document package does not contain an XLSX workbook")

            workbook = read_xml_part(archive, "xl/workbook.xml", self._limits)
            relationships = read_xml_part(archive, "xl/_rels/workbook.xml.rels", self._limits)
            if workbook.tag != _S + "workbook":
                raise DocumentParseError("DOCUMENT_MALFORMED", "workbook XML has an invalid root")
            relation_targets: dict[str, tuple[str, bool]] = {}
            for relation in relationships:
                if relation.tag != _PKG_REL + "Relationship":
                    continue
                relationship_id = relation.attrib.get("Id", "")
                target = relation.attrib.get("Target", "")
                external = relation.attrib.get("TargetMode", "").casefold() == "external"
                if not relationship_id or not target or relationship_id in relation_targets:
                    raise DocumentParseError("DOCUMENT_MALFORMED", "workbook relationships are invalid")
                relation_targets[relationship_id] = (target, external)

            sheet_collection = next((item for item in workbook if item.tag == _S + "sheets"), None)
            sheets = list(sheet_collection) if sheet_collection is not None else []
            if not sheets:
                raise DocumentParseError("DOCUMENT_TEXT_UNAVAILABLE", "workbook contains no worksheets")
            if len(sheets) > self._limits.max_xlsx_sheets:
                raise DocumentParseError("DOCUMENT_SHEET_LIMIT_EXCEEDED", "workbook exceeds the configured sheet limit")

            shared_strings: list[str] = []
            if "xl/sharedstrings.xml" in names:
                shared_root = read_xml_part(archive, "xl/sharedStrings.xml", self._limits)
                if shared_root.tag != _S + "sst":
                    raise DocumentParseError("DOCUMENT_MALFORMED", "shared strings XML has an invalid root")
                for item in shared_root:
                    if item.tag == _S + "si":
                        shared_strings.append(_rich_text(item))

            for sheet in sheets:
                if sheet.tag != _S + "sheet":
                    raise DocumentParseError("DOCUMENT_MALFORMED", "workbook sheet entry is invalid")
                sheet_name = sheet.attrib.get("name", "")
                relationship_id = sheet.attrib.get(_R + "id", "")
                if not sheet_name or len(sheet_name) > 128 or relationship_id not in relation_targets:
                    raise DocumentParseError("DOCUMENT_MALFORMED", "workbook sheet reference is invalid")
                target, external = relation_targets[relationship_id]
                if external:
                    raise DocumentParseError(
                        "DOCUMENT_EXTERNAL_REFERENCE_UNSUPPORTED", "workbooks with external sheet references are not supported"
                    )
                sheet_part = _worksheet_part(target)
                if sheet_part.casefold() not in names:
                    raise DocumentParseError("DOCUMENT_MALFORMED", "worksheet content is missing")
                worksheet = read_xml_part(archive, sheet_part, self._limits)
                if worksheet.tag != _S + "worksheet":
                    raise DocumentParseError("DOCUMENT_MALFORMED", "worksheet XML has an invalid root")
                sheet_data = next((item for item in worksheet if item.tag == _S + "sheetData"), None)
                if sheet_data is None:
                    continue
                for row_sequence, row in enumerate((item for item in sheet_data if item.tag == _S + "row"), start=1):
                    total_rows += 1
                    if total_rows > self._limits.max_xlsx_rows:
                        raise DocumentParseError("DOCUMENT_ROW_LIMIT_EXCEEDED", "workbook exceeds the configured row limit")
                    row_number = _positive_int(row.attrib.get("r"), row_sequence)
                    cells: list[tuple[str, str]] = []
                    row_text_characters = 0
                    for cell in (item for item in row if item.tag == _S + "c"):
                        total_cells += 1
                        if total_cells > self._limits.max_xlsx_cells:
                            raise DocumentParseError("DOCUMENT_CELL_LIMIT_EXCEEDED", "workbook exceeds the configured cell limit")
                        reference = cell.attrib.get("r", "")
                        match = _CELL_REFERENCE.fullmatch(reference)
                        if match is None or int(match.group(2)) != row_number or _column_number(match.group(1)) > 16_384:
                            raise DocumentParseError("DOCUMENT_MALFORMED", "worksheet cell reference is invalid")
                        value = _cell_value(cell, shared_strings)
                        value = value.strip()
                        if value:
                            row_text_characters += len(reference) + 2 + len(value) + (1 if cells else 0)
                            if total_characters + row_text_characters > self._limits.max_text_characters:
                                raise DocumentParseError(
                                    "DOCUMENT_TEXT_LIMIT_EXCEEDED",
                                    "document text exceeds the configured limit",
                                )
                            cells.append((reference, value))
                    if cells:
                        text = "\t".join(f"{reference}: {value}" for reference, value in cells)
                        total_characters += len(text)
                        if total_characters > self._limits.max_text_characters:
                            raise DocumentParseError(
                                "DOCUMENT_TEXT_LIMIT_EXCEEDED", "document text exceeds the configured limit"
                            )
                        sections.append(
                            ParsedTextSection(
                                text,
                                {"sheet": sheet_name, "row": row_number, "cells": [reference for reference, _ in cells]},
                            )
                        )
        return checked_document(sections, self._limits)


def _worksheet_part(target: str) -> str:
    if "\\" in target or "\x00" in target or target.startswith("//"):
        raise DocumentParseError("DOCUMENT_MALFORMED", "worksheet relationship target is invalid")
    normalized = posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join("xl", target))
    if not normalized.startswith("xl/worksheets/") or not normalized.endswith(".xml"):
        raise DocumentParseError("DOCUMENT_MALFORMED", "worksheet relationship target is invalid")
    if not all(part not in {"", ".", ".."} for part in normalized.split("/")):
        raise DocumentParseError("DOCUMENT_MALFORMED", "worksheet relationship target is invalid")
    return normalized


def _rich_text(element) -> str:
    return "".join(part.text or "" for part in element.iter() if part.tag == _S + "t")


def _cell_value(cell, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        inline = next((item for item in cell if item.tag == _S + "is"), None)
        return _rich_text(inline) if inline is not None else ""
    value = next((item for item in cell if item.tag == _S + "v"), None)
    if value is None or value.text is None:
        return ""
    raw_value = value.text
    if cell_type == "s":
        try:
            index = int(raw_value)
            return shared_strings[index] if 0 <= index < len(shared_strings) else _invalid_shared_string()
        except (ValueError, IndexError):
            return _invalid_shared_string()
    return raw_value


def _invalid_shared_string() -> str:
    raise DocumentParseError("DOCUMENT_MALFORMED", "worksheet shared-string reference is invalid")


def _positive_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        raise DocumentParseError("DOCUMENT_MALFORMED", "worksheet row reference is invalid") from None
    if parsed < 1 or parsed > 1_048_576:
        raise DocumentParseError("DOCUMENT_MALFORMED", "worksheet row reference is invalid")
    return parsed


def _column_number(column: str) -> int:
    value = 0
    for character in column:
        value = value * 26 + ord(character) - ord("A") + 1
    return value
