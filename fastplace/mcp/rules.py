"""``record-rule`` storage — shared, committed markdown notes in
``.fastplace/rules/``.

Rules are grouped into per-area files (one file per meaningful directory of
the glob) with YAML ``paths:`` frontmatter, and ``index.md`` routes agents
from a file path to the rule file covering it. Layout mirrors the reference
design so agents trained on that flow feel at home.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_INDEX_NAME = "index.md"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


class RuleError(ValueError):
    """A rule could not be recorded (empty fields, unwritable location)."""


@dataclass(frozen=True)
class _Parsed:
    file: Path
    paths: list[str]
    body: str


def parse_frontmatter(content: str) -> tuple[list[str], str]:
    """Split ``paths:`` frontmatter; unparsable content keeps everything."""
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    match = _FRONTMATTER_RE.match(content)
    if match is None:
        return [], content

    paths: list[str] = []
    in_paths = False
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if stripped.startswith("paths:"):
            in_paths = True
            inline = stripped[len("paths:") :].strip()
            if inline.startswith("[") and inline.endswith("]"):
                paths = [
                    item.strip().strip("'\"") for item in inline[1:-1].split(",") if item.strip()
                ]
                in_paths = False
            continue
        if in_paths and stripped.startswith("- "):
            paths.append(stripped[2:].strip().strip("'\""))
    body = content[match.end() :]
    return [p for p in paths if p], body


class RuleRepository:
    """Read/write the ``.fastplace/rules`` tree under a project root."""

    def __init__(self, directory: Path, *, base_path: Path) -> None:
        self.directory = directory
        self.base_path = base_path
        self.index_path = directory / _INDEX_NAME

    # ------------------------------------------------------------- write

    def write(self, glob: str, title: str, note: str) -> Path:
        glob = self.normalize_glob(glob)
        title = re.sub(r"\s+", " ", title.replace("\r\n", "\n").replace("\r", "\n")).strip()
        note = note.strip()

        missing = [
            name for name, value in (("glob", glob), ("title", title), ("note", note)) if not value
        ]
        if missing:
            raise RuleError(
                "A rule needs a non-empty glob, title, and note. Missing or "
                f"empty: {', '.join(missing)}."
            )

        target = self._resolve_target(glob)
        if not target.file.exists():
            self._create_file(target.file, target.heading, [glob])
        else:
            self._ensure_glob_applied(target.file, glob)

        self._append_entry(target.file, title, note)
        self.write_index()
        return target.file

    def normalize_glob(self, glob: str) -> str:
        return self.relative_path(glob.strip())

    def relative_path(self, path: str | Path) -> str:
        text = str(path).replace("\\", "/")
        base = str(self.base_path).rstrip("/") + "/"
        base = base.replace("\\", "/")
        if text.startswith(base):
            text = text[len(base) :]
        return text.lstrip("/")

    def write_index(self) -> Path:
        rows: list[str] = []
        for parsed in sorted(self._parsed_files(), key=lambda p: self.relative_path(p.file)):
            if not parsed.paths:
                continue
            rows.append(f"| {', '.join(parsed.paths)} | {self.relative_path(parsed.file)} |")
        table = (
            "| Applies to | Rule file |\n| --- | --- |\n" + "\n".join(rows)
            if rows
            else "No rules recorded yet."
        )
        body = (
            "# Project Rules Index\n\n"
            "Before planning or editing, find the row whose globs match the "
            "file's path and read that rule file.\n\n" + table + "\n"
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(body, encoding="utf-8")
        return self.index_path

    # ------------------------------------------------------------ lookup

    def _parsed_files(self) -> list[_Parsed]:
        if not self.directory.is_dir():
            return []
        parsed: list[_Parsed] = []
        for file in sorted(self.directory.glob("*.md")):
            if file == self.index_path:
                continue
            try:
                content = file.read_text(encoding="utf-8")
            except OSError:
                continue
            paths, body = parse_frontmatter(content)
            parsed.append(_Parsed(file=file, paths=paths, body=body))
        return parsed

    def _resolve_target(self, glob: str) -> _Target:
        area = self._area_key(glob)
        for parsed in self._parsed_files():
            if glob in parsed.paths or any(self._area_key(path) == area for path in parsed.paths):
                return _Target(file=parsed.file, heading="")

        path = self._unique_file_path(glob)
        return _Target(file=path, heading=self._headline(path.stem))

    def _unique_file_path(self, glob: str) -> Path:
        segments = self._meaningful_segments(glob)
        taken = {parsed.file for parsed in self._parsed_files()}
        candidates = [
            slug
            for take in range(1, len(segments) + 1)
            if (slug := self._slug_for_segments(segments[-take:]))
        ] or ["general"]

        for candidate in candidates:
            path = self.directory / f"{candidate}.md"
            if path != self.index_path and path not in taken and not path.exists():
                return path

        base = candidates[-1]
        suffix = 2
        while True:
            path = self.directory / f"{base}-{suffix}.md"
            if path not in taken and not path.exists():
                return path
            suffix += 1

    def _area_key(self, glob: str) -> str:
        return "/".join(self._meaningful_segments(glob))

    @staticmethod
    def _meaningful_segments(glob: str) -> list[str]:
        segments = [s for s in glob.split("/") if s]
        directories = [s for s in segments if "*" not in s and "." not in s]
        return directories or segments

    @staticmethod
    def _slug_for_segments(segments: list[str]) -> str:
        words = [
            re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
            if ("*" in s or "." in s)
            else re.sub(r"(?<!^)(?=[A-Z])", "_", s).lower().replace(" ", "_")
            for s in segments
        ]
        return re.sub(r"-+", "-", "-".join(w for w in words if w))

    @staticmethod
    def _headline(stem: str) -> str:
        words = re.split(r"[-_]+", stem)
        return " ".join(w.capitalize() for w in words if w)

    # ------------------------------------------------------------- files

    def _create_file(self, path: Path, heading: str, paths: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._render_frontmatter(paths) + f"# {heading}\n", encoding="utf-8")

    def _ensure_glob_applied(self, path: Path, glob: str) -> None:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuleError(f"Cannot read rule file {path}: {exc}") from exc
        paths, body = parse_frontmatter(content)
        if glob in paths:
            return
        path.write_text(
            self._render_frontmatter([*paths, glob]) + body.lstrip("\n"),
            encoding="utf-8",
        )

    def _append_entry(self, path: Path, title: str, note: str) -> None:
        try:
            contents = path.read_text(encoding="utf-8").rstrip("\n")
        except OSError as exc:
            raise RuleError(f"Cannot read rule file {path}: {exc}") from exc
        path.write_text(f"{contents}\n\n## {title}\n{note}\n", encoding="utf-8")

    @staticmethod
    def _render_frontmatter(paths: list[str]) -> str:
        lines = ["---", "paths:"]
        lines += [f"- {p}" for p in paths]
        lines += ["---", ""]
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class _Target:
    file: Path
    heading: str
