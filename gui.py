"""
gui.py

Desktop GUI for decode-for-humans.

Launches a ttkbootstrap window with file selection, provider switching,
a settings screen for API key management, and a live console log panel.
The decode operation runs in a background thread so the UI stays responsive.

Dependencies:
    pip install ttkbootstrap

Usage:
    python gui.py
"""

from __future__ import annotations

import queue
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk

try:
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import END
except ImportError as exc:
    raise ImportError("Run: pip install ttkbootstrap") from exc

from decode_for_humans import (
    build_pdf,
    build_prompt,
    detect_language,
    load_config,
    read_file,
    save_config,
)
from providers import PROVIDERS, get_provider


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WINDOW_TITLE: str = "decode-for-humans"
WINDOW_WIDTH: int = 640
WINDOW_HEIGHT: int = 640

CONSOLE_BG: str = "#161616"
CONSOLE_HEADER_BG: str = "#1e1e1e"
CONSOLE_FOOTER_BG: str = "#1a1a1a"

# Log level colour map used as Text widget tags
LOG_COLORS: dict[str, str] = {
    "info":    "#6fa8c8",
    "success": "#7abf7a",
    "warn":    "#d4a84b",
    "error":   "#c97070",
    "muted":   "#555555",
    "dim":     "#666666",
    "time":    "#3a3a3a",
}

API_GUIDE_URLS: dict[str, str] = {
    "Claude":  "https://platform.anthropic.com/account/api-keys",
    "ChatGPT": "https://platform.openai.com/api-keys",
    "Gemini":  "https://aistudio.google.com/app/apikey",
    "Mistral": "https://console.mistral.ai/api-keys",
    "Groq":    "https://console.groq.com/keys",
}

# Sentinel placed on the log queue to signal the decode thread has finished
_DECODE_DONE: str = "__DECODE_DONE__"


# ---------------------------------------------------------------------------
# Add / Edit key dialog
# ---------------------------------------------------------------------------

class AddKeyDialog(ttk.Toplevel):
    """Modal dialog for adding or editing a single provider API key.

    Attributes:
        result: The (provider_name, api_key) tuple submitted by the user,
                or None if the dialog was cancelled.
    """

    def __init__(self, parent: tk.Widget, existing_name: str = "") -> None:
        """Initialise the dialog, optionally pre-selecting a provider.

        Args:
            parent: The parent widget (settings window).
            existing_name: If editing an existing entry, the provider name
                           to pre-select in the dropdown.
        """
        super().__init__(parent)
        self.title("Add API key")
        self.geometry("400x280")
        self.resizable(False, False)
        self.grab_set()

        self.result: tuple[str, str] | None = None
        self._show_key = False

        self._build(existing_name)

    def _build(self, existing_name: str) -> None:
        """Lay out all widgets inside the dialog.

        Args:
            existing_name: Pre-selected provider name, or empty string.
        """
        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)

        # Provider dropdown
        ttk.Label(frame, text="Provider", font=("", 10)).pack(anchor="w")
        self._provider_var = ttk.StringVar(
            value=existing_name or list(PROVIDERS.keys())[0]
        )
        provider_cb = ttk.Combobox(
            frame,
            textvariable=self._provider_var,
            values=list(PROVIDERS.keys()),
            state="readonly",
            width=30,
        )
        provider_cb.pack(fill="x", pady=(2, 12))
        provider_cb.bind("<<ComboboxSelected>>", lambda _: self._update_guide_link())

        # API key entry
        ttk.Label(frame, text="API key", font=("", 10)).pack(anchor="w")
        key_row = ttk.Frame(frame)
        key_row.pack(fill="x", pady=(2, 4))

        self._key_var = ttk.StringVar()
        self._key_entry = ttk.Entry(
            key_row, textvariable=self._key_var, show="•", width=32
        )
        self._key_entry.pack(side="left", fill="x", expand=True)

        self._show_btn = ttk.Button(
            key_row, text="Show", width=6, command=self._toggle_show
        )
        self._show_btn.pack(side="left", padx=(6, 0))

        # Guide link
        self._guide_link = ttk.Label(
            frame, text="", cursor="hand2",
            font=("", 9, "underline"), bootstyle="info",
        )
        self._guide_link.pack(anchor="w", pady=(0, 14))
        self._guide_link.bind("<Button-1>", lambda _: self._open_guide())
        self._update_guide_link()

        # Action buttons
        btn_row = ttk.Frame(frame)
        btn_row.pack(fill="x")
        ttk.Button(
            btn_row, text="Cancel", bootstyle="secondary-outline",
            command=self.destroy, width=12,
        ).pack(side="left")
        ttk.Button(
            btn_row, text="Save key", bootstyle="dark",
            command=self._save, width=12,
        ).pack(side="right")

    def _toggle_show(self) -> None:
        """Toggle the key entry between masked and visible."""
        self._show_key = not self._show_key
        self._key_entry.config(show="" if self._show_key else "•")
        self._show_btn.config(text="Hide" if self._show_key else "Show")

    def _update_guide_link(self) -> None:
        """Update the guide link text to match the selected provider."""
        name = self._provider_var.get()
        self._guide_link.config(text=f"↗  How to get a {name} API key")

    def _open_guide(self) -> None:
        """Open the API key guide URL in the default browser."""
        name = self._provider_var.get()
        url = API_GUIDE_URLS.get(name, "")
        if url:
            webbrowser.open(url)

    def _save(self) -> None:
        """Validate inputs and store the result, then close the dialog."""
        name = self._provider_var.get()
        key = self._key_var.get().strip()
        if not key:
            messagebox.showwarning("Missing key", "Please paste your API key.")
            return
        self.result = (name, key)
        self.destroy()


