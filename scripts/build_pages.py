#!/usr/bin/env python3
"""Build a small dependency-free static site from explicitly allowlisted public files."""

from __future__ import annotations

import argparse
import html
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_ROOTS = (ROOT / "docs", ROOT / "published", ROOT / "references" / "frontier")
ALLOWED_TOP_LEVEL = (ROOT / "README.md",)


def _render_link(match: re.Match[str]) -> str:
    label, target = match.groups()
    href = target
    if not target.startswith(("http://", "https://", "#", "/", "\\")):
        path, separator, fragment = target.partition("#")
        if ":" not in path and path.endswith(".md"):
            href = f"{path.removesuffix('.md')}.html"
            if separator:
                href = f"{href}#{fragment}"
    return f'<a href="{href}">{label}</a>'


def inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(
        r"\[([^]]+)\]\((https?://[^)]+|#[^)]*|[^):]+\.md(?:#[^)]*)?)\)",
        _render_link,
        escaped,
    )
    return escaped


def render_markdown(source: Path, title: str) -> str:
    body: list[str] = []
    in_code = False
    in_list = False
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            body.append(f"<p>{inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    for raw in source.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            flush_paragraph()
            if in_list:
                body.append("</ul>")
                in_list = False
            body.append("</code></pre>" if in_code else "<pre><code>")
            in_code = not in_code
            continue
        if in_code:
            body.append(html.escape(line) + "\n")
            continue
        heading = re.match(r"^(#{1,4})\s+(.+)$", line)
        if heading:
            flush_paragraph()
            if in_list:
                body.append("</ul>")
                in_list = False
            level = len(heading.group(1))
            body.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
        elif line.startswith("- "):
            flush_paragraph()
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{inline(line[2:])}</li>")
        elif not line:
            flush_paragraph()
            if in_list:
                body.append("</ul>")
                in_list = False
        else:
            paragraph.append(line)
    flush_paragraph()
    if in_list:
        body.append("</ul>")
    if in_code:
        raise ValueError(f"unclosed code fence in {source.relative_to(ROOT)}")
    content = "\n".join(body)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} · Splash Evals</title>
<style>
body{{max-width:58rem;margin:2rem auto;padding:0 1rem;font:16px/1.55 system-ui;color:#18202a}}
a{{color:#075985}} code,pre{{background:#f1f5f9}} pre{{padding:1rem;overflow:auto}}
table{{border-collapse:collapse}} td,th{{border:1px solid #cbd5e1;padding:.45rem;text-align:left}}
</style></head>
<body><nav><a href="index.html">Splash Evals</a></nav>{content}</body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "_site")
    args = parser.parse_args()
    output = args.output.resolve()
    if output == ROOT or ROOT not in output.parents:
        raise SystemExit("output must be a child of the repository")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    sources = list(ALLOWED_TOP_LEVEL)
    for allowed in ALLOWED_ROOTS:
        sources.extend(path for path in allowed.rglob("*") if path.is_file())
    for source in sorted(sources):
        if source.is_symlink():
            raise SystemExit(f"symlink rejected: {source.relative_to(ROOT)}")
        relative = source.relative_to(ROOT)
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix == ".md":
            destination = destination.with_suffix(".html")
            destination.write_text(
                render_markdown(source, source.stem.replace("-", " ").title()), encoding="utf-8"
            )
        elif source.suffix in {".yaml", ".yml"}:
            destination = destination.with_suffix(source.suffix + ".html")
            payload = html.escape(source.read_text(encoding="utf-8"))
            destination.write_text(
                "<!doctype html><meta charset='utf-8'>"
                f"<title>Historical reference</title><pre>{payload}</pre>",
                encoding="utf-8",
            )
        else:
            raise SystemExit(f"unapproved Pages file type: {relative}")

    docs_index = output / "docs" / "index.html"
    shutil.copyfile(docs_index, output / "index.html")
    (output / ".nojekyll").write_text("", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
