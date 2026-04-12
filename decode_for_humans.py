"""
decode_for_humans.py

Translates source code (and notebooks) into plain-English Markdown + TXT
explanations for non-technical readers.

Output:
  {name}_explanation.md   — Markdown (renders in GitHub, Obsidian, VS Code…)
  {name}_explanation.txt  — Plain text (always, no external tools needed)

Supported notebooks:
  .ipynb  Jupyter  — cells extracted, kernel language detected
  .qmd    Quarto   — read as text (YAML front-matter preserved for context)
  .rmd    R Markdown — read as text
  .jl     Pluto.jl — read as text (Pluto reactive cells detected)

Dependencies:
  pip install anthropic   # or openai / google-genai / mistralai / groq
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Callable

from providers import PROVIDERS, get_provider

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIG_DIR:  Path = Path.home() / ".decode_for_humans"
CONFIG_FILE: Path = CONFIG_DIR / "config.json"

SOURCE_LINE_LIMIT: int = 150
MAX_CODE_CHARS:    int = 80_000

# Map file extension → human-readable language name
EXTENSION_MAP: dict[str, str] = {
    ".py": "Python", ".pyw": "Python",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".mts": "TypeScript",
    ".jsx": "React (JSX)", ".tsx": "React (TSX)",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin",
    ".scala": "Scala", ".groovy": "Groovy",
    ".c": "C", ".h": "C",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++",
    ".cs": "C#", ".fs": "F#", ".vb": "Visual Basic",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby", ".rake": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".pl": "Perl", ".pm": "Perl",
    ".lua": "Lua",
    ".sh": "Shell / Bash", ".bash": "Shell / Bash",
    ".zsh": "Zsh", ".fish": "Fish Shell",
    ".ps1": "PowerShell", ".psm1": "PowerShell",
    ".r": "R", ".rmd": "R Markdown",
    ".jl": "Julia",
    ".sql": "SQL",
    ".m": "MATLAB / Objective-C",
    ".ex": "Elixir", ".exs": "Elixir",
    ".erl": "Erlang", ".hrl": "Erlang",
    ".hs": "Haskell", ".lhs": "Haskell",
    ".ml": "OCaml", ".mli": "OCaml",
    ".clj": "Clojure", ".cljs": "ClojureScript",
    ".dart": "Dart",
    ".html": "HTML", ".htm": "HTML",
    ".css": "CSS", ".scss": "SCSS", ".sass": "Sass", ".less": "Less",
    ".yaml": "YAML", ".yml": "YAML",
    ".json": "JSON", ".jsonc": "JSON",
    ".xml": "XML", ".svg": "SVG",
    ".toml": "TOML", ".ini": "INI", ".cfg": "Config",
    ".ipynb": "Jupyter Notebook",
    ".qmd": "Quarto",
    ".md": "Markdown",
    ".dockerfile": "Dockerfile", ".tf": "Terraform",
    ".proto": "Protocol Buffers",
}

# Shebang patterns for extensionless scripts
SHEBANG_MAP: list[tuple[str, str]] = [
    ("python",     "Python"),
    ("node",       "JavaScript"),
    ("ruby",       "Ruby"),
    ("perl",       "Perl"),
    ("php",        "PHP"),
    ("bash",       "Shell / Bash"),
    ("sh",         "Shell / Bash"),
    ("zsh",        "Zsh"),
    ("fish",       "Fish Shell"),
    ("powershell", "PowerShell"),
    ("Rscript",    "R"),
    ("julia",      "Julia"),
    ("lua",        "Lua"),
]

# Fence language identifiers for Markdown code blocks
FENCE_LANG: dict[str, str] = {
    "Python": "python", "JavaScript": "javascript", "TypeScript": "typescript",
    "React (JSX)": "jsx", "React (TSX)": "tsx",
    "Java": "java", "Kotlin": "kotlin", "Scala": "scala", "Groovy": "groovy",
    "C": "c", "C++": "cpp", "C#": "csharp", "F#": "fsharp",
    "Go": "go", "Rust": "rust", "Ruby": "ruby", "PHP": "php",
    "Swift": "swift", "Dart": "dart", "Lua": "lua",
    "Shell / Bash": "bash", "Zsh": "bash", "Fish Shell": "fish",
    "PowerShell": "powershell",
    "R": "r", "R Markdown": "r", "Julia": "julia",
    "SQL": "sql", "HTML": "html", "CSS": "css",
    "SCSS": "scss", "Sass": "sass", "Less": "less",
    "YAML": "yaml", "JSON": "json", "TOML": "toml",
    "XML": "xml", "Markdown": "markdown",
    "Dockerfile": "dockerfile", "Terraform": "hcl",
    "Elixir": "elixir", "Erlang": "erlang",
    "Haskell": "haskell", "OCaml": "ocaml",
    "Clojure": "clojure", "Quarto": "markdown",
    "Jupyter Notebook": "python", "Protocol Buffers": "protobuf",
}

# Known unsupported formats — rejected with a helpful message
_UNSUPPORTED_FORMATS: dict[str, str] = {
    ".pdf":  "PDF documents",
    ".doc":  "Word documents",  ".docx": "Word documents",
    ".xls":  "Excel files",     ".xlsx": "Excel files",
    ".ppt":  "PowerPoint files",".pptx": "PowerPoint files",
    ".png":  "PNG images",  ".jpg": "JPEG images", ".jpeg": "JPEG images",
    ".gif":  "GIF images",  ".bmp": "BMP images",  ".ico":  "icon files",
    ".tiff": "TIFF images", ".webp": "WebP images",
    ".zip":  "ZIP archives", ".tar": "tar archives",
    ".gz":   "gzip archives", ".7z": "7-Zip archives", ".rar": "RAR archives",
    ".exe":  "Windows executables", ".dll": "DLL files",
    ".so":   "shared libraries",   ".dylib": "dynamic libraries",
    ".pyc":  "compiled Python bytecode",
    ".class":"compiled Java bytecode", ".jar": "Java archives",
    ".mp3":  "audio files", ".mp4": "video files", ".mov": "video files",
    ".txt":  "plain text files",
    ".csv":  "CSV data files", ".log": "log files",
}

# Section names expected in AI response (others demoted)
_KNOWN_SECTIONS: frozenset = frozenset({
    "what this file does",
    "how it works", "how it works — step by step",
    "key things to know",
    "dependencies", "dependencies & setup",
    "plain-english summary", "plain english summary",
    "original source code",
})

PROMPT_TEMPLATE: str = """\
You are an expert at explaining technical code to non-technical people.

