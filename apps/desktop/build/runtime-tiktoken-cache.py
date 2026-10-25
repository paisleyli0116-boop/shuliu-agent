"""PyInstaller runtime hook: point tiktoken at the bundled encoding cache.

litellm counts tokens through tiktoken, which normally downloads BPE files
on first use. The frozen sidecar must run fully offline, so the encoding
files are shipped as datas and selected through TIKTOKEN_CACHE_DIR.
"""
import os
import sys

base = getattr(sys, "_MEIPASS", None)
if base:
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", os.path.join(base, "tiktoken_cache"))
