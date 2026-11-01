from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.knowledge.parsers.base import DocumentParseError, ParserLimits
from app.knowledge.parsers.xlsx import XlsxParser


CONTENT_TYPES = b'''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml" />
</Types>'''


def _xlsx(
    sheets: tuple[tuple[str, bytes], ...],
    shared_strings: bytes | None = None,
    *,
    external_first_sheet: bool = False,
    extra_entries: tuple[tuple[str, bytes], ...] = (),
) -> bytes:
    buffer = BytesIO()
    workbook_sheets = []
    relationships = []
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        for index, (name, worksheet) in enumerate(sheets, start=1):
            workbook_sheets.append(f'<sheet name="{name}" sheetId="{index}" r:id="rId{index}"/>')
            target = 'https://example.invalid/external.xlsx' if external_first_sheet and index == 1 else f'worksheets/sheet{index}.xml'
            target_mode = ' TargetMode="External"' if external_first_sheet and index == 1 else ""
            relationships.append(
                f'<Relationship Id="rId{index}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                f'Target="{target}"{target_mode}/>'
            )
            archive.writestr(f"xl/worksheets/sheet{index}.xml", worksheet)
        workbook = (
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{''.join(workbook_sheets)}</sheets></workbook>"
        )
        rels = (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{''.join(relationships)}</Relationships>"
        )
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        if shared_strings is not None:
            archive.writestr("xl/sharedStrings.xml", shared_strings)
        for entry_name, payload in extra_entries:
            archive.writestr(entry_name, payload)
    return buffer.getvalue()


def _worksheet(rows: bytes) -> bytes:
    return (
        b'''<?xml version="1.0" encoding="UTF-8"?>
        <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
          <sheetData>'''
        + rows
        + b"</sheetData></worksheet>"
    )


def test_xlsx_parser_extracts_shared_and_inline_values_with_sheet_references() -> None:
    strings = b'''<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <si><t>Quarterly report</t></si></sst>'''
    worksheet = _worksheet(
        b'''<row r="1"><c r="A1" t="s"><v>0</v></c>
        <c r="B1" t="inlineStr"><is><t>Revenue</t></is></c>
        <c r="C1"><f>SUM(B2:B4)</f><v>42</v></c></row>'''
    )
    parsed = XlsxParser().parse(
        BytesIO(_xlsx((("Finance", worksheet),), strings)),
        filename="report.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    assert [section.text for section in parsed.text_sections] == [
        "A1: Quarterly report\tB1: Revenue\tC1: 42"
    ]
    assert parsed.text_sections[0].source_reference == {
        "sheet": "Finance", "row": 1, "cells": ["A1", "B1", "C1"]
    }