Explain the {language} file named '{filename}' in plain English for someone
with no programming knowledge whatsoever. Use simple, everyday language.
Never use technical jargon without immediately explaining it in plain terms.

Format your response in Markdown using exactly these section headings, in order:

## What This File Does
2-4 sentences describing the overall purpose.

## How It Works — Step by Step
Numbered list walking through the logic. Each step should be one or two
plain-English sentences. Explain technical terms immediately.

## Key Things to Know
3-6 bullet points: what data it uses, what it produces, risks, assumptions,
external services it depends on.

## Dependencies & Setup
ONLY include this section if the file has explicit external dependencies
(imports, packages, environment variables, config files it needs that must be
installed separately). If it uses only the standard library, omit this section.
List each dependency in plain English — what it is and whether it needs
separate installation.

## Plain-English Summary
3-5 sentences written for executives in a meeting. No bullet points.
Plain conversational prose only.

---

Here is the code to explain:

```{language}
{code}
```
"""

NOTEBOOK_PROMPT_TEMPLATE: str = """\
You are an expert at explaining technical notebooks to non-technical people.

Explain the {language} notebook named '{filename}' in plain English for someone
with no programming knowledge whatsoever. A notebook is an interactive document
that mixes code, results, and explanatory text in sections called "cells."

Format your response in Markdown using exactly these section headings, in order:

## What This Notebook Does
2-4 sentences describing the overall purpose and what question it answers.

## How It Works — Step by Step
Walk through the notebook cell by cell (or logical group by group). Explain
what each section does in plain English. Label cells naturally (e.g.
"The first section imports tools…").

## Key Things to Know
3-6 bullet points covering inputs needed, outputs produced, assumptions made,
and any external services or data the notebook depends on.

## Dependencies & Setup
ONLY include if there are external packages to install. List each one plainly.

## Plain-English Summary
3-5 executive-friendly sentences summarising what the notebook does,
what it produces, and when you'd run it.

---

Here is the notebook content:

