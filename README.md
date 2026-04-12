# decode-for-humans

**Turn code into plain English — for the people who don't speak it.**

`decode-for-humans` reads source code files and explains what they do in
clear, jargon-free language that anyone can understand — no programming
knowledge required. It produces a clean **Markdown** and **plain-text**
explanation you can open, share, or paste straight into a document.

---

## What it does

Most code is written by technical people and read only by other technical
people. But code makes decisions, handles data, and affects real workflows —
and the people responsible for those things often can't read it.

`decode-for-humans` bridges that gap. Drop in a source file, pick an AI
provider, and get back a structured explanation:

- **What This File Does** — plain-language summary of the purpose
- **How It Works — Step by Step** — numbered walkthrough of the logic
- **Key Things to Know** — inputs, outputs, risks, assumptions
- **Dependencies & Setup** — what needs to be installed (only when relevant)
- **Plain-English Summary** — executive-ready paragraph
- **Original Source Code** — the full source appended for reference

---

## Features

- **55 languages supported** — Python, JavaScript, TypeScript, Go, Rust, Java,
  Kotlin, C, C++, C#, R, Julia, SQL, Ruby, PHP, Swift, Scala, Dart, Elixir,
  Haskell, OCaml, Clojure, Lua, Perl, Shell/Bash, PowerShell, and more
- **Notebook support** — Jupyter (`.ipynb`), Quarto (`.qmd`),
  R Markdown (`.rmd`), with cell-by-cell walkthrough
- **Markdown + plain-text output** — renders in GitHub, Obsidian, VS Code,
  Notion, or any text editor; paste straight into Word or Google Docs
- **Batch mode** — drop a whole folder, review token estimates, decode everything
- **Five AI providers** — Claude, ChatGPT, Gemini, Mistral, Groq
- **Simple desktop GUI** — no terminal required; drag-and-drop files
- **Auto-generated table of contents** with anchor links
- **Token estimate preview** before any API call is made

---

## Outputs

For each file decoded you get two files in your Downloads folder:

| File | Format | Best for |
|---|---|---|
| `filename_explanation.md` | Markdown | GitHub, Obsidian, VS Code, Notion |
| `filename_explanation.txt` | Plain text | Word, Google Docs, email, printing |

---

## Getting started

```bash
# Install dependencies
pip install customtkinter anthropic pillow

# Also install whichever AI provider you want to use:
pip install openai          # ChatGPT
pip install google-genai    # Gemini
pip install mistralai       # Mistral
pip install groq            # Groq

# Launch the GUI
python gui.py
```

Then:

1. Open **Settings** (⚙) → **Connect a provider** → paste your API key
2. Drag a source file onto the drop zone (or click to browse)
3. Click **Decode →**

Your `_explanation.md` and `_explanation.txt` files appear in Downloads.

---

## Batch decoding

Click **Batch folder** to process an entire directory at once:

1. Browse to a folder — all supported files are detected automatically
2. Review the file list with per-file token estimates and estimated time
3. Confirm and walk away — results log to the console as files complete

---

## Supported languages

| Category | Languages |
|---|---|
| **Notebooks** | Jupyter, Quarto, R Markdown, Markdown |
| **Data Science** | Python, R, Julia, SQL |
| **JavaScript / TypeScript** | JS, TS, JSX, TSX |
| **JVM & .NET** | Java, Kotlin, Scala, Groovy, C#, F#, Visual Basic |
| **Systems** | C, C++, Go, Rust, Swift |
| **Scripting** | Ruby, PHP, Lua, Perl, Dart, Elixir, Erlang |
| **Shell & Infra** | Bash, Zsh, Fish, PowerShell, Dockerfile, Terraform |
| **Web** | HTML, CSS, SCSS, Sass, Less |
| **Config & Data** | JSON, YAML, TOML, XML, INI |
| **Functional** | Haskell, OCaml, Clojure, ClojureScript |
| **Other** | Protocol Buffers, SVG, MATLAB |

---

## AI providers

| Provider | Free tier | Key format |
|---|---|---|
| **Claude** (Anthropic) | — | `sk-ant-api03-…` |
| **ChatGPT** (OpenAI) | — | `sk-proj-…` |
| **Gemini** (Google) | ✓ | `AIza…` |
| **Groq** | ✓ generous | `gsk_…` |
| **Mistral** | — | long random string |

API keys are stored locally in `~/.decode_for_humans/config.json`.
Nothing is sent anywhere except the AI provider you choose.

---

## Requirements

- Python 3.10+
- `customtkinter` — GUI framework
- `pillow` — provider brand icons
- One or more AI provider packages (see Getting started above)

No LaTeX, no external binaries, no database. Pure Python.

---

## Project structure

```
decode_for_humans/
├── gui.py                  # Desktop GUI (CustomTkinter)
├── decode_for_humans.py    # Core pipeline — file reading, prompts, MD/TXT output
├── providers/
│   ├── __init__.py         # Provider registry
│   ├── base.py             # BaseProvider abstract class
│   ├── anthropic.py        # Claude
│   ├── openai.py           # ChatGPT
│   ├── google.py           # Gemini
│   ├── mistral.py          # Mistral
│   └── groq.py             # Groq
└── test_files/             # Sample files in 13 languages for testing
```

---


## License

Licensed under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/).

You are free to use, share, and adapt this work — including for your job —
under these terms:

- **Attribution** — Credit the original author
- **NonCommercial** — Not for selling or building commercial products
- **ShareAlike** — Derivatives must use the same license
