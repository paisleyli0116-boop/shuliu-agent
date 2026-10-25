"""Diagnostics-only stderr capture for the browser driver subprocess.

The backend deliberately never propagates driver stderr (it can contain page
text and paths). CI diagnostics need the raw stderr to explain Chrome startup
failures on hosted runners, so an explicit opt-in file sink is provided via
SHULIU_BROWSER_DEBUG_STDERR_FILE. These tests cover the capture helper only;
the file is written locally by the host that opted in.
"""
from __future__ import annotations

from io import BytesIO

from shuliu_runtime.browser.backend import _capture_stderr_to_sink


def test_capture_stderr_writes_chunks_until_eof():
    sink = BytesIO()
    stream = BytesIO(b"line one\nline two\n")

    _capture_stderr_to_sink(stream, sink.write)

    assert sink.getvalue() == b"line one\nline two\n"


def test_capture_stderr_swallows_stream_errors():
    class BrokenStream:
        def read(self, _size):
            raise OSError("stream gone")

    sink = BytesIO()

    _capture_stderr_to_sink(BrokenStream(), sink.write)  # must not raise

    assert sink.getvalue() == b""
