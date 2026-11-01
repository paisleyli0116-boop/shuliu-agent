from __future__ import annotations

from io import BytesIO
from typing import BinaryIO

from pypdf import Configuration, PdfReader, apply_configuration

from .base import (
    MIME_PDF,
    DocumentParseError,
    ParsedDocument,
    ParsedTextSection,
    ParserLimits,
    checked_document,
    read_bounded_stream,
    validate_filename_mime,
)


class PdfParser:
    def __init__(self, limits: ParserLimits | None = None) -> None:
        self._limits = limits or ParserLimits()

    def parse(
        self,
        file_stream: BinaryIO | bytes | bytearray | memoryview,
        *,
        filename: str,
        mime_type: str,
    ) -> ParsedDocument:
        validate_filename_mime(filename, mime_type, "pdf", MIME_PDF)
        data = read_bounded_stream(file_stream, self._limits.max_file_size_bytes)
        decoded_limit = self._limits.max_pdf_decoded_stream_bytes
        configuration = Configuration(
            maximum_declared_stream_length=decoded_limit,
            array_based_stream_maximum_output_length=decoded_limit,
            jbig2_maximum_output_length=decoded_limit,
            lzw_maximum_output_length=decoded_limit,
            run_length_maximum_output_length=decoded_limit,
            zlib_maximum_output_length=decoded_limit,
            brotli_maximum_output_length=decoded_limit,
            image_maximum_buffer_size=decoded_limit,
            stream_filters_maximum_length=8,
            stream_decoding_work_maximum_length=self._limits.max_pdf_decoding_work_bytes,
            stream_parsing_work_maximum_count=min(self._limits.max_xml_elements, 1_000_000),
            xmp_maximum_input_length=min(decoded_limit, 1_000_000),
            xmp_maximum_element_count=min(self._limits.max_xml_elements, 100_000),
            page_tree_maximum_entries=max(1, self._limits.max_pdf_pages + 1),
        )
        try:
            with apply_configuration(configuration):
                reader = PdfReader(BytesIO(data), strict=True)
                if reader.is_encrypted:
                    raise DocumentParseError("DOCUMENT_ENCRYPTED", "encrypted PDF files are not supported")
                pages = reader.pages
                try:
                    page_count = len(pages)
                except Exception as error:
                    if type(error).__name__ == "LimitReachedError":
                        raise DocumentParseError(
                            "DOCUMENT_PAGE_LIMIT_EXCEEDED", "PDF exceeds the configured page limit"
                        ) from None
                    raise
                if page_count > self._limits.max_pdf_pages:
                    raise DocumentParseError("DOCUMENT_PAGE_LIMIT_EXCEEDED", "PDF exceeds the configured page limit")
                sections: list[ParsedTextSection] = []
                total_characters = 0
                for index, page in enumerate(pages, start=1):
                    text = page.extract_text() or ""
                    total_characters += len(text)
                    if total_characters > self._limits.max_text_characters:
                        raise DocumentParseError(
                            "DOCUMENT_TEXT_LIMIT_EXCEEDED", "document text exceeds the configured limit"
                        )
                    if text.strip():
                        sections.append(ParsedTextSection(text, {"page": index}))
        except DocumentParseError:
            raise
        except Exception as error:
            if type(error).__name__ == "LimitReachedError":
                raise DocumentParseError(
                    "DOCUMENT_DECOMPRESSION_LIMIT_EXCEEDED", "PDF exceeds a configured processing limit"
                ) from None
            raise DocumentParseError("DOCUMENT_MALFORMED", "PDF content is malformed or unsupported") from None
        return checked_document(sections, self._limits)
