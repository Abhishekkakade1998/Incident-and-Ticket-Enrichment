"""Text extraction for the supported document formats (.md, .txt, .html, .json).

Each loader returns a ``Document`` with metadata and a list of ``(heading_path, text)``
sections. Headings are kept as structure (not text) so chunks can cite a section.
"""

from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from rag.models import Document

SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".html", ".htm", ".json"}
REQUIRED_FIELDS = ("doc_id", "title", "doc_type")


class ExtractionError(ValueError):
    pass


def _parse_list(value: str) -> list[str]:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return [v.strip().strip("'\"") for v in value.split(",") if v.strip()]


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Minimal YAML front-matter parser (``key: value`` and ``key: [a, b]``)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        raise ExtractionError("unterminated front matter")
    meta: dict[str, Any] = {}
    for line in text[3:end].strip().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if value.startswith("["):
            meta[key] = _parse_list(value)
        else:
            meta[key] = value.strip("'\"")
    return meta, text[end + 4 :].lstrip("\n")


def split_markdown_sections(body: str, title: str) -> list[tuple[str, str]]:
    """Split on markdown headings, tracking a 'H1 > H2 > H3' heading path."""
    sections: list[tuple[str, str]] = []
    stack: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        text = "\n".join(buf).strip()
        if text:
            sections.append((" > ".join(stack[1:]) or stack[0] if stack else title, text))
        buf.clear()

    for line in body.splitlines():
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            flush()
            level = len(m.group(1))
            stack[:] = stack[: level - 1]
            while len(stack) < level - 1:
                stack.append(title)
            stack.append(m.group(2).strip())
        else:
            buf.append(line)
    flush()
    return sections


class _HtmlText(HTMLParser):
    BLOCK = {"p", "li", "div", "br", "tr", "section", "article"}
    SKIP = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self.lines: list[str] = []
        self._skip = 0
        self._in_title = False
        self._heading: int | None = None
        self._cur: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "meta" and a.get("name") and a.get("content") is not None:
            self.meta[a["name"] or ""] = a["content"] or ""
        elif tag == "title":
            self._in_title = True
        elif re.fullmatch(r"h[1-4]", tag):
            self._end_line()
            self._heading = int(tag[1])
        elif tag == "li":
            self._end_line()
            self._cur.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif re.fullmatch(r"h[1-4]", tag) and self._heading:
            text = "".join(self._cur).strip()
            self._cur.clear()
            self.lines.append("#" * self._heading + " " + text)
            self._heading = None
        elif tag in self.BLOCK:
            self._end_line()

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title += data
            return
        self._cur.append(re.sub(r"\s+", " ", data))

    def _end_line(self) -> None:
        text = "".join(self._cur).strip()
        if text and text != "-":
            self.lines.append(text)
        self._cur.clear()

    def close(self) -> None:
        super().close()
        self._end_line()


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _finalise(meta: dict[str, Any], sections: list[tuple[str, str]], path: Path, root: Path) -> Document:
    missing = [f for f in REQUIRED_FIELDS if not meta.get(f)]
    if missing:
        raise ExtractionError(f"{path.name}: missing required metadata {missing}")
    for key in ("asset_types", "alarm_names", "sites"):
        v = meta.get(key, [])
        meta[key] = _parse_list(v) if isinstance(v, str) else list(v)
    trust = meta.get("trust", "verified")
    return Document(
        doc_id=str(meta["doc_id"]),
        title=str(meta["title"]),
        doc_type=str(meta["doc_type"]),
        source_path=str(path.relative_to(root)) if path.is_relative_to(root) else path.name,
        version=str(meta["version"]) if meta.get("version") else None,
        effective_date=meta.get("effective_date"),
        owner=meta.get("owner"),
        trust="unverified" if trust == "unverified" else "verified",
        asset_types=meta["asset_types"],
        alarm_names=meta["alarm_names"],
        sites=meta["sites"],
        sections=sections,
        checksum=_checksum(path),
    )


def load_document(path: Path, root: Path | None = None) -> Document:
    root = root or path.parent
    suffix = path.suffix.lower()
    raw = path.read_text(encoding="utf-8")
    if suffix in (".md", ".markdown", ".txt"):
        meta, body = parse_front_matter(raw)
        sections = split_markdown_sections(body, meta.get("title", path.stem))
    elif suffix in (".html", ".htm"):
        parser = _HtmlText()
        parser.feed(raw)
        parser.close()
        meta = {**parser.meta}
        meta.setdefault("title", parser.title.strip())
        sections = split_markdown_sections("\n".join(parser.lines), meta["title"])
    elif suffix == ".json":
        data = json.loads(raw)
        meta = {k: v for k, v in data.items() if k != "records"}
        sections = []
        for rec in data.get("records", []):
            sections.append((str(rec.get("section", "Record")), str(rec.get("text", ""))))
            # record-level metadata is merged up so filters still work
            for key in ("asset_types", "alarm_names"):
                meta.setdefault(key, [])
                meta[key] = sorted(set(meta[key]) | set(rec.get(key, [])))
    else:
        raise ExtractionError(f"unsupported file type: {path.suffix}")
    return _finalise(meta, sections, path, root)


def discover(docs_dir: Path) -> list[Path]:
    return sorted(p for p in docs_dir.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES)
