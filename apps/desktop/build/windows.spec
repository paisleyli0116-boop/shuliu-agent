from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

repo_root = Path(SPECPATH).resolve().parents[2]
worker_root = repo_root / "runtimes" / "python-worker"
schema_root = repo_root / "packages" / "protocols" / "schemas"
exclude_test_modules = lambda name: ".tests" not in name and ".benchmarks" not in name

analysis = Analysis(
    [str(worker_root / "shuliu_runtime" / "ipc" / "server.py")],
    pathex=[str(worker_root)],
    binaries=[],
    datas=[
        (str(schema_root / "common-v1.schema.json"), "schemas"),
        (str(schema_root / "runtime-ipc-v1.schema.json"), "schemas"),
        # litellm loads package data (e.g. model_prices_and_context_window_backup.json)
        # at import time; without these files the frozen sidecar raises FileNotFoundError
        # inside every completion call.
        *collect_data_files("litellm"),
        # tiktoken BPE encodings for offline token counting (loaded via TIKTOKEN_CACHE_DIR).
        (str(repo_root / "apps" / "desktop" / "build" / "tiktoken_cache"), "tiktoken_cache"),
    ],
    hiddenimports=[
        *collect_submodules("jsonschema", filter=exclude_test_modules),
        *collect_submodules("referencing", filter=exclude_test_modules),
        *collect_submodules("litellm", filter=exclude_test_modules),
        *collect_submodules("tiktoken_ext"),
        "keyring.backends.Windows",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(repo_root / "apps" / "desktop" / "build" / "runtime-tiktoken-cache.py")],
    excludes=[],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="ShuLiuRuntime",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
)
