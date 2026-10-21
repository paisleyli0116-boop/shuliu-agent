from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

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
    ],
    hiddenimports=[
        *collect_submodules("jsonschema", filter=exclude_test_modules),
        *collect_submodules("referencing", filter=exclude_test_modules),
        "keyring.backends.Windows",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
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