{code}
"""


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {"active_provider": "", "keys": {}}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"active_provider": "", "keys": {}}


def save_config(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _is_binary(data: bytes) -> bool:
    if b"\x00" in data:
        return True
    non_text = sum(1 for b in data[:4096] if b < 9 or (13 < b < 32))
    return non_text / max(len(data[:4096]), 1) > 0.30


def _is_minified(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    avg_len = sum(len(l) for l in lines) / len(lines)
    return avg_len > 300 or (len(lines) < 5 and len(text) > 2000)


def detect_language(path: Path, content: str | None = None) -> str:
    lang = EXTENSION_MAP.get(path.suffix.lower())
    if lang:
        return lang
    name_lower = path.name.lower()
    if name_lower == "dockerfile":          return "Dockerfile"
    if name_lower in ("makefile", "gnumakefile"): return "Makefile"
    if name_lower in ("rakefile", "gemfile"):     return "Ruby"
    if name_lower in ("jenkinsfile",):            return "Groovy / Jenkinsfile"
    if content:
        first = content.splitlines()[0] if content.splitlines() else ""
        if first.startswith("#!"):
            for token, lang_name in SHEBANG_MAP:
                if token in first:
                    return lang_name
    return "Unknown"


def _read_notebook(path: Path) -> tuple[str, str]:
    """Extract source code from a Jupyter notebook (.ipynb).

    Returns (extracted_source, kernel_language).
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    kernel_lang = (
        data.get("metadata", {})
            .get("kernelspec", {})
            .get("language", "python")
    )
    lang_name = {
        "python": "Python", "r": "R", "julia": "Julia",
        "scala": "Scala", "javascript": "JavaScript",
    }.get(kernel_lang.lower(), kernel_lang.capitalize())

    cells: list[str] = []
    for i, cell in enumerate(data.get("cells", []), 1):
        cell_type = cell.get("cell_type", "code")
        source    = "".join(cell.get("source", []))
        if not source.strip():
            continue
        if cell_type == "markdown":
            # Prefix each markdown line with #  so it reads clearly
            md_lines = [f"# {l}" if l.strip() else "#" for l in source.splitlines()]
            cells.append(f"# ── Cell {i}: Markdown ──\n" + "\n".join(md_lines))
        elif cell_type == "code":
            cells.append(f"# ── Cell {i}: Code ──\n{source}")
        elif cell_type == "raw":
            cells.append(f"# ── Cell {i}: Raw ──\n# {source}")

    return "\n\n".join(cells), lang_name


def _is_pluto_notebook(content: str) -> bool:
    """Detect Pluto.jl reactive notebooks (Julia)."""
    return "PlutoRunner" in content or "# ╔═╡" in content


def read_file(path: Path) -> str:
    """Read a source file with multi-encoding fallback.

    For .ipynb files, cells are extracted and returned as annotated source.

    Raises:
        FileNotFoundError: path does not exist.
        ValueError:        unsupported format, empty, or binary file.
    """
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    ext = path.suffix.lower()

    # Explicit rejection of known-unsupported formats
    if ext in _UNSUPPORTED_FORMATS and ext not in EXTENSION_MAP:
        kind = _UNSUPPORTED_FORMATS[ext]
        raise ValueError(
            f"'{path.name}' is a {kind} — "
            f"decode-for-humans only works with source code files.\n"
            f"Supported: .py .js .ts .go .rs .java .sql .r .ipynb .qmd and 40+ more."
        )

    # Jupyter notebooks are JSON — special extraction
    if ext == ".ipynb":
        try:
            source, _ = _read_notebook(path)
            return source
        except Exception as exc:
            raise ValueError(f"Could not parse notebook '{path.name}': {exc}") from exc

    raw = path.read_bytes()
    if not raw:
        raise ValueError(f"File is empty: {path}")

    if _is_binary(raw):
        raise ValueError(
            f"'{path.name}' appears to be a binary file. "
            "decode-for-humans only works with plain-text source files."
        )

    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            content = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        content = raw.decode("utf-8", errors="replace")

    content = content.strip()
    if not content:
        raise ValueError(f"File is empty after stripping: {path}")
    return content


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_prompt(filename: str, language: str, code: str) -> str:
    """Build the AI prompt, truncating code if it exceeds MAX_CODE_CHARS."""
    if len(code) > MAX_CODE_CHARS:
        lines       = code.splitlines()
        kept, total = [], 0
        for line in lines:
            if total + len(line) > MAX_CODE_CHARS:
                break
            kept.append(line)
            total += len(line) + 1
        omitted = len(lines) - len(kept)
        code = "\n".join(kept) + f"\n\n# … ({omitted} more lines truncated)"

    # Use notebook-aware prompt for .ipynb
    is_notebook = any(marker in code for marker in
                      ["# ── Cell ", "PlutoRunner", "# ╔═╡"])
    template = NOTEBOOK_PROMPT_TEMPLATE if is_notebook else PROMPT_TEMPLATE
    return template.format(language=language, filename=filename, code=code)


# ---------------------------------------------------------------------------
# Markdown builder  (primary output)
# ---------------------------------------------------------------------------

def _md_anchor(text: str) -> str:
    """GitHub-compatible heading anchor from text."""
    return re.sub(r"[^\w\s-]", "", text.lower()).strip().replace(" ", "-")