# ---------------------------------------------------------------------------
# Settings window
# ---------------------------------------------------------------------------

class SettingsWindow(ttk.Toplevel):
    """Settings screen for managing saved API keys and output preferences.

    Attributes:
        on_close_callback: Called when the window closes so the main
                           window can refresh its provider badge.
    """

    def __init__(
        self, parent: tk.Widget, on_close_callback: callable
    ) -> None:
        """Initialise the settings window.

        Args:
            parent: The main application window.
            on_close_callback: Zero-argument callable invoked on close.
        """
        super().__init__(parent)
        self.title("Settings")
        self.geometry("480x500")
        self.resizable(False, False)
        self.grab_set()

        self._on_close = on_close_callback
        self.protocol("WM_DELETE_WINDOW", self._close)

        self._build()
        self._refresh_key_list()

    def _build(self) -> None:
        """Lay out the static parts of the settings window."""
        outer = ttk.Frame(self, padding=(20, 16))
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer, text="SAVED API KEYS",
            font=("", 9, "bold"), bootstyle="secondary",
        ).pack(anchor="w", pady=(0, 6))

        # Key wallet frame — rows injected by _refresh_key_list
        self._wallet_frame = ttk.Frame(outer, bootstyle="light")
        self._wallet_frame.pack(fill="x", pady=(0, 10))

        ttk.Button(
            outer, text="+ Add another API key",
            bootstyle="secondary-outline", command=self._add_key,
        ).pack(fill="x", pady=(0, 20))

        ttk.Separator(outer).pack(fill="x", pady=(0, 14))

        ttk.Label(
            outer, text="OUTPUT",
            font=("", 9, "bold"), bootstyle="secondary",
        ).pack(anchor="w", pady=(0, 8))

        self._open_after_var = ttk.BooleanVar(value=True)
        ttk.Checkbutton(
            outer, text="Open PDF after generating",
            variable=self._open_after_var, bootstyle="round-toggle",
        ).pack(anchor="w", pady=4)

        self._save_next_to_var = ttk.BooleanVar(value=True)
        ttk.Checkbutton(
            outer, text="Save PDF next to source file",
            variable=self._save_next_to_var, bootstyle="round-toggle",
        ).pack(anchor="w", pady=4)

    def _refresh_key_list(self) -> None:
        """Rebuild the key wallet rows from the current config."""
        for widget in self._wallet_frame.winfo_children():
            widget.destroy()

        config = load_config()
        active = config.get("active_provider", "")
        keys: dict = config.get("keys", {})

        if not keys:
            ttk.Label(
                self._wallet_frame,
                text="No keys saved yet. Add one above.",
                font=("", 10), bootstyle="secondary",
            ).pack(pady=8)
            return

        for name, key in keys.items():
            is_active = name == active
            self._build_key_row(name, key, is_active)

    def _build_key_row(
        self, name: str, key: str, is_active: bool
    ) -> None:
        """Render a single row in the key wallet.

        Args:
            name: Provider display name.
            key: The stored API key (will be masked in the UI).
            is_active: Whether this provider is currently active.
        """
        row = ttk.Frame(
            self._wallet_frame,
            bootstyle="secondary" if is_active else "light",
            padding=(10, 7),
        )
        row.pack(fill="x", pady=1)

        ttk.Label(row, text=name, font=("", 11, "bold"), width=10).pack(
            side="left"
        )

        masked = key[:8] + "••••••••" + key[-4:] if len(key) > 12 else "••••••••"
        ttk.Label(
            row, text=masked, font=("Courier", 9), bootstyle="secondary"
        ).pack(side="left", padx=8)

        if is_active:
            ttk.Label(
                row, text="Active", font=("", 9, "bold"), bootstyle="success"
            ).pack(side="right", padx=4)
        else:
            ttk.Button(
                row, text="Set active", bootstyle="secondary-outline",
                width=9,
                command=lambda n=name: self._set_active(n),
            ).pack(side="right", padx=2)

        ttk.Button(
            row, text="Remove", bootstyle="danger-link",
            command=lambda n=name: self._remove_key(n),
        ).pack(side="right")

    def _add_key(self) -> None:
        """Open the add-key dialog and save the result if confirmed."""
        dialog = AddKeyDialog(self)
        self.wait_window(dialog)
        if dialog.result:
            name, key = dialog.result
            config = load_config()
            config.setdefault("keys", {})[name] = key
            if not config.get("active_provider"):
                config["active_provider"] = name
            save_config(config)
            self._refresh_key_list()

    def _set_active(self, name: str) -> None:
        """Mark a provider as the active one.

        Args:
            name: The provider display name to activate.
        """
        config = load_config()
        config["active_provider"] = name
        save_config(config)
        self._refresh_key_list()

    def _remove_key(self, name: str) -> None:
        """Remove a saved key after confirmation.

        Args:
            name: The provider display name whose key to remove.
        """
        if not messagebox.askyesno(
            "Remove key",
            f"Remove the saved key for {name}?",
        ):
            return
        config = load_config()
        config.get("keys", {}).pop(name, None)
        if config.get("active_provider") == name:
            remaining = list(config.get("keys", {}).keys())
            config["active_provider"] = remaining[0] if remaining else ""
        save_config(config)
        self._refresh_key_list()

    def _close(self) -> None:
        """Invoke the close callback and destroy the window."""
        self._on_close()
        self.destroy()


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------

