"""File skills — REQ-20, REQ-21.

`organize_folder` is the reference implementation of a safe consequential
action: it plans first, previews the plan with real counts, moves nothing until
confirmed, never deletes, and records enough to put every file back.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..base import Skill, SkillContext, SkillError, SkillParam, SkillResult
from . import paths

CATEGORIES: dict[str, tuple[str, ...]] = {
    "Images": (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".heic", ".tiff", ".ico"),
    "Documents": (".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt", ".epub", ".pages"),
    "Spreadsheets": (".xls", ".xlsx", ".csv", ".ods", ".tsv"),
    "Presentations": (".ppt", ".pptx", ".odp", ".key"),
    "Audio": (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma"),
    "Video": (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".flv"),
    "Archives": (".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso"),
    "Installers": (".exe", ".msi", ".appx", ".msix"),
    "Code": (".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h", ".cs",
             ".go", ".rs", ".rb", ".php", ".sh", ".ps1", ".sql", ".json", ".yaml", ".yml"),
}

_EXT_TO_CATEGORY = {ext: name for name, exts in CATEGORIES.items() for ext in exts}
OTHER = "Other"


def _category_for(path: Path) -> str:
    return _EXT_TO_CATEGORY.get(path.suffix.lower(), OTHER)


def _bucket_for(path: Path, mode: str) -> str:
    if mode == "date":
        try:
            stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            return "Unknown date"
        return stamp.strftime("%Y-%m")
    return _category_for(path)


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


class OrganizeFolderSkill(Skill):
    name = "system.organize_folder"
    description = (
        "Tidy a folder by moving its loose files into subfolders, grouped by file type "
        "or by month. Only moves files that sit directly in the folder; never touches "
        "existing subfolders and never deletes anything."
    )
    parameters = (
        SkillParam("folder", "string", "The folder to organize, e.g. 'Downloads'."),
        SkillParam(
            "mode", "string", "Group by 'type' (default) or 'date'.",
            required=False, default="type", enum=("type", "date"),
        ),
    )
    consequential = True
    reversible = True

    def _plan(self, args: dict[str, Any]) -> tuple[Path, str, list[tuple[Path, Path]]]:
        folder = paths.resolve_allowed(str(args["folder"]))
        if not folder.is_dir():
            raise SkillError(f"'{folder}' is a file, not a folder.")

        mode = str(args.get("mode", "type")).lower()
        if mode not in {"type", "date"}:
            mode = "type"

        existing_buckets = set(CATEGORIES) | {OTHER}
        moves: list[tuple[Path, Path]] = []
        for entry in sorted(folder.iterdir()):
            if entry.is_dir():
                continue
            if entry.name.startswith("."):  # leave dotfiles where they are
                continue
            bucket = _bucket_for(entry, mode)
            # Don't re-file something already sitting in a bucket folder.
            if entry.parent.name in existing_buckets:
                continue
            destination = folder / bucket / entry.name
            if destination == entry:
                continue
            moves.append((entry, destination))

        return folder, mode, moves

    def preview(self, args: dict[str, Any]) -> str:
        folder, mode, moves = self._plan(args)
        if not moves:
            return f"Nothing to organize in {folder} — no loose files."

        buckets: dict[str, int] = {}
        for _, destination in moves:
            buckets[destination.parent.name] = buckets.get(destination.parent.name, 0) + 1
        breakdown = ", ".join(f"{count} -> {name}" for name, count in sorted(buckets.items()))
        grouping = "file type" if mode == "type" else "month modified"
        return (
            f"Move {len(moves)} files in {folder} into {len(buckets)} subfolders "
            f"by {grouping} ({breakdown}). Nothing is deleted; fully undoable."
        )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        folder, mode, moves = self._plan(args)
        if not moves:
            return SkillResult(ok=True, message=f"Nothing to organize in {folder}.", data={"moved": 0})

        performed: list[dict[str, str]] = []
        created_dirs: list[str] = []
        errors: list[str] = []

        for source, destination in moves:
            try:
                if not destination.parent.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    created_dirs.append(str(destination.parent))
                final = _unique_destination(destination)
                shutil.move(str(source), str(final))
                performed.append({"from": str(source), "to": str(final)})
            except (OSError, shutil.Error) as exc:
                # Stop on first failure and report honestly what already moved,
                # rather than half-finishing quietly (design.md §5).
                errors.append(f"{source.name}: {exc}")
                break

        undo_payload = {"moves": performed, "created_dirs": created_dirs}

        if errors:
            return SkillResult(
                ok=False,
                message=(
                    f"Moved {len(performed)} of {len(moves)} files, then stopped: {errors[0]}. "
                    f"The {len(performed)} that moved can still be undone."
                ),
                data={"moved": len(performed), "planned": len(moves), "errors": errors},
                undo_payload=undo_payload,
            )

        buckets = {Path(m['to']).parent.name for m in performed}
        return SkillResult(
            ok=True,
            message=f"Moved {len(performed)} files in {folder} into {len(buckets)} subfolders.",
            data={"moved": len(performed), "folder": str(folder), "buckets": sorted(buckets)},
            undo_payload=undo_payload,
        )

    def undo(self, undo_payload: dict[str, Any]) -> SkillResult:
        moves = undo_payload.get("moves", [])
        restored, failed = 0, []

        for entry in reversed(moves):
            source, destination = Path(entry["to"]), Path(entry["from"])
            if not source.exists():
                failed.append(f"{source.name} is no longer there")
                continue
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(_unique_destination(destination)))
                restored += 1
            except (OSError, shutil.Error) as exc:
                failed.append(f"{source.name}: {exc}")

        # Remove bucket folders this run created, but only if they are now empty —
        # anything the user put there since stays put.
        for directory in sorted(undo_payload.get("created_dirs", []), key=len, reverse=True):
            path = Path(directory)
            try:
                if path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
            except OSError:
                pass

        if failed:
            return SkillResult(
                ok=False,
                message=f"Put {restored} files back; {len(failed)} could not be restored ({failed[0]}).",
            )
        return SkillResult(ok=True, message=f"Put {restored} files back where they were.")


class FindFilesSkill(Skill):
    name = "system.find_files"
    description = (
        "Find files by name fragment, extension, how recently they changed, or what "
        "they contain. Searches only the folders Kai is allowed to read. Returns "
        "candidates for the user to choose from — it does not open anything."
    )
    parameters = (
        SkillParam("query", "string", "Words from the filename, or an extension like '.pdf'.",
                   required=False),
        SkillParam("folder", "string", "Limit to one folder. Defaults to all allowed folders.",
                   required=False),
        SkillParam("days", "integer", "Only files modified in the last N days.", required=False),
        SkillParam("contains", "string", "Words the file's text should contain.",
                   required=False),
        SkillParam("limit", "integer", "Max results (default 15).", required=False, default=15),
    )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        query = str(args.get("query", "") or "").strip().lower()
        contains = str(args.get("contains", "") or "").strip()
        days = args.get("days")
        limit = int(args.get("limit", 15) or 15)

        # Content matching is answered from the document index (REQ-16, REQ-20)
        # rather than by opening every file on disk, which would be unusable on
        # a real Documents folder.
        content_paths: set[str] | None = None
        if contains:
            from ...index import store as index_store

            content_paths = {hit.path for hit in index_store.search(contains, limit=50)}
            if not content_paths:
                return SkillResult(
                    ok=True,
                    message=(
                        f"No indexed document contains '{contains}'. "
                        "Only indexed folders can be searched by content."
                    ),
                    data={"matches": []},
                )

        if args.get("folder"):
            roots: Iterable[Path] = [paths.resolve_allowed(str(args["folder"]))]
        else:
            roots = [r for r in paths.allowed_roots() if r.exists()]

        if not roots:
            raise SkillError(
                f"There are no readable folders configured ({paths.describe_roots()})."
            )

        cutoff = None
        if days:
            cutoff = datetime.now(timezone.utc).timestamp() - (int(days) * 86400)

        hits: list[tuple[float, Path, os.stat_result]] = []
        for root in roots:
            for path in _walk(root):
                if content_paths is not None and str(path) not in content_paths:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if cutoff is not None and stat.st_mtime < cutoff:
                    continue
                # With a content filter and no name query, matching content is
                # enough on its own.
                score = _match_score(path, query) if query else (2.0 if content_paths else 1.0)
                if score <= 0:
                    continue
                hits.append((score + stat.st_mtime / 1e12, path, stat))

        if not hits:
            scope = "those folders" if len(list(roots)) > 1 else str(list(roots)[0])
            return SkillResult(
                ok=True,
                message=f"No files matching that in {scope}.",
                data={"matches": []},
            )

        hits.sort(key=lambda item: item[0], reverse=True)
        top = hits[:limit]

        # Enough detail to tell candidates apart, per REQ-20 — never just names.
        lines = []
        matches = []
        for _, path, stat in top:
            modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
            lines.append(f"{path.name} — {_human_size(stat.st_size)}, modified {modified}, in {path.parent}")
            matches.append({
                "name": path.name,
                "path": str(path),
                "size": stat.st_size,
                "modified": modified,
            })

        more = f" (showing {len(top)} of {len(hits)})" if len(hits) > len(top) else ""
        return SkillResult(
            ok=True,
            message=f"{len(hits)} match{'es' if len(hits) != 1 else ''}{more}:\n" + "\n".join(lines),
            data={"matches": matches},
        )


def _walk(root: Path, max_depth: int = 4) -> Iterable[Path]:
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _: None):
        current = Path(dirpath)
        if len(current.parts) - root_depth >= max_depth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "$", "node_modules"))]
        for filename in filenames:
            yield current / filename


def _match_score(path: Path, query: str) -> float:
    if not query:
        return 1.0
    name = path.name.lower()
    if query.startswith("."):
        return 2.0 if path.suffix.lower() == query else 0.0
    if name == query:
        return 5.0
    if name.startswith(query):
        return 4.0
    if query in name:
        return 3.0
    terms = [t for t in query.split() if t]
    if terms and all(t in name for t in terms):
        return 2.0
    return 0.0


def _unique_destination(destination: Path) -> Path:
    """Never silently overwrite a file that is already there."""
    if not destination.exists():
        return destination
    stem, suffix, parent = destination.stem, destination.suffix, destination.parent
    for counter in range(1, 1000):
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
    raise SkillError(f"Too many files named like {destination.name}")