def _extract_md_headings(text: str) -> list[tuple[int, str]]:
    """Return (level, heading_text) for each ## heading in explanation."""
    result = []
    for line in text.splitlines():
        m = re.match(r"^(#{2,4})\s+(.+)$", line.strip())
        if m:
            result.append((len(m.group(1)), m.group(2).strip()))
    return result


def build_md(
    output_path: Path,
    filename: str,
    language: str,
    code: str,
    explanation: str,
    include_source: bool = True,
) -> None:
    """Write a Markdown explanation file.

    The AI response is already Markdown — we add a header, auto-generate
    a table of contents from the ## headings, append the source block,
    and write the footer.
    """
    if not explanation or not explanation.strip():
        raise ValueError("The AI returned an empty explanation. Please try again.")

    fence = FENCE_LANG.get(language, "")
    today = datetime.now().strftime("%Y-%m-%d")
    lines: list[str] = []

    # ── Document header ───────────────────────────────────────────────────
    lines += [
        "# Code Explanation Report",
        "",
        f"| | |",
        f"|---|---|",
        f"| **File** | `{filename}` |",
        f"| **Language** | {language} |",
        f"| **Generated** | {today} |",
        f"| **Tool** | decode-for-humans |",
        "",
        "---",
        "",
    ]

    # ── Auto-generated TOC ────────────────────────────────────────────────
    headings = _extract_md_headings(explanation)
    if include_source:
        headings.append((2, "Original Source Code"))

    if headings:
        lines.append("## Table of Contents")
        lines.append("")
        for level, text in headings:
            indent = "  " * (level - 2)
            lines.append(f"{indent}- [{text}](#{_md_anchor(text)})")
        lines.append("")
        lines.append("---")
        lines.append("")

    # ── AI explanation (already Markdown — pass through) ─────────────────
    exp_lines = explanation.strip().splitlines()
    cleaned = []
    prev_heading = False
    for line in exp_lines:
        is_rule    = line.strip() == "---"
        is_heading = bool(re.match(r"^#{1,6} ", line.strip()))
        # Drop --- that immediately follow a heading (AI-added noise; we add our own)
        if is_rule and prev_heading:
            prev_heading = False
            continue
        cleaned.append(line)
        prev_heading = is_heading
    lines.append("\n".join(cleaned))
    lines.append("")

    # ── Source code appendix ──────────────────────────────────────────────
    if include_source:
        src_lines = code.splitlines()
        shown     = src_lines[:SOURCE_LINE_LIMIT]
        omitted   = len(src_lines) - SOURCE_LINE_LIMIT

        # If the source itself contains triple backticks (notebooks, .qmd, .rmd,
        # .md files with code blocks) use ~~~ as the outer fence so the inner
        # ``` don't prematurely close the code block.
        has_backtick_fence = any(ln.strip().startswith("```") for ln in shown)
        outer_fence = "~~~" if has_backtick_fence else f"```{fence}"

        lines += [
            "---",
            "",
            "## Original Source Code",
            "",
            f"*The {language} source analysed to produce this report.*",
            "",
            outer_fence,
        ]
        lines.extend(shown)
        if omitted > 0:
            lines += [
                "",
                f"# … {omitted:,} more lines not shown "
                f"(showing first {SOURCE_LINE_LIMIT} of {len(src_lines):,})",
            ]
        lines += [outer_fence if has_backtick_fence else "```", ""]

    # ── Minified warning ──────────────────────────────────────────────────
    if _is_minified(code):
        lines.insert(
            lines.index("---") + 1 if "---" in lines else 0,
            "> ⚠️ **Note:** This file appears to be minified or machine-generated. "
            "The explanation may be less accurate than for hand-written source code.\n",
        )

    # ── Footer ────────────────────────────────────────────────────────────
    lines += [
        "---",
        "",
        "*Generated by [decode-for-humans](https://github.com/hihipy/decode-for-humans) "
        "· Licensed under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/)*",
    ]

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Plain-text builder  (secondary output)
# ---------------------------------------------------------------------------