def test_xlsx_parser_rejects_dtd_and_excess_sheets() -> None:
    unsafe_workbook = b'''<!DOCTYPE workbook [<!ENTITY value "x">]>
      <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <sheets><sheet name="&value;" sheetId="1" r:id="rId1"/></sheets></workbook>'''
    unsafe = _xlsx((("Finance", _worksheet(b"")),))
    # Replace the workbook XML member using the same safe package builder.
    buffer = BytesIO()
    with ZipFile(BytesIO(unsafe), "r") as source, ZipFile(buffer, "w", ZIP_DEFLATED) as target:
        for entry in source.infolist():
            target.writestr(
                entry.filename,
                unsafe_workbook if entry.filename == "xl/workbook.xml" else source.read(entry.filename),
            )
    unsafe = buffer.getvalue()

    with pytest.raises(DocumentParseError) as xml_error:
        XlsxParser().parse(
            BytesIO(unsafe),
            filename="unsafe.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    assert xml_error.value.code == "DOCUMENT_UNSAFE_XML"

    with pytest.raises(DocumentParseError) as external_error:
        XlsxParser().parse(
            BytesIO(_xlsx((("Finance", _worksheet(b"")),), external_first_sheet=True)),
            filename="external.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    assert external_error.value.code == "DOCUMENT_EXTERNAL_REFERENCE_UNSUPPORTED"

    with pytest.raises(DocumentParseError) as sheet_limit:
        XlsxParser(ParserLimits(max_xlsx_sheets=1)).parse(
            BytesIO(_xlsx((("One", _worksheet(b"")), ("Two", _worksheet(b""))))),
            filename="many.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    assert sheet_limit.value.code == "DOCUMENT_SHEET_LIMIT_EXCEEDED"

    with pytest.raises(DocumentParseError) as encrypted_error:
        XlsxParser().parse(
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1encrypted office payload",
            filename="locked.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    assert encrypted_error.value.code == "DOCUMENT_ENCRYPTED"

    with pytest.raises(DocumentParseError) as macro_error:
        XlsxParser().parse(
            _xlsx((
                ("Finance", _worksheet(b'<row r="1"><c r="A1" t="inlineStr"><is><t>value</t></is></c></row>')),
            ), extra_entries=(("xl/vbaProject.bin", b"macro"),)),
            filename="macro.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    assert macro_error.value.code == "DOCUMENT_MACROS_UNSUPPORTED"


def test_xlsx_parser_enforces_compression_ratio_and_never_evaluates_formulas() -> None:
    repetitive = _worksheet(b'<row r="1"><c r="A1" t="inlineStr"><is><t>' + (b"X" * 4000) + b"</t></is></c></row>")
    with pytest.raises(DocumentParseError) as expansion:
        XlsxParser(ParserLimits(max_compression_ratio=2.0)).parse(
            BytesIO(_xlsx((("Data", repetitive),))),
            filename="bomb.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    assert expansion.value.code == "DOCUMENT_COMPRESSION_RATIO_EXCEEDED"

    formula = _worksheet(
        b'<row r="1"><c r="A1"><f>WEBSERVICE("https://example.invalid")</f></c></row>'
        b'<row r="2"><c r="B2" t="inlineStr"><is><t>safe value</t></is></c></row>'
    )
    parsed = XlsxParser().parse(
        BytesIO(_xlsx((("Data", formula),))),
        filename="formula.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert [section.text for section in parsed.text_sections] == ["B2: safe value"]


def test_xlsx_parser_enforces_row_and_cell_limits() -> None:
    two_rows = _worksheet(
        b'<row r="1"><c r="A1" t="inlineStr"><is><t>first</t></is></c></row>'
        b'<row r="2"><c r="A2" t="inlineStr"><is><t>second</t></is></c></row>'
    )
    with pytest.raises(DocumentParseError) as row_limit:
        XlsxParser(ParserLimits(max_xlsx_rows=1)).parse(
            BytesIO(_xlsx((("Data", two_rows),))),
            filename="rows.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    assert row_limit.value.code == "DOCUMENT_ROW_LIMIT_EXCEEDED"

    two_cells = _worksheet(
        b'<row r="1"><c r="A1" t="inlineStr"><is><t>one</t></is></c>'
        b'<c r="B1" t="inlineStr"><is><t>two</t></is></c></row>'
    )
    with pytest.raises(DocumentParseError) as cell_limit:
        XlsxParser(ParserLimits(max_xlsx_cells=1)).parse(
            BytesIO(_xlsx((("Data", two_cells),))),
            filename="cells.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    assert cell_limit.value.code == "DOCUMENT_CELL_LIMIT_EXCEEDED"


def test_xlsx_parser_stops_reading_a_row_as_soon_as_text_limit_is_exceeded() -> None:
    strings = b'''<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <si><t>0123456789</t></si></sst>'''
    worksheet = _worksheet(
        b'<row r="1"><c r="A1" t="s"><v>0</v></c>'
        b'<c r="B1" t="s"><v>9</v></c></row>'
    )

    with pytest.raises(DocumentParseError) as text_limit:
        XlsxParser(ParserLimits(max_text_characters=10, max_compression_ratio=10_000)).parse(
            BytesIO(_xlsx((("Data", worksheet),), strings)),
            filename="over-limit.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    assert text_limit.value.code == "DOCUMENT_TEXT_LIMIT_EXCEEDED"