class DecodeForHumansApp(ttk.Window):
    """Main application window.

    Attributes:
        _source_path: Path to the currently loaded source file, or None.
        _log_queue: Thread-safe queue for console log messages.
        _line_count: Number of lines written to the console.
        _running: True while a decode operation is in progress.
    """

    def __init__(self) -> None:
        """Initialise the main window and all child widgets."""
        super().__init__(themename="litera")
        self.title(WINDOW_TITLE)
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.resizable(False, False)

        self._source_path: Path | None = None
        self._log_queue: queue.Queue = queue.Queue()
        self._line_count: int = 0
        self._running: bool = False

        self._include_source_var = ttk.BooleanVar(value=True)
        self._ai_enrichment_var = ttk.BooleanVar(value=True)

        self._build_header()
        self._build_body()
        self._build_console()

        self._poll_log_queue()
        self._log("decode-for-humans ready", "muted")
        self._log("Drop a file to begin", "dim")

    # ------------------------------------------------------------------ #
    # Layout builders                                                      #
    # ------------------------------------------------------------------ #

    def _build_header(self) -> None:
        """Build the top titlebar with app name and provider badge."""
        header = ttk.Frame(self, bootstyle="light", padding=(14, 8))
        header.pack(fill="x")

        ttk.Label(
            header, text="decode-for-humans",
            font=("", 13, "bold"),
        ).pack(side="left")

        self._settings_btn = ttk.Button(
            header, text="⚙", bootstyle="secondary-link",
            command=self._open_settings, width=3,
        )
        self._settings_btn.pack(side="right")

        self._provider_badge = ttk.Button(
            header, bootstyle="secondary-outline",
            command=self._open_settings,
        )
        self._provider_badge.pack(side="right", padx=6)
        self._refresh_provider_badge()

    def _build_body(self) -> None:
        """Build the main body: tabs, file area, options, and decode button."""
        body = ttk.Frame(self, padding=(20, 16, 20, 8))
        body.pack(fill="x")

        # Tabs (visual only for now — batch mode is a future feature)
        tab_row = ttk.Frame(body)
        tab_row.pack(fill="x", pady=(0, 16))
        ttk.Button(
            tab_row, text="Decode file", bootstyle="secondary",
        ).pack(side="left")
        ttk.Button(
            tab_row, text="Batch folder", bootstyle="secondary-outline",
        ).pack(side="left", padx=4)

        # Drop zone
        self._drop_frame = ttk.Frame(body)
        self._drop_frame.pack(fill="x", pady=(0, 12))
        self._build_drop_zone()

        # Options row
        opt_row = ttk.Frame(body)
        opt_row.pack(fill="x", pady=(0, 14))

        ttk.Checkbutton(
            opt_row, text="Include source appendix",
            variable=self._include_source_var,
            bootstyle="round-toggle",
        ).pack(side="left")

        ttk.Checkbutton(
            opt_row, text="AI enrichment",
            variable=self._ai_enrichment_var,
            bootstyle="round-toggle",
        ).pack(side="right")

        # Decode button
        self._decode_btn = ttk.Button(
            body, text="Decode →", bootstyle="dark",
            command=self._start_decode, state="disabled",
        )
        self._decode_btn.pack(fill="x", ipady=6)

    def _build_drop_zone(self) -> None:
        """Render the file drop zone (click to browse)."""
        for widget in self._drop_frame.winfo_children():
            widget.destroy()

        zone = ttk.Frame(
            self._drop_frame,
            bootstyle="light", padding=(0, 28),
            cursor="hand2",
        )
        zone.pack(fill="x")
        zone.bind("<Button-1>", lambda _: self._browse_file())

        ttk.Label(
            zone, text="↑", font=("", 20), bootstyle="secondary",
        ).pack()
        ttk.Label(
            zone, text="Drop your file here",
            font=("", 12, "bold"),
        ).pack()
        ttk.Label(
            zone,
            text="or click to browse  ·  "
                 ".py  .js  .sql  .r  .java  .ipynb  .qmd + more",
            font=("", 9), bootstyle="secondary",
        ).pack(pady=(4, 0))

    def _build_file_loaded_row(self) -> None:
        """Render the compact row shown when a file has been loaded."""
        for widget in self._drop_frame.winfo_children():
            widget.destroy()

        path = self._source_path
        language = detect_language(path)
        try:
            lines = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
            size_kb = round(path.stat().st_size / 1024, 1)
        except OSError:
            lines, size_kb = 0, 0.0

        row = ttk.Frame(self._drop_frame, bootstyle="light", padding=(12, 10))
        row.pack(fill="x")

        ttk.Label(
            row, text=language[:2].upper(),
            font=("", 11, "bold"), bootstyle="info",
            width=4,
        ).pack(side="left")

        info = ttk.Frame(row)
        info.pack(side="left", fill="x", expand=True)
        ttk.Label(
            info, text=path.name, font=("", 11, "bold")
        ).pack(anchor="w")
        ttk.Label(
            info,
            text=f"{language}  ·  {size_kb} KB  ·  {lines} lines",
            font=("", 9), bootstyle="secondary",
        ).pack(anchor="w")

        ttk.Button(
            row, text="×", bootstyle="secondary-link",
            command=self._clear_file, width=3,
        ).pack(side="right")

    def _build_console(self) -> None:
        """Build the dark console panel fixed at the bottom of the window."""
        outer = tk.Frame(self, bg=CONSOLE_HEADER_BG)
        outer.pack(fill="x", side="bottom")

        # Console header
        header = tk.Frame(outer, bg=CONSOLE_HEADER_BG)
        header.pack(fill="x")

        self._console_dot = tk.Label(
            header, text="●", fg="#444444", bg=CONSOLE_HEADER_BG,
            font=("", 8),
        )
        self._console_dot.pack(side="left", padx=(10, 4), pady=6)

        tk.Label(
            header, text="CONSOLE",
            fg="#666666", bg=CONSOLE_HEADER_BG,
            font=("Courier", 9, "bold"),
        ).pack(side="left")

        self._line_count_label = tk.Label(
            header, text="0 lines",
            fg="#444444", bg=CONSOLE_HEADER_BG,
            font=("Courier", 9),
        )
        self._line_count_label.pack(side="right", padx=10)

        # Separator
        tk.Frame(outer, bg="#252525", height=1).pack(fill="x")

        # Log text area
        self._console_text = tk.Text(
            outer,
            bg=CONSOLE_BG, fg="#666666",
            font=("Courier", 10),
            height=7,
            state="disabled",
            wrap="word",
            relief="flat",
            bd=0,
            padx=12, pady=8,
            cursor="arrow",
        )
        self._console_text.pack(fill="x")

        # Configure colour tags for each log level
        for level, colour in LOG_COLORS.items():
            self._console_text.tag_config(level, foreground=colour)

        # Progress bar
        self._progress = ttk.Progressbar(
            outer, bootstyle="success-striped", mode="indeterminate",
        )

        # Console footer
        footer = tk.Frame(outer, bg=CONSOLE_FOOTER_BG)
        footer.pack(fill="x")
        tk.Frame(outer, bg="#202020", height=1).pack(fill="x")

        tk.Button(
            footer, text="clear", fg="#555555", bg=CONSOLE_FOOTER_BG,
            font=("Courier", 9), relief="flat", bd=0,
            activebackground=CONSOLE_FOOTER_BG, activeforeground="#888888",
            command=self._clear_console,
        ).pack(side="left", padx=(10, 0), pady=4)

        tk.Button(
            footer, text="copy logs", fg="#555555", bg=CONSOLE_FOOTER_BG,
            font=("Courier", 9), relief="flat", bd=0,
            activebackground=CONSOLE_FOOTER_BG, activeforeground="#888888",
            command=self._copy_logs,
        ).pack(side="left", padx=8)

        self._status_label = tk.Label(
            footer, text="", fg="#3d3d3d", bg=CONSOLE_FOOTER_BG,
            font=("Courier", 9),
        )
        self._status_label.pack(side="right", padx=10)

    # ------------------------------------------------------------------ #
    # Console helpers                                                      #
    # ------------------------------------------------------------------ #

    def _log(self, message: str, level: str = "info") -> None:
        """Queue a log message for display in the console panel.

        Thread-safe — may be called from the decode background thread.

        Args:
            message: The text to display.
            level: One of info, success, warn, error, muted, dim.
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._log_queue.put((timestamp, message, level))

    def _poll_log_queue(self) -> None:
        """Drain the log queue and append messages to the console widget.

        Scheduled repeatedly via after() so it runs on the main thread.
        """
        try:
            while True:
                item = self._log_queue.get_nowait()
                if item[0] == _DECODE_DONE:
                    self._on_decode_finished()
                    continue
                timestamp, message, level = item
                self._append_console_line(timestamp, message, level)
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _append_console_line(
        self, timestamp: str, message: str, level: str
    ) -> None:
        """Write one formatted line to the console Text widget.

        Args:
            timestamp: HH:MM:SS string for the left column.
            message: The log message text.
            level: Log level tag name for colouring.
        """
        self._line_count += 1
        self._console_text.config(state="normal")
        self._console_text.insert(END, timestamp, "time")
        self._console_text.insert(END, f"  {message}\n", level)
        self._console_text.see(END)
        self._console_text.config(state="disabled")
        self._line_count_label.config(
            text=f"{self._line_count} line{'s' if self._line_count != 1 else ''}"
        )

    def _clear_console(self) -> None:
        """Clear all text from the console panel."""
        self._console_text.config(state="normal")
        self._console_text.delete("1.0", END)
        self._console_text.config(state="disabled")
        self._line_count = 0
        self._line_count_label.config(text="0 lines")
        self._status_label.config(text="")

    def _copy_logs(self) -> None:
        """Copy all console text to the system clipboard."""
        text = self._console_text.get("1.0", END).strip()
        self.clipboard_clear()
        self.clipboard_append(text)
        self._status_label.config(text="copied!")
        self.after(1500, lambda: self._status_label.config(text=""))

    # ------------------------------------------------------------------ #
    # File management                                                      #
    # ------------------------------------------------------------------ #

    def _browse_file(self) -> None:
        """Open a file picker dialog and load the selected file."""
        path_str = filedialog.askopenfilename(
            title="Select a source code file",
            filetypes=[
                ("All supported files",
                 "*.py *.js *.ts *.jsx *.tsx *.java *.c *.cpp *.cs "
                 "*.go *.rs *.rb *.php *.swift *.kt *.r *.jl *.sql "
                 "*.sh *.html *.css *.yaml *.yml *.json *.ipynb *.qmd *.rmd"),
                ("All files", "*.*"),
            ],
        )
        if not path_str:
            return
        self._load_file(Path(path_str))

    def _load_file(self, path: Path) -> None:
        """Store the file path and update the UI to the loaded state.

        Args:
            path: Path to the source file to load.
        """
        self._source_path = path
        self._build_file_loaded_row()
        self._decode_btn.config(state="normal")
        self._clear_console()
        lang = detect_language(path)
        self._log(f"Loaded → {path.name}", "success")
        self._log(f"Detected: {lang}", "info")
        self._log("Ready — press Decode to continue", "muted")
        self._console_dot.config(fg="#7abf7a")

    def _clear_file(self) -> None:
        """Remove the loaded file and return to the drop zone state."""
        self._source_path = None
        self._build_drop_zone()
        self._decode_btn.config(state="disabled")
        self._clear_console()
        self._log("File cleared", "muted")
        self._console_dot.config(fg="#444444")

    # ------------------------------------------------------------------ #
    # Settings                                                             #
    # ------------------------------------------------------------------ #

    def _open_settings(self) -> None:
        """Open the settings window."""
        SettingsWindow(self, on_close_callback=self._refresh_provider_badge)

    def _refresh_provider_badge(self) -> None:
        """Update the provider badge label from the current config."""
        config = load_config()
        name = config.get("active_provider", "")
        label = f"● {name}" if name else "No provider set"
        self._provider_badge.config(text=label)

    # ------------------------------------------------------------------ #
    # Decode pipeline                                                      #
    # ------------------------------------------------------------------ #

    def _start_decode(self) -> None:
        """Validate state and launch the decode thread."""
        if self._running or not self._source_path:
            return

        config = load_config()
        provider_name = config.get("active_provider", "")
        api_key = config.get("keys", {}).get(provider_name, "")

        if not provider_name or not api_key:
            messagebox.showwarning(
                "No API key",
                "No provider key is set.\n\n"
                "Open Settings (⚙) to add one.",
            )
            return

        self._running = True
        self._decode_btn.config(state="disabled", text="Decoding...")
        self._progress.pack(fill="x")
        self._progress.start(12)
        self._console_dot.config(fg="#d4a84b")
        self._clear_console()

        thread = threading.Thread(
            target=self._decode_worker,
            args=(
                self._source_path,
                provider_name,
                api_key,
                self._include_source_var.get(),
            ),
            daemon=True,
        )
        thread.start()

    def _decode_worker(
        self,
        source_path: Path,
        provider_name: str,
        api_key: str,
        include_source: bool,
    ) -> None:
        """Run the full decode pipeline in a background thread.

        Posts log messages via the queue and signals completion with the
        _DECODE_DONE sentinel so the main thread can update the UI safely.

        Args:
            source_path: Path to the source file to decode.
            provider_name: Display name of the active provider.
            api_key: API key for the provider.
            include_source: Whether to append raw source to the PDF.
        """
        output_path = source_path.with_name(source_path.stem + "_explanation.pdf")

        try:
            self._log("Reading file...", "info")
            code = read_file(source_path)
            language = detect_language(source_path)
            lines = len(code.splitlines())
            self._log(f"Detected: {language}  ·  {lines} lines", "success")

            self._log("Building prompt...", "info")
            prompt = build_prompt(source_path.name, language, code)
            self._log(f"Prompt ready  ·  {len(prompt)} chars", "dim")

            self._log(f"Connecting to {provider_name}...", "info")
            provider = get_provider(provider_name, api_key)

            self._log("Sending to AI — waiting for response...", "info")
            explanation = provider.explain(prompt)
            token_estimate = len(explanation.split())
            self._log(
                f"Response received  ·  ~{token_estimate} words", "success"
            )

            self._log("Building PDF...", "info")
            build_pdf(
                output_path=output_path,
                filename=source_path.name,
                language=language,
                code=code,
                explanation=explanation,
                include_source=include_source,
            )
            self._log(f"PDF saved → {output_path.name}", "success")
            self._log("Done!", "success")

        except FileNotFoundError as exc:
            self._log(f"File error: {exc}", "error")
        except ValueError as exc:
            self._log(f"Value error: {exc}", "error")
        except Exception as exc:
            self._log(f"Error: {exc}", "error")
        finally:
            self._log_queue.put((_DECODE_DONE, "", ""))

    def _on_decode_finished(self) -> None:
        """Re-enable the UI after the decode thread has completed."""
        self._running = False
        self._progress.stop()
        self._progress.pack_forget()
        self._decode_btn.config(state="normal", text="Decode →")
        self._console_dot.config(fg="#7abf7a")
        self._status_label.config(text="last run complete")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Launch the decode-for-humans GUI."""
    app = DecodeForHumansApp()
    app.mainloop()


if __name__ == "__main__":
    main()