def build_txt(
    output_path: Path,
    filename: str,
    language: str,
    code: str,
    explanation: str,
    include_source: bool = True,
) -> None:
    """Write a clean, editable plain-text version."""
    WIDTH  = 80
    DIV_H  = "─" * WIDTH
    DIV_L  = "·" * WIDTH

    def _strip_md(text: str) -> str:
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        text = re.sub(r"`([^`]+)`",     r"\1", text)
        text = re.sub(r"^#{1,6}\s+",   "",    text, flags=re.MULTILINE)
        return text

    def _wrap(text: str, indent: str = "  ") -> list[str]:
        return textwrap.wrap(
            text, WIDTH - len(indent),
            initial_indent=indent, subsequent_indent=indent
        ) or [""]

    out: list[str] = [
        DIV_H,
        "CODE EXPLANATION REPORT".center(WIDTH),
        DIV_H,
        f"  File     : {filename}",
        f"  Language : {language}",
        f"  Tool     : decode-for-humans",
        DIV_H, "",
    ]

    in_fence   = False
    fence_buf: list[str] = []

    def flush_fence():
        nonlocal fence_buf
        for ln in fence_buf:
            out.append("  " + ln)
        fence_buf = []

    for line in explanation.splitlines():
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_fence:
                flush_fence()
                in_fence = False
            else:
                in_fence = True
            continue

        if in_fence:
            fence_buf.append(line)
            continue

        # "---" horizontal rules from AI → proper divider in TXT
        if stripped == "---":
            out.append(DIV_L)
            continue

        if not stripped:
            out.append("")
            continue

        if re.match(r"^#{1,6}\s+", stripped):
            heading = re.sub(r"^#{1,6}\s+", "", stripped)
            heading = _strip_md(heading).upper()
            out += ["", DIV_L, heading, "─" * len(heading), ""]
            continue

        if re.match(r"^\d+[.)]\s", stripped):
            num, _, rest = stripped.partition(" ")
            num = num.rstrip(".)").strip()
            text = _strip_md(rest.strip())
            wrapped = _wrap(text, indent=" " * 6)
            wrapped[0] = f"  {num:>2}.  " + wrapped[0].lstrip()
            out += wrapped
            continue

        if stripped.startswith(("- ", "* ", "• ")):
            text = _strip_md(stripped[2:].strip())
            wrapped = _wrap(text, indent="       ")
            wrapped[0] = "  •    " + wrapped[0].lstrip()
            out += wrapped
            continue

        out += _wrap(_strip_md(stripped))

    if in_fence:
        flush_fence()

    if include_source:
        src_lines = code.splitlines()
        shown     = src_lines[:SOURCE_LINE_LIMIT]
        omitted   = len(src_lines) - SOURCE_LINE_LIMIT
        out += ["", DIV_H, "ORIGINAL SOURCE CODE".center(WIDTH), DIV_H,
                f"  {filename}  ({language})", ""]
        for ln in shown:
            out.append("  " + ln)
        if omitted > 0:
            out += ["", f"  … {omitted:,} more lines not shown",
                    f"  (showing first {SOURCE_LINE_LIMIT} of {len(src_lines):,})"]

    out += ["", DIV_L,
            "Generated by decode-for-humans  ·  CC BY-NC-SA 4.0".center(WIDTH),
            DIV_L]

    output_path.write_text("\n".join(out) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="decode-for-humans",
        description="Translate a source code file into plain-English Markdown.",
    )
    parser.add_argument("file", type=Path)
    parser.add_argument("--provider", choices=list(PROVIDERS.keys()), default=None)
    parser.add_argument("--no-source", action="store_true")
    parser.add_argument("--no-txt", action="store_true", help="Skip plain-text output")
    return parser.parse_args()


def _resolve_provider(name: str | None) -> tuple[str, str]:
    config = load_config()
    name   = name or config.get("active_provider", "")
    if not name:
        print("No active provider. Run the GUI and add an API key.")
        sys.exit(1)
    key = config.get("keys", {}).get(name, "")
    if not key:
        print(f"No API key for '{name}'.")
        sys.exit(1)
    return name, key


def main() -> None:
    args   = _parse_args()
    source = args.file

    try:
        code = read_file(source)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    language = detect_language(source, content=code)
    print(f"Language: {language}  ({len(code.splitlines())} lines)")

    if _is_minified(code):
        print("Warning: file looks minified — explanation may be limited.")

    provider_name, api_key = _resolve_provider(args.provider)
    print(f"Provider: {provider_name}")

    prompt = build_prompt(source.name, language, code)
    print("Sending to AI…")
    try:
        explanation = get_provider(provider_name, api_key).explain(prompt)
    except Exception as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    include = not args.no_source
    md_out  = source.with_name(source.stem + "_explanation.md")
    build_md(md_out, source.name, language, code, explanation, include)
    print(f"Markdown → {md_out}")

    if not args.no_txt:
        txt_out = source.with_name(source.stem + "_explanation.txt")
        build_txt(txt_out, source.name, language, code, explanation, include)
        print(f"Text     → {txt_out}")


if __name__ == "__main__":
    main()
