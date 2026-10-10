# Standalone build: one folder, four exes sharing _internal, each with its own Task Manager name.
#   uv run --isolated --no-dev --with pyinstaller pyinstaller packaging/shelldeck.spec
# Layout once installed (shelldeck.frozen): <root>/versions/<ver>/<this folder's contents>, <root>/current.txt.
# All four run cli._main; `serve` starts sd-pty/sd-ui with explicit subcommands (ptyhost / ui). sd = the CLI.
import sys
from importlib.metadata import version
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent
VERSION = version("shelldeck")  # the installed package (CI builds from the wheel, so release candidates match)
NUMS = tuple(int(x) for x in VERSION.split("rc")[0].split(".")) + (0,)
ICON = str(ROOT / "packaging" / "icon.ico")  # from static/icon-512.png

datas, binaries, hidden = [], [], collect_submodules("uvicorn")
for pkg in ("shelldeck", "winpty", "apscheduler") if sys.platform == "win32" else ("shelldeck", "apscheduler"):
    d, b, h = collect_all(pkg)  # data files (static/, integration/, agent_detection/), ConPTY binaries, entry-point metadata
    datas, binaries, hidden = datas + d, binaries + b, hidden + h

a = Analysis([str(ROOT / "packaging" / "entry.py")], datas=datas, binaries=binaries, hiddenimports=hidden)
pyz = PYZ(a.pure)


def version_info(desc: str, name: str):
    # Windows only: the module needs pefile, which PyInstaller installs on Windows alone
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                     VarFileInfo, VarStruct, VSVersionInfo)

    table = StringTable("040904B0", [StringStruct("FileDescription", desc), StringStruct("ProductName", "shelldeck"),
                                     StringStruct("FileVersion", VERSION), StringStruct("ProductVersion", VERSION),
                                     StringStruct("OriginalFilename", f"{name}.exe")])
    return VSVersionInfo(ffi=FixedFileInfo(filevers=NUMS, prodvers=NUMS),
                         kids=[StringFileInfo([table]), VarFileInfo([VarStruct("Translation", [1033, 1200])])])


exes = [
    EXE(pyz, a.scripts, [], exclude_binaries=True, name=name, icon=ICON, console=True,
        version=version_info(desc, name) if sys.platform == "win32" else None)
    for name, desc in (("shelldeck", "shelldeck"), ("sd-pty", "sd-pty"), ("sd-ui", "sd-ui"), ("sd", "shelldeck CLI"))
]
coll = COLLECT(*exes, a.binaries, a.datas, name="shelldeck")
