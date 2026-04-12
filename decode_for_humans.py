"""
decode_for_humans.py

Reads a source code file, sends it to an AI provider, and produces a
clean plain-English PDF explanation suitable for non-technical readers.

Usage:
    python decode_for_humans.py <file> [--provider NAME] [--no-source]

    <file>              Path to any supported source code file.
    --provider NAME     AI provider to use (Claude, ChatGPT, Gemini,
                        Mistral, Groq). Defaults to the saved active
                        provider in ~/.decode_for_humans/config.json.
    --no-source         Omit the raw source code appendix from the PDF.

Dependencies:
    pip install reportlab
    pip install anthropic   # for Claude
    pip install openai      # for ChatGPT
    pip install google-genai  # for Gemini
    pip install mistralai   # for Mistral
    pip install groq        # for Groq
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from providers import PROVIDERS, get_provider

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIG_DIR: Path = Path.home() / ".decode_for_humans"
CONFIG_FILE: Path = CONFIG_DIR / "config.json"

SOURCE_LINE_LIMIT: int = 120
MAX_PDF_TOKENS: int = 4096

PAGE_MARGIN: float = 1.0 * inch
CONTENT_WIDTH: float = 6.5 * inch

# PDF colour palette
COLOR_NAVY: colors.HexColor = colors.HexColor("#1B2A4A")
COLOR_WHITE: colors.Color = colors.white
COLOR_TEXT: colors.HexColor = colors.HexColor("#1E293B")
COLOR_MUTED: colors.HexColor = colors.HexColor("#64748B")
COLOR_BORDER: colors.HexColor = colors.HexColor("#CBD5E1")
COLOR_LIGHT: colors.HexColor = colors.HexColor("#F4F7FA")
COLOR_SUBTITLE: colors.HexColor = colors.HexColor("#B0C4D8")

# Supported file extensions mapped to human-readable language names.
EXTENSION_MAP: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".jsx": "React (JSX)",
    ".tsx": "React (TSX)",
    ".java": "Java",
    ".c": "C",
    ".cpp": "C++",
    ".cs": "C#",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".scala": "Scala",
    ".pl": "Perl",
    ".lua": "Lua",
    ".sh": "Shell / Bash",
    ".ps1": "PowerShell",
    ".r": "R",
    ".jl": "Julia",
    ".sql": "SQL",
    ".m": "MATLAB",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".json": "JSON",
    ".xml": "XML",
    ".ipynb": "Jupyter Notebook",
    ".qmd": "Quarto",
    ".rmd": "R Markdown",
}

# Prompt template — instructs the AI to return exactly four sections.
PROMPT_TEMPLATE: str = """\
You are an expert at explaining technical code to non-technical people.

Explain the {language} file named '{filename}' in plain English for someone
with no programming knowledge whatsoever. Use simple, everyday language.
Never use technical jargon without immediately explaining it in plain terms.

Structure your response using exactly these four section headings, in order:

## What This File Does
2-4 sentences describing the overall purpose. What problem does it solve?
What does it produce or enable?

## How It Works — Step by Step
A numbered list walking through the logic from top to bottom. Each step
should be a complete sentence or two. If a concept is technical, explain
it in plain terms immediately (e.g. "a loop — meaning it repeats the same
action multiple times until a condition is met").

## Key Things to Know
3-6 bullet points covering the most important facts a non-technical
stakeholder needs: what data it uses, what it produces, any risks,
assumptions, or external services it depends on.

## Plain-English Summary
3-5 sentences written as if you were explaining this to a group of
executives in a meeting. No bullet points. Plain conversational prose only.

---

Here is the code to explain:

```{language}
{code}
```
"""


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load the saved configuration from disk.

    Returns:
        A dict with keys 'active_provider' and 'keys'. Returns safe
        defaults if the config file does not exist yet.
    """
    if not CONFIG_FILE.exists():
        return {"active_provider": "", "keys": {}}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"active_provider": "", "keys": {}}


