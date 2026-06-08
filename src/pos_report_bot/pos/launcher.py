from collections.abc import Mapping
from dataclasses import dataclass, field
import os
from pathlib import Path


DEFAULT_POS_EXECUTABLE_NAME = "SPA1.exe"
DEFAULT_POS_APPREF_SUFFIX = ".appref-ms"
DEFAULT_CLICKONCE_RELATIVE_ROOT = Path("Apps") / "2.0"
APPREF_KEYWORDS = ("spa", "pos")


@dataclass(frozen=True)
class PosExecutableResolution:
    path: Path | None
    configured_path: Path | None
    appref_roots: list[Path] = field(default_factory=list)
    clickonce_roots: list[Path] = field(default_factory=list)
    candidates: list[Path] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.path is not None

    def failure_message(self) -> str:
        configured = str(self.configured_path) if self.configured_path is not None else "(未設定)"
        appref_roots = "、".join(str(root) for root in self.appref_roots) or "(無)"
        clickonce_roots = "、".join(str(root) for root in self.clickonce_roots) or "(無)"
        return (
            f"找不到 POS 啟動檔。設定路徑：{configured}；"
            f"已搜尋捷徑目錄：{appref_roots}；已搜尋 ClickOnce 目錄：{clickonce_roots}"
        )


def resolve_pos_executable_path(executable_text: str, *, environ: Mapping[str, str] | None = None) -> PosExecutableResolution:
    env = environ if environ is not None else os.environ
    configured_path = Path(executable_text.strip()) if executable_text.strip() else None
    if configured_path is not None and configured_path.exists():
        return PosExecutableResolution(path=configured_path, configured_path=configured_path)

    appref_roots = _appref_search_roots(env)
    appref_candidates = _find_pos_apprefs(appref_roots)
    if appref_candidates:
        return PosExecutableResolution(
            path=appref_candidates[0],
            configured_path=configured_path,
            appref_roots=appref_roots,
            clickonce_roots=[],
            candidates=appref_candidates,
        )

    clickonce_roots = _clickonce_search_roots(env)
    candidates = _find_clickonce_pos_executables(clickonce_roots)
    if candidates:
        return PosExecutableResolution(
            path=candidates[0],
            configured_path=configured_path,
            appref_roots=appref_roots,
            clickonce_roots=clickonce_roots,
            candidates=candidates,
        )

    return PosExecutableResolution(
        path=None,
        configured_path=configured_path,
        appref_roots=appref_roots,
        clickonce_roots=clickonce_roots,
        candidates=[],
    )


def _appref_search_roots(environ: Mapping[str, str]) -> list[Path]:
    roots: list[Path] = []
    appdata = environ.get("APPDATA", "").strip()
    if appdata:
        roots.append(Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    userprofile = environ.get("USERPROFILE", "").strip()
    if userprofile:
        roots.append(Path(userprofile) / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    programdata = environ.get("ProgramData", environ.get("PROGRAMDATA", "")).strip()
    if programdata:
        roots.append(Path(programdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    if userprofile:
        roots.append(Path(userprofile) / "Desktop")
    public = environ.get("PUBLIC", "").strip()
    if public:
        roots.append(Path(public) / "Desktop")
    return _dedupe_existing_order(roots)


def _clickonce_search_roots(environ: Mapping[str, str]) -> list[Path]:
    roots: list[Path] = []
    for name in ("LOCALAPPDATA", "USERPROFILE"):
        value = environ.get(name, "").strip()
        if not value:
            continue
        base = Path(value)
        root = base / DEFAULT_CLICKONCE_RELATIVE_ROOT if name == "LOCALAPPDATA" else base / "AppData" / "Local" / DEFAULT_CLICKONCE_RELATIVE_ROOT
        roots.append(root)
    return _dedupe_existing_order(roots)


def _find_pos_apprefs(roots: list[Path]) -> list[Path]:
    candidates: list[tuple[int, Path]] = []
    for index, root in enumerate(roots):
        if not root.exists():
            continue
        try:
            matches = list(root.rglob(f"*{DEFAULT_POS_APPREF_SUFFIX}"))
        except OSError:
            continue
        candidates.extend((index, path) for path in matches if path.is_file() and _looks_like_pos_appref(path))
    return [path for _, path in sorted(candidates, key=_appref_candidate_sort_key)]


def _looks_like_pos_appref(path: Path) -> bool:
    lowered = path.stem.lower()
    return any(keyword in lowered for keyword in APPREF_KEYWORDS)


def _find_clickonce_pos_executables(roots: list[Path]) -> list[Path]:
    candidates: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        try:
            matches = list(root.rglob(DEFAULT_POS_EXECUTABLE_NAME))
        except OSError:
            continue
        candidates.extend(path for path in matches if path.is_file())
    return sorted(candidates, key=_candidate_sort_key, reverse=True)


def _candidate_sort_key(path: Path) -> tuple[float, str]:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (mtime, str(path))


def _appref_candidate_sort_key(candidate: tuple[int, Path]) -> tuple[int, float, str]:
    root_index, path = candidate
    mtime, path_text = _candidate_sort_key(path)
    return (root_index, -mtime, path_text)


def _dedupe_existing_order(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    for path in paths:
        if path not in result:
            result.append(path)
    return result