def save_config(config: dict) -> None:
    """Persist the configuration to disk.

    Args:
        config: The full config dict to write.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def detect_language(path: Path) -> str:
    """Return a human-readable language name for a given file path.

    Args:
        path: Path to the source code file.

    Returns:
        A language name string (e.g. "Python"), or "Unknown" if the
        extension is not in the supported map.
    """
    return EXTENSION_MAP.get(path.suffix.lower(), "Unknown")


def read_file(path: Path) -> str:
    """Read a source file and return its contents as a string.

    Args:
        path: Path to the source code file.

    Returns:
        The full file contents as a UTF-8 string.

    Raises:
        FileNotFoundError: If the path does not exist.
        ValueError: If the file is empty.
    """
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    content = path.read_text(encoding="utf-8", errors="replace").strip()

    if not content:
        raise ValueError(f"File is empty: {path}")

    return content


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_prompt(filename: str, language: str, code: str) -> str:
    """Assemble the full prompt string to send to the AI provider.

    Args:
        filename: The base name of the source file (e.g. "pipeline.py").
        language: Human-readable language name (e.g. "Python").
        code: The raw source code string.

    Returns:
        A formatted prompt string ready to pass to provider.explain().
    """
    return PROMPT_TEMPLATE.format(
        language=language,
        filename=filename,
        code=code,
    )


# ---------------------------------------------------------------------------
# PDF styles
# ---------------------------------------------------------------------------

def _build_pdf_styles() -> dict[str, ParagraphStyle]:
    """Build and return all ReportLab paragraph styles used in the PDF.

    Returns:
        A dict mapping style name strings to ParagraphStyle objects.
    """
    return {
        "doc_title": ParagraphStyle(
            "doc_title",
            fontName="Helvetica-Bold",
            fontSize=22,
            textColor=COLOR_WHITE,
            alignment=TA_CENTER,
            spaceAfter=4,
        ),
        "doc_subtitle": ParagraphStyle(
            "doc_subtitle",
            fontName="Helvetica",
            fontSize=11,
            textColor=COLOR_SUBTITLE,
            alignment=TA_CENTER,
        ),
        "section_heading": ParagraphStyle(
            "section_heading",
            fontName="Helvetica-Bold",
            fontSize=13,
            textColor=COLOR_NAVY,
            spaceBefore=16,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "body",
            fontName="Helvetica",
            fontSize=10.5,
            textColor=COLOR_TEXT,
            leading=16,
            spaceAfter=6,
            alignment=TA_LEFT,
        ),
        "bullet": ParagraphStyle(
            "bullet",
            fontName="Helvetica",
            fontSize=10.5,
            textColor=COLOR_TEXT,
            leading=16,
            leftIndent=18,
            spaceAfter=4,
        ),
        "numbered": ParagraphStyle(
            "numbered",
            fontName="Helvetica",
            fontSize=10.5,
            textColor=COLOR_TEXT,
            leading=16,
            leftIndent=22,
            spaceAfter=5,
        ),
        "code_block": ParagraphStyle(
            "code_block",
            fontName="Courier",
            fontSize=8,
            leading=12,
            textColor=colors.HexColor("#334155"),
            leftIndent=8,
            rightIndent=8,
        ),
        "footer": ParagraphStyle(
            "footer",
            fontName="Helvetica",
            fontSize=8,
            textColor=COLOR_MUTED,
            alignment=TA_CENTER,
        ),
    }


# ---------------------------------------------------------------------------
# PDF rendering helpers
# ---------------------------------------------------------------------------

def _render_header_banner(
    filename: str, language: str, styles: dict
) -> Table:
    """Build the navy header banner shown at the top of the PDF.

    Args:
        filename: The source file name displayed in the banner.
        language: The detected language displayed in the banner.
        styles: The styles dict from _build_pdf_styles().

    Returns:
        A ReportLab Table flowable styled as the header banner.
    """
    rows = [
        [Paragraph("Code Explanation Report", styles["doc_title"])],
        [Paragraph(f"{filename} &nbsp;·&nbsp; {language}", styles["doc_subtitle"])],
    ]
    table = Table(rows, colWidths=[CONTENT_WIDTH])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COLOR_NAVY),
        ("TOPPADDING", (0, 0), (-1, 0), 18),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 18),
        ("ROWPADDING", (0, 0), (-1, -1), 10),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return table


def _render_explanation(
    explanation: str, styles: dict
) -> list:
    """Convert the AI explanation text into a list of ReportLab flowables.

    Parses the fixed four-section markdown-style output and maps each
    element to an appropriate paragraph style.

    Args:
        explanation: The raw explanation string returned by the provider.
        styles: The styles dict from _build_pdf_styles().

    Returns:
        A list of ReportLab flowable objects ready to append to a story.
    """
    flowables = []

    for line in explanation.splitlines():
        stripped = line.strip()

        if not stripped:
            continue

        if stripped.startswith("## "):
            heading = stripped[3:].strip()
            flowables.append(
                HRFlowable(width="100%", thickness=1, color=COLOR_BORDER, spaceAfter=4)
            )
            flowables.append(Paragraph(heading, styles["section_heading"]))
            continue

        # Numbered list item: "1." or "1)"
        if re.match(r"^\d+[.)]\s", stripped):
            number, _, rest = stripped.partition(" ")
            number = number.rstrip(".")
            content = rest.strip()
            flowables.append(
                Paragraph(f"<b>{number}.</b>  {content}", styles["numbered"])
            )
            continue

        if stripped.startswith(("- ", "* ")):
            content = stripped[2:].strip()
            # Convert **bold** markers to ReportLab tags
            content = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", content)
            flowables.append(
                Paragraph(f"&bull; &nbsp; {content}", styles["bullet"])
            )
            continue

        # Regular body paragraph — convert bold markers
        body_text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", stripped)
        flowables.append(Paragraph(body_text, styles["body"]))

    return flowables


def _render_source_appendix(
    code: str, language: str, styles: dict
) -> list:
    """Build the source code appendix shown at the end of the PDF.

    Truncates very long files to SOURCE_LINE_LIMIT lines to keep
    the PDF a reasonable size.

    Args:
        code: The raw source code string.
        language: Human-readable language name for the section label.
        styles: The styles dict from _build_pdf_styles().

    Returns:
        A list of ReportLab flowable objects for the appendix section.
    """
    flowables: list = [
        Spacer(1, 12),
        HRFlowable(width="100%", thickness=1, color=COLOR_BORDER, spaceAfter=8),
        Paragraph("Original Source Code", styles["section_heading"]),
        Paragraph(
            f"The {language} source analysed to produce this report.",
            styles["body"],
        ),
    ]

    lines = code.splitlines()
    if len(lines) > SOURCE_LINE_LIMIT:
        truncated = "\n".join(lines[:SOURCE_LINE_LIMIT])
        truncated += f"\n\n... ({len(lines) - SOURCE_LINE_LIMIT} more lines not shown)"
    else:
        truncated = code

    code_para = Preformatted(truncated, styles["code_block"])
    box = Table([[code_para]], colWidths=[CONTENT_WIDTH])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COLOR_LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.75, COLOR_BORDER),
        ("ROWPADDING", (0, 0), (-1, -1), 8),
    ]))
    flowables.append(box)

    return flowables


# ---------------------------------------------------------------------------
# PDF builder
# ---------------------------------------------------------------------------

def build_pdf(
    output_path: Path,
    filename: str,
    language: str,
    code: str,
    explanation: str,
    include_source: bool = True,
) -> None:
    """Render the explanation and optional source code into a PDF file.

    Args:
        output_path: Destination path for the generated PDF.
        filename: Original source file name, shown in the header banner.
        language: Detected language, shown in the header banner.
        code: Raw source code (used in the appendix if include_source).
        explanation: Plain-English explanation returned by the AI provider.
        include_source: Whether to append the raw source code at the end.
    """
    styles = _build_pdf_styles()

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        leftMargin=PAGE_MARGIN,
        rightMargin=PAGE_MARGIN,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )

    story: list = []

    story.append(_render_header_banner(filename, language, styles))
    story.append(Spacer(1, 16))
    story.extend(_render_explanation(explanation, styles))

    if include_source:
        story.extend(_render_source_appendix(code, language, styles))

    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=0.5, color=COLOR_BORDER))
    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            "Generated by decode-for-humans &nbsp;·&nbsp; "
            "Licensed under CC BY-NC-SA 4.0",
            styles["footer"],
        )
    )

    doc.build(story)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    """Parse and return command-line arguments.

    Returns:
        A Namespace with attributes: file, provider, no_source.
    """
    parser = argparse.ArgumentParser(
        prog="decode-for-humans",
        description=(
            "Translate a source code file into a plain-English PDF "
            "explanation for non-technical readers."
        ),
    )
    parser.add_argument(
        "file",
        type=Path,
        help="Path to the source code file to decode.",
    )
    parser.add_argument(
        "--provider",
        choices=list(PROVIDERS.keys()),
        default=None,
        help=(
            "AI provider to use. Overrides the saved active provider. "
            f"Choices: {', '.join(PROVIDERS.keys())}."
        ),
    )
    parser.add_argument(
        "--no-source",
        action="store_true",
        help="Omit the raw source code appendix from the PDF.",
    )
    return parser.parse_args()


def _resolve_provider(provider_name: str | None) -> tuple[str, str]:
    """Look up the provider name and API key to use for this run.

    Checks (in order): the --provider CLI flag, then the saved active
    provider in config.json, then prompts the user if nothing is set.

    Args:
        provider_name: The --provider flag value, or None if not supplied.

    Returns:
        A (name, api_key) tuple.

    Raises:
        SystemExit: If no provider or key can be resolved.
    """
    config = load_config()
    name = provider_name or config.get("active_provider", "")

    if not name:
        print(
            "No active provider configured.\n"
            "Run the GUI and add an API key, or pass --provider NAME."
        )
        sys.exit(1)

    api_key = config.get("keys", {}).get(name, "")

    if not api_key:
        print(
            f"No API key found for '{name}'.\n"
            "Run the GUI and add your key in Settings."
        )
        sys.exit(1)

    return name, api_key


def main() -> None:
    """Entry point — orchestrates the full decode pipeline."""
    args = _parse_args()
    source_path = args.file

    # 1. Read the file
    print(f"Reading: {source_path}")
    try:
        code = read_file(source_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    language = detect_language(source_path)
    print(f"Detected language: {language}")
    print(f"Size: {len(code.splitlines())} lines")

    # 2. Resolve provider
    provider_name, api_key = _resolve_provider(args.provider)
    print(f"Provider: {provider_name}")

    # 3. Build prompt and call the AI
    prompt = build_prompt(source_path.name, language, code)
    print("Sending to AI provider...")
    try:
        provider = get_provider(provider_name, api_key)
        explanation = provider.explain(prompt)
    except Exception as exc:
        print(f"AI provider error: {exc}")
        sys.exit(1)

    print("Explanation received.")

    # 4. Generate the PDF
    output_path = source_path.with_name(source_path.stem + "_explanation.pdf")
    print(f"Building PDF: {output_path}")
    try:
        build_pdf(
            output_path=output_path,
            filename=source_path.name,
            language=language,
            code=code,
            explanation=explanation,
            include_source=not args.no_source,
        )
    except Exception as exc:
        print(f"PDF generation error: {exc}")
        sys.exit(1)

    print(f"\nDone. PDF saved to: {output_path}")


if __name__ == "__main__":
    main()
