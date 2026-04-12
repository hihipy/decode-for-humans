"""
gui.py

Desktop GUI for decode-for-humans — built with CustomTkinter.

CustomTkinter replaces ttkbootstrap/tkinter because it has genuine
OS-level dark/light mode support on macOS, Windows, and Linux.
No more colour fights.

Dependencies:
    pip install customtkinter

Usage:
    python gui.py
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk

try:
	import customtkinter as ctk
except ImportError as exc:
	raise ImportError("Run: pip install customtkinter") from exc

from PIL import Image, ImageDraw, ImageFont

from decode_for_humans import (
	build_md, build_txt, build_prompt, detect_language,
	load_config, read_file, save_config, EXTENSION_MAP,
	MAX_CODE_CHARS, _UNSUPPORTED_FORMATS,
)
from providers import PROVIDERS, get_provider

# ---------------------------------------------------------------------------
# Appearance — follows OS automatically
# ---------------------------------------------------------------------------

ctk.set_appearance_mode("system")  # "system" | "dark" | "light"
ctk.set_default_color_theme("blue")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WINDOW_TITLE = "decode-for-humans"
WINDOW_W = 660
WINDOW_H = 880
_DECODE_DONE = "__DECODE_DONE__"

# Log colours — these ARE canvas-drawn so stay correct
LOG_COLORS = {
	"info": "#4fc1ff",
	"success": "#4ec94e",
	"warn": "#d4974a",
	"error": "#f14c4c",
	"muted": "#c0c0c0",
	"dim": "#8c8c8c",
	"time": "#5a5a5a",
}

# ---------------------------------------------------------------------------
# Brand colours from simpleicons.org + PIL icon generator
# ---------------------------------------------------------------------------

# Official Simple Icons hex colours for each provider
PROVIDER_BRANDS: dict[str, dict] = {
	"Claude": {"bg": "#D4C5A9", "fg": "#1a1a1a", "letter": "A"},  # Anthropic
	"ChatGPT": {"bg": "#412991", "fg": "#ffffff", "letter": "O"},  # OpenAI
	"Gemini": {"bg": "#8E75B2", "fg": "#ffffff", "letter": "G"},  # Google Gemini
	"Mistral": {"bg": "#FF7000", "fg": "#ffffff", "letter": "M"},  # Mistral AI
	"Groq": {"bg": "#F55036", "fg": "#ffffff", "letter": "G"},  # Groq
}

_icon_cache: dict[tuple, ctk.CTkImage] = {}


def make_provider_icon(name: str, size: int = 32) -> ctk.CTkImage:
	"""Generate a circular brand-coloured PIL badge for a provider.

	Uses official Simple Icons colours. Result is cached so the same
	image object is reused across the lifetime of the process.
	"""
	key = (name, size)
	if key in _icon_cache:
		return _icon_cache[key]

	brand = PROVIDER_BRANDS.get(name, {"bg": "#888888", "fg": "#ffffff", "letter": name[0]})
	bg_hex = brand["bg"]
	fg_hex = brand["fg"]
	letter = brand["letter"]

	# Parse hex colours
	def hex_to_rgb(h):
		h = h.lstrip("#")
		return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

	bg_rgb = hex_to_rgb(bg_hex)
	fg_rgb = hex_to_rgb(fg_hex)

	# Draw circle with letter on transparent background
	img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
	draw = ImageDraw.Draw(img)
	draw.ellipse([0, 0, size - 1, size - 1], fill=bg_rgb + (255,))

	# Try to use a bold font; fall back to default
	font_size = int(size * 0.44)
	font = None
	for font_path in [
		"/System/Library/Fonts/Helvetica.ttc",
		"/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
		"C:/Windows/Fonts/arialbd.ttf",
	]:
		try:
			font = ImageFont.truetype(font_path, font_size)
			break
		except (IOError, OSError):
			pass
	if font is None:
		font = ImageFont.load_default()

	# Centre the letter
	bbox = draw.textbbox((0, 0), letter, font=font)
	tw = bbox[2] - bbox[0]
	th = bbox[3] - bbox[1]
	tx = (size - tw) // 2 - bbox[0]
	ty = (size - th) // 2 - bbox[1]
	draw.text((tx, ty), letter, fill=fg_rgb + (255,), font=font)

	ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))
	_icon_cache[key] = ctk_img
	return ctk_img


# Per-provider setup guides
PROVIDER_GUIDES: dict[str, dict] = {
	"Claude": {
		"tagline": "Made by Anthropic — best for detailed explanations",
		"free": False,
		"url": "https://console.anthropic.com/account/keys",
		"hint": "Starts with  sk-ant-api03-…",
		"steps": [
			'Click "API Keys" in the left sidebar',
			'Click "Create Key", give it any name',
			"Copy the key — it starts with  sk-ant-api03-…",
			"Paste it in the box below and click Save",
		],
	},
	"ChatGPT": {
		"tagline": "Made by OpenAI — widely used, very capable",
		"free": False,
		"url": "https://platform.openai.com/api-keys",
		"hint": "Starts with  sk-proj-…  or  sk-…",
		"steps": [
			'Click "API Keys" in the left sidebar',
			'Click "Create new secret key", give it any name',
			"Copy the key immediately — OpenAI only shows it once",
			"Paste it in the box below and click Save",
		],
	},
	"Gemini": {
		"tagline": "Made by Google — free tier available",
		"free": True,
		"url": "https://aistudio.google.com/app/apikey",
		"hint": "Starts with  AIza…",
		"steps": [
			'Click "Get API Key" in the top-left menu',
			'Click "Create API key in new project"',
			"Copy the key — it starts with  AIza…",
			"Paste it in the box below and click Save",
		],
	},
	"Mistral": {
		"tagline": "Made by Mistral AI — efficient European model",
		"free": False,
		"url": "https://console.mistral.ai/api-keys",
		"hint": "A long random string of letters and numbers",
		"steps": [
			'Click "API Keys" in the left sidebar',
			'Click "Create new key", give it any name',
			"Copy the key — it's a long random string",
			"Paste it in the box below and click Save",
		],
	},
	"Groq": {
		"tagline": "Extremely fast — generous free tier",
		"free": True,
		"url": "https://console.groq.com/keys",
		"hint": "Starts with  gsk_…",
		"steps": [
			'Click "API Keys" in the left sidebar',
			'Click "Create API Key", give it any name',
			"Copy the key — it starts with  gsk_…",
			"Paste it in the box below and click Save",
		],
	},
}


# ---------------------------------------------------------------------------
# Add Key wizard
# ---------------------------------------------------------------------------


def _friendly_api_error(exc: Exception) -> str:
	"""Convert a raw API exception into a plain-English message."""
	msg = str(exc)
	low = msg.lower()
	if "credit balance is too low" in low or "insufficient_quota" in low:
		return "✗  No credits left. Add credits at the provider's billing page."
	if "invalid api key" in low or "incorrect api key" in low or "api key not found" in low:
		return "✗  Invalid key — double-check you copied the whole key."
	if "rate limit" in low:
		return "✗  Rate limit hit — wait a moment then try again."
	if "401" in msg:
		return "✗  Authentication failed — key may be wrong or expired."
	if "403" in msg:
		return "✗  Access denied — check your account has API access enabled."
	if "timeout" in low or "timed out" in low:
		return "✗  Connection timed out — check your internet and try again."
	if len(msg) > 120:
		msg = msg[:120] + "…"
	return f"✗  {msg}"


class AddKeyDialog(ctk.CTkToplevel):
	"""Two-step wizard for connecting a new AI provider.

	Step 1 lets the user pick a provider from a radio button list.
	Step 2 shows setup instructions, a key entry field, and a live
	connection test. On success, ``result`` is set to (name, key).
	"""

	def __init__(self, parent):
		super().__init__(parent)
		self.title("Connect an AI provider")
		self.geometry("500x660")
		self.resizable(False, True)
		self.minsize(500, 580)
		self.grab_set()
		self.lift()
		self.focus_force()

		self.result: tuple[str, str] | None = None
		self._selected = list(PROVIDERS.keys())[0]
		self._key_var = ctk.StringVar()
		self._show_key = False
		self._card_frames: dict[str, ctk.CTkFrame] = {}

		# Fixed footer always visible at bottom
		self._footer = ctk.CTkFrame(self, fg_color="transparent", height=60)
		self._footer.pack(side="bottom", fill="x", padx=20, pady=12)
		self._footer.pack_propagate(False)

		# Scrollable content above the footer
		self._container = ctk.CTkScrollableFrame(self, fg_color="transparent")
		self._container.pack(fill="both", expand=True)

		self._show_step1()

	# ── Step 1: choose provider ───────────────────────────────────────────

	def _show_step1(self):
		"""Render the provider selection step (step 1 of 2)."""
		for w in self._container.winfo_children():
			w.destroy()

		ctk.CTkLabel(self._container, text="Step 1 of 2",
		             text_color="gray60", font=ctk.CTkFont(size=11),
		             anchor="w").pack(anchor="w", padx=24, pady=(20, 0))
		ctk.CTkLabel(self._container, text="Choose a provider",
		             font=ctk.CTkFont(size=18, weight="bold"),
		             anchor="w").pack(anchor="w", padx=24, pady=(2, 0))
		ctk.CTkLabel(self._container,
		             text="A provider is an AI service that does the explaining.\n"
		                  "You'll need a free or paid account with one of them.",
		             font=ctk.CTkFont(size=12), text_color="gray60",
		             justify="left", anchor="w",
		             ).pack(anchor="w", padx=24, pady=(4, 16))

		# Radio button variable — selection just works, no click binding needed
		self._radio_var = ctk.StringVar(value=self._selected)

		for name, info in PROVIDER_GUIDES.items():
			row = ctk.CTkFrame(self._container,
			                   fg_color=("gray90", "gray20"),
			                   corner_radius=10)
			row.pack(fill="x", padx=20, pady=4)

			ctk.CTkRadioButton(
				row,
				text="",
				variable=self._radio_var,
				value=name,
				width=24,
				command=lambda n=name: setattr(self, "_selected", n),
			).pack(side="left", padx=(14, 4), pady=14)

			info_frame = ctk.CTkFrame(row, fg_color="transparent")
			info_frame.pack(side="left", fill="x", expand=True, pady=10)

			name_row = ctk.CTkFrame(info_frame, fg_color="transparent")
			name_row.pack(anchor="w")
			icon = make_provider_icon(name, size=30)
			ctk.CTkLabel(name_row, image=icon, text="",
			             width=30).pack(side="left", padx=(0, 8))
			ctk.CTkLabel(name_row,
			             text=name,
			             font=ctk.CTkFont(size=14, weight="bold"),
			             anchor="w").pack(side="left")
			if info["free"]:
				ctk.CTkLabel(name_row, text="  FREE TIER",
				             font=ctk.CTkFont(size=10, weight="bold"),
				             text_color=("#16a34a", "#4ade80"),
				             anchor="w").pack(side="left")

			ctk.CTkLabel(info_frame, text=info["tagline"],
			             font=ctk.CTkFont(size=12),
			             text_color=("gray40", "gray60"),
			             anchor="w").pack(anchor="w")

		# Footer buttons live in the fixed _footer frame (always visible)
		for w in self._footer.winfo_children():
			w.destroy()
		ctk.CTkButton(self._footer, text="Cancel", width=100,
		              fg_color=("gray80", "gray25"),
		              text_color=("gray20", "gray80"),
		              hover_color=("gray70", "gray35"),
		              command=self.destroy).pack(side="left", pady=10)
		ctk.CTkButton(self._footer, text="Next →", width=120,
		              command=self._go_step2).pack(side="right", pady=10)

	def _build_provider_card(self, parent, name, info):
		"""Superseded by radio button rows in _show_step1."""
		pass  # superseded by radio button rows in _show_step1

	def _select(self, name):
		"""Set the selected provider and sync the radio button."""
		self._selected = name
		self._radio_var.set(name)

	def _highlight_card(self):
		"""No-op — radio buttons handle visual state."""
		pass  # radio buttons handle visual state

		# ── Step 2: instructions + key entry ─────────────────────────────────

	def _go_step2(self):
		"""Render the key entry step (step 2 of 2)."""
		for w in self._container.winfo_children():
			w.destroy()

		info = PROVIDER_GUIDES[self._selected]

		ctk.CTkLabel(self._container, text="Step 2 of 2",
		             text_color="gray60", font=ctk.CTkFont(size=11)
		             ).pack(anchor="w", padx=24, pady=(20, 0))
		hdr_row = ctk.CTkFrame(self._container, fg_color="transparent")
		hdr_row.pack(anchor="w", padx=24, pady=(2, 12))
		icon = make_provider_icon(self._selected, size=36)
		ctk.CTkLabel(hdr_row, image=icon, text="",
		             width=36).pack(side="left", padx=(0, 10))
		ctk.CTkLabel(hdr_row, text=self._selected,
		             font=ctk.CTkFont(size=18, weight="bold")
		             ).pack(side="left")

		# Open console button
		ctk.CTkButton(
			self._container,
			text=f"1.  Open {self._selected} Console  ↗",
			height=36,
			fg_color=("gray85", "gray20"),
			hover_color=("gray75", "gray30"),
			text_color=("gray10", "gray90"),
			border_width=1,
			corner_radius=8,
			anchor="w",
			command=lambda: webbrowser.open(info["url"]),
		).pack(fill="x", padx=24, pady=(0, 4))
		ctk.CTkLabel(self._container,
		             text="Opens the website where you'll create your free API key.",
		             font=ctk.CTkFont(size=11), text_color="gray60",
		             ).pack(anchor="w", padx=24, pady=(0, 10))

		# Numbered steps
		steps_frame = ctk.CTkFrame(self._container, fg_color=("gray90", "gray17"),
		                           corner_radius=8)
		steps_frame.pack(fill="x", padx=24, pady=(0, 12))
		ctk.CTkLabel(steps_frame, text="Then follow these steps:",
		             font=ctk.CTkFont(size=11, weight="bold"),
		             text_color="gray60",
		             ).pack(anchor="w", padx=12, pady=(8, 4))
		for i, step in enumerate(info["steps"], start=2):
			row = ctk.CTkFrame(steps_frame, fg_color="transparent")
			row.pack(fill="x", padx=12, pady=1)
			ctk.CTkLabel(row, text=f"{i}.", width=20,
			             font=ctk.CTkFont(size=12, family="Courier"),
			             text_color="gray60", anchor="e",
			             ).pack(side="left", padx=(0, 6))
			ctk.CTkLabel(row, text=step, font=ctk.CTkFont(size=12),
			             wraplength=380, justify="left", anchor="w",
			             ).pack(side="left", fill="x", expand=True)
		ctk.CTkFrame(steps_frame, fg_color="transparent", height=6).pack()

		# Key entry
		ctk.CTkLabel(self._container, text="Paste your key here:",
		             font=ctk.CTkFont(size=13, weight="bold"),
		             ).pack(anchor="w", padx=24, pady=(0, 4))

		entry_row = ctk.CTkFrame(self._container, fg_color="transparent")
		entry_row.pack(fill="x", padx=24)
		self._key_entry = ctk.CTkEntry(
			entry_row, textvariable=self._key_var,
			show="•", font=ctk.CTkFont(size=12, family="Courier"),
			height=36, placeholder_text="Paste key here…",
		)
		self._key_entry.pack(side="left", fill="x", expand=True)
		self._key_entry.focus_set()
		self._show_btn = ctk.CTkButton(
			entry_row, text="Show", width=60,
			fg_color="transparent", border_width=1,
			text_color=("gray30", "gray70"),
			hover_color=("gray85", "gray25"),
			command=self._toggle_show,
		)
		self._show_btn.pack(side="left", padx=(6, 0))

		ctk.CTkLabel(self._container, text=info["hint"],
		             font=ctk.CTkFont(size=11, family="Courier"),
		             text_color="gray60",
		             ).pack(anchor="w", padx=24, pady=(4, 0))

		# Test feedback
		self._test_lbl = ctk.CTkLabel(self._container, text="",
		                              font=ctk.CTkFont(size=11))
		self._test_lbl.pack(anchor="w", padx=24, pady=(4, 0))

		# Footer — lives in fixed self._footer, Save locked until test passes
		for w in self._footer.winfo_children():
			w.destroy()
		ctk.CTkButton(self._footer, text="← Back", width=100,
		              fg_color=("gray80", "gray25"),
		              text_color=("gray20", "gray80"),
		              hover_color=("gray70", "gray35"),
		              command=self._show_step1).pack(side="left", pady=10)
		self._save_btn = ctk.CTkButton(
			self._footer, text="Save & connect", width=140,
			state="disabled",
			command=self._save,
		)
		self._save_btn.pack(side="right", pady=10)
		self._test_btn = ctk.CTkButton(
			self._footer, text="Test key →", width=110,
			fg_color=("gray80", "gray25"),
			text_color=("gray20", "gray80"),
			hover_color=("gray70", "gray35"),
			command=self._test_key,
		)
		self._test_btn.pack(side="right", padx=(0, 8), pady=10)

	def _toggle_show(self):
		"""Toggle API key visibility between masked and plain text."""
		self._show_key = not self._show_key
		self._key_entry.configure(show="" if self._show_key else "•")
		self._show_btn.configure(text="Hide" if self._show_key else "Show")

	def _test_key(self):
		"""Validate the entered key by firing a minimal API call."""
		key = self._key_var.get().strip()
		if not key:
			self._test_lbl.configure(text="⚠  Paste a key first.", text_color="#d4974a")
			return
		self._test_lbl.configure(text="⏳  Testing…", text_color="gray60")
		self._test_btn.configure(state="disabled")
		threading.Thread(target=self._run_test, args=(key,), daemon=True).start()

	def _run_test(self, key: str):
		"""Worker thread: test the key and update the feedback label.

		Args:
			key: The raw API key string to test.
		"""
		ok = False
		try:
			ok = get_provider(self._selected, key).test_connection()
			if ok:
				msg, color = "✓  Connected! Now click Save & connect.", "#4ec94e"
			else:
				msg, color = "✗  Key was rejected by the provider.", "#f14c4c"
		except Exception as exc:
			msg, color = _friendly_api_error(exc), "#f14c4c"
		self.after(0, lambda: self._test_lbl.configure(text=msg, text_color=color))
		self.after(0, lambda: self._test_btn.configure(state="normal"))
		if ok:
			self.after(0, lambda: self._save_btn.configure(state="normal"))

	def _save(self):
		"""Store the validated key in result and close the dialog."""
		key = self._key_var.get().strip()
		if not key:
			self._test_lbl.configure(text="⚠  Paste your key first.", text_color="#d4974a")
			return
		self.result = (self._selected, key)
		self.destroy()


# ---------------------------------------------------------------------------
# Settings window
# ---------------------------------------------------------------------------

class SettingsWindow(ctk.CTkToplevel):
	"""Modal settings panel for managing saved API keys.

	Displays all saved provider keys with masked values, allows
	the user to set the active provider, add new keys via
	AddKeyDialog, or remove existing ones.
	"""

	def __init__(self, parent, on_close):
		super().__init__(parent)
		self.title("Settings")
		self.geometry("460x480")
		self.resizable(False, True)
		self.minsize(460, 400)
		self.grab_set()
		self.lift()
		self.focus_force()
		self._on_close = on_close
		self.protocol("WM_DELETE_WINDOW", self._close)
		self._build()
		self._refresh_keys()

	def _build(self):
		"""Build the scrollable settings layout."""
		scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
		scroll.pack(fill="both", expand=True, padx=4, pady=4)
		self._scroll = scroll

		ctk.CTkLabel(scroll, text="SAVED API KEYS",
		             font=ctk.CTkFont(size=11, weight="bold"),
		             text_color="gray60").pack(anchor="w", padx=16, pady=(12, 4))

		self._keys_frame = ctk.CTkFrame(scroll, fg_color="transparent")
		self._keys_frame.pack(fill="x", padx=16)

		ctk.CTkButton(scroll, text="+ Add another API key",
		              fg_color="transparent", border_width=1,
		              text_color=("gray30", "gray70"),
		              hover_color=("gray85", "gray25"),
		              command=self._add_key,
		              ).pack(fill="x", padx=16, pady=(10, 0))

		ctk.CTkFrame(scroll, height=1, fg_color=("gray80", "gray30")
		             ).pack(fill="x", padx=16, pady=16)

		ctk.CTkLabel(scroll, text="OUTPUT",
		             font=ctk.CTkFont(size=11, weight="bold"),
		             text_color="gray60").pack(anchor="w", padx=16, pady=(0, 8))

		self._open_after = ctk.BooleanVar(value=True)
		ctk.CTkCheckBox(scroll, text="Open PDF after generating",
		                variable=self._open_after).pack(anchor="w", padx=16, pady=2)

		self._save_next = ctk.BooleanVar(value=True)
		ctk.CTkCheckBox(scroll, text="Save PDF next to source file",
		                variable=self._save_next).pack(anchor="w", padx=16, pady=2)

	def _refresh_keys(self):
		"""Reload saved keys from config and redraw the key list."""
		for w in self._keys_frame.winfo_children():
			w.destroy()
		config = load_config()
		active = config.get("active_provider", "")
		keys = config.get("keys", {})

		if not keys:
			ctk.CTkLabel(self._keys_frame,
			             text="No keys saved yet. Click '+ Add' above.",
			             text_color="gray60", font=ctk.CTkFont(size=12),
			             ).pack(pady=12)
			return

		for name, key in keys.items():
			is_active = name == active
			row = ctk.CTkFrame(self._keys_frame,
			                   fg_color=("gray88", "gray22") if is_active else ("gray92", "gray18"),
			                   corner_radius=8)
			row.pack(fill="x", pady=3)

			masked = key[:8] + "••••" + key[-4:] if len(key) > 12 else "••••••••"
			left = ctk.CTkFrame(row, fg_color="transparent")
			left.pack(side="left", padx=12, pady=8, fill="x", expand=True)
			ctk.CTkLabel(left, text=name, font=ctk.CTkFont(size=13, weight="bold"),
			             ).pack(anchor="w")
			ctk.CTkLabel(left, text=masked,
			             font=ctk.CTkFont(size=11, family="Courier"),
			             text_color="gray60").pack(anchor="w")

			if is_active:
				ctk.CTkLabel(row, text="● Active",
				             text_color=("#16a34a", "#4ec94e"),
				             font=ctk.CTkFont(size=11, weight="bold"),
				             ).pack(side="right", padx=12)
			else:
				ctk.CTkButton(row, text="Set active", width=90,
				              fg_color="transparent", border_width=1,
				              text_color=("gray30", "gray70"),
				              hover_color=("gray85", "gray25"),
				              command=lambda n=name: self._set_active(n),
				              ).pack(side="right", padx=4, pady=6)

			ctk.CTkButton(row, text="Remove", width=76,
			              fg_color="transparent",
			              text_color=("#dc2626", "#f87171"),
			              hover_color=("gray85", "gray25"),
			              command=lambda n=name: self._remove(n),
			              ).pack(side="right", padx=(4, 0), pady=6)

	def _add_key(self):
		"""Open the AddKeyDialog wizard and save the result."""
		dlg = AddKeyDialog(self)
		self.wait_window(dlg)
		if dlg.result:
			name, key = dlg.result
			cfg = load_config()
			cfg.setdefault("keys", {})[name] = key
			if not cfg.get("active_provider"):
				cfg["active_provider"] = name
			save_config(cfg)
			self._refresh_keys()

	def _set_active(self, name: str):
		"""Set the named provider as the active one in config.

		Args:
			name: Provider display name (e.g. "Claude").
		"""
		cfg = load_config()
		cfg["active_provider"] = name
		save_config(cfg)
		self._refresh_keys()

	def _remove(self, name: str):
		"""Prompt for confirmation then delete the named provider key.

		Args:
			name: Provider display name to remove.
		"""
		if not messagebox.askyesno("Remove key", f"Remove the saved key for {name}?"):
			return
		cfg = load_config()
		cfg.get("keys", {}).pop(name, None)
		if cfg.get("active_provider") == name:
			remaining = list(cfg.get("keys", {}).keys())
			cfg["active_provider"] = remaining[0] if remaining else ""
		save_config(cfg)
		self._refresh_keys()

	def _close(self):
		"""Run the on_close callback then destroy the window."""
		self._on_close()
		self.destroy()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class DecodeForHumansApp(ctk.CTk):
	"""Main application window for decode-for-humans.

	Provides a file drop zone, decode options, a console log pane,
	and access to Settings and Batch mode. Decode operations run
	in a background thread; results are relayed via a queue.
	"""

	def __init__(self):
		super().__init__()
		self.title(WINDOW_TITLE)
		self.geometry(f"{WINDOW_W}x{WINDOW_H}")
		self.resizable(True, True)
		self.minsize(560, 700)

		self._source_path: Path | None = None
		self._log_queue = queue.Queue()
		self._line_count = 0
		self._running = False

		self._include_source = ctk.BooleanVar(value=True)
		self._export_txt = ctk.BooleanVar(value=True)

		self._build_header()
		self._build_body()
		self._build_console()

		self._poll_log_queue()
		self._log("decode-for-humans ready", "muted")
		self._log("Drop a file to begin", "dim")

	# ── Header ────────────────────────────────────────────────────────────

	def _build_header(self):
		"""Build the top header bar with title, provider badge, and settings button."""
		hdr = ctk.CTkFrame(self, height=52, corner_radius=0,
		                   fg_color=("gray88", "gray14"))
		hdr.pack(fill="x")
		hdr.pack_propagate(False)

		ctk.CTkLabel(hdr, text="decode-for-humans",
		             font=ctk.CTkFont(size=15, weight="bold"),
		             ).pack(side="left", padx=18)

		self._settings_btn = ctk.CTkButton(
			hdr, text="⚙", width=36, height=32,
			fg_color="transparent",
			text_color=("gray40", "gray60"),
			hover_color=("gray80", "gray25"),
			command=self._open_settings,
		)
		self._settings_btn.pack(side="right", padx=10)

		self._provider_btn = ctk.CTkButton(
			hdr, text="No provider set", height=30,
			fg_color="transparent", border_width=1,
			border_color=("gray70", "gray40"),
			text_color=("gray40", "gray60"),
			hover_color=("gray80", "gray25"),
			command=self._open_settings,
		)
		self._provider_btn.pack(side="right", padx=(0, 6))

		# Thin brand-colour stripe below the header — changes with active provider
		self._brand_stripe = ctk.CTkFrame(self, height=3, corner_radius=0,
		                                  fg_color=("gray85", "gray20"))
		self._brand_stripe.pack(fill="x")

		self._refresh_provider_badge()

	# ── Body ──────────────────────────────────────────────────────────────

	def _build_body(self):
		"""Build the main body: tabs, drop zone, options, and decode button."""
		body = ctk.CTkFrame(self, fg_color="transparent")
		body.pack(fill="x", padx=20, pady=14)

		# Tabs
		tab_row = ctk.CTkFrame(body, fg_color="transparent")
		tab_row.pack(fill="x", pady=(0, 14))
		ctk.CTkButton(tab_row, text="Decode file", width=110, height=32,
		              corner_radius=6).pack(side="left")
		ctk.CTkButton(tab_row, text="Batch folder", width=110, height=32,
		              corner_radius=6,
		              fg_color="transparent", border_width=1,
		              border_color=("gray70", "gray40"),
		              text_color=("gray40", "gray60"),
		              hover_color=("gray85", "gray25"),
		              command=self._open_batch,
		              ).pack(side="left", padx=6)

		# Drop zone
		self._drop_frame = ctk.CTkFrame(body, fg_color="transparent")
		self._drop_frame.pack(fill="x", pady=(0, 14))
		self._build_drop_zone()

		# Options — row 1
		opt1 = ctk.CTkFrame(body, fg_color="transparent")
		opt1.pack(fill="x", pady=(0, 6))
		ctk.CTkCheckBox(opt1, text="Include source appendix",
		                variable=self._include_source).pack(side="left")

		# Options — row 2
		opt2 = ctk.CTkFrame(body, fg_color="transparent")
		opt2.pack(fill="x", pady=(0, 14))
		ctk.CTkCheckBox(opt2, text="Also export plain text (.txt)",
		                variable=self._export_txt).pack(side="left")

		# Decode button
		self._decode_btn = ctk.CTkButton(
			body, text="Decode →", height=42,
			font=ctk.CTkFont(size=14, weight="bold"),
			state="disabled",
			command=self._start_decode,
		)
		self._decode_btn.pack(fill="x")

		# Progress bar (hidden until decoding)
		self._progress = ctk.CTkProgressBar(body, mode="indeterminate")

	def _build_drop_zone(self):
		"""Render the empty drop zone with supported language list."""
		for w in self._drop_frame.winfo_children():
			w.destroy()

		zone = ctk.CTkFrame(
			self._drop_frame,
			height=360,
			corner_radius=10,
			fg_color=("gray92", "gray18"),
			border_width=2,
			border_color=("gray75", "gray35"),
			cursor="hand2",
		)
		zone.pack(fill="x")
		zone.pack_propagate(False)
		zone.bind("<Button-1>", lambda _: self._browse_file())

		# Content pinned to top of zone — not centred, so nothing clips
		inner = ctk.CTkFrame(zone, fg_color="transparent")
		inner.place(relx=0.5, rely=0.0, anchor="n", y=14)

		ctk.CTkLabel(inner, text="↑",
		             font=ctk.CTkFont(size=28),
		             text_color=("gray55", "gray55"),
		             ).pack()
		ctk.CTkLabel(inner, text="Drop your file here",
		             font=ctk.CTkFont(size=15, weight="bold"),
		             ).pack()
		ctk.CTkLabel(inner, text="or click to browse",
		             font=ctk.CTkFont(size=11),
		             text_color=("gray55", "gray55"),
		             ).pack(pady=(4, 2))

		# All supported types — logically grouped, alphabetical within each group
		ext_groups = [
			("Notebooks", "Jupyter (.ipynb)  ·  Quarto (.qmd)  ·  R Markdown (.rmd)  ·  Markdown (.md)"),
			("Data Science", "Julia (.jl)  ·  Python (.py)  ·  R (.r)  ·  SQL (.sql)"),
			("JS / TS", "JavaScript (.js)  ·  JSX (.jsx)  ·  TypeScript (.ts)  ·  TSX (.tsx)"),
			("JVM & .NET", "C# (.cs)  ·  F# (.fs)  ·  Groovy  ·  Java  ·  Kotlin  ·  Scala  ·  VB"),
			("Systems", "C  ·  C++  ·  Go  ·  Rust  ·  Swift"),
			("Scripting", "Dart  ·  Elixir  ·  Erlang  ·  Lua  ·  Perl  ·  PHP  ·  Ruby"),
			("Shell & Infra", "Bash  ·  Dockerfile  ·  Fish  ·  PowerShell  ·  Terraform  ·  Zsh"),
			("Web", "CSS  ·  HTML  ·  Less  ·  Sass  ·  SCSS"),
			("Config", "INI  ·  JSON  ·  TOML  ·  XML  ·  YAML"),
			("Functional", "Clojure  ·  Haskell  ·  OCaml  ·  Erlang"),
		]
		for label, items in ext_groups:
			row = ctk.CTkFrame(inner, fg_color="transparent")
			row.pack(fill="x", pady=0)
			ctk.CTkLabel(row, text=f"{label}:",
			             font=ctk.CTkFont(size=9, weight="bold"),
			             text_color=("gray50", "gray45"),
			             width=80, anchor="e",
			             ).pack(side="left", padx=(0, 4))
			ctk.CTkLabel(row, text=items,
			             font=ctk.CTkFont(size=9),
			             text_color=("gray60", "gray50"),
			             anchor="w",
			             ).pack(side="left")

		for child in inner.winfo_children():
			child.bind("<Button-1>", lambda _: self._browse_file())

	def _build_file_loaded_row(self):
		"""Replace the drop zone with a file info card for the loaded file."""
		for w in self._drop_frame.winfo_children():
			w.destroy()

		path = self._source_path
		lang = detect_language(path)
		try:
			lines = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
			size_kb = round(path.stat().st_size / 1024, 1)
		except OSError:
			lines, size_kb = 0, 0.0

		row = ctk.CTkFrame(self._drop_frame, corner_radius=10,
		                   fg_color=("gray92", "gray18"),
		                   border_width=2, border_color=("gray75", "gray35"))
		row.pack(fill="x")

		badge = ctk.CTkFrame(row, width=50, height=50, corner_radius=8,
		                     fg_color=("#2563eb", "#1e40af"))
		badge.pack(side="left", padx=12, pady=12)
		badge.pack_propagate(False)
		ctk.CTkLabel(badge, text=lang[:2].upper(),
		             font=ctk.CTkFont(size=13, weight="bold"),
		             text_color="white").place(relx=0.5, rely=0.5, anchor="center")

		info = ctk.CTkFrame(row, fg_color="transparent")
		info.pack(side="left", fill="x", expand=True, pady=12)
		ctk.CTkLabel(info, text=path.name,
		             font=ctk.CTkFont(size=13, weight="bold"),
		             anchor="w").pack(anchor="w")
		ctk.CTkLabel(info, text=f"{lang}  ·  {size_kb} KB  ·  {lines} lines",
		             font=ctk.CTkFont(size=11),
		             text_color=("gray50", "gray60"),
		             anchor="w").pack(anchor="w")

		ctk.CTkButton(row, text="✕", width=32, height=32,
		              fg_color="transparent",
		              text_color=("gray50", "gray60"),
		              hover_color=("gray85", "gray25"),
		              command=self._clear_file,
		              ).pack(side="right", padx=10)

	# ── Console ───────────────────────────────────────────────────────────

	def _build_console(self):
		"""Build the console pane with coloured log output and action buttons."""
		console_outer = ctk.CTkFrame(self, fg_color=("gray88", "gray14"),
		                             corner_radius=0)
		console_outer.pack(fill="both", expand=True, side="bottom")

		# Header strip
		hdr = ctk.CTkFrame(console_outer, fg_color=("gray85", "gray17"),
		                   height=28, corner_radius=0)
		hdr.pack(fill="x")
		hdr.pack_propagate(False)

		self._dot_canvas = tk.Canvas(hdr, bg=self._console_hdr_color(),
		                             width=20, height=20,
		                             highlightthickness=0)
		self._dot_canvas.pack(side="left", padx=(10, 0))
		self._dot_item = self._dot_canvas.create_text(
			10, 10, text="●", fill="#5a5a5a", font=("", 9))

		self._console_lbl = tk.Label(hdr, text="CONSOLE",
		                             fg="#5a5a5a", bg=self._console_hdr_color(),
		                             font=("Courier", 9, "bold"))
		self._console_lbl.pack(side="left", padx=4)

		self._lc_canvas = tk.Canvas(hdr, bg=self._console_hdr_color(),
		                            height=20, highlightthickness=0)
		self._lc_canvas.pack(side="right", padx=10, fill="x", expand=True)
		self._lc_item = self._lc_canvas.create_text(
			0, 10, text="0 lines", fill="#5a5a5a",
			font=("Courier", 9), anchor="e")

		def _place_lc(event=None):
			"""Reposition the line counter text when the canvas is resized."""
			w = self._lc_canvas.winfo_width()
			if w > 1:
				self._lc_canvas.coords(self._lc_item, w, 10)

		self._lc_canvas.bind("<Configure>", _place_lc)

		# Separator
		ctk.CTkFrame(console_outer, height=1,
		             fg_color=("gray75", "gray28"), corner_radius=0).pack(fill="x")

		# Text log
		self._console_text = tk.Text(
			console_outer, bg="#1e1e1e", fg="#c0c0c0",
			font=("Courier", 10), height=7,
			state="normal", wrap="word",
			relief="flat", bd=0, padx=12, pady=8,
			cursor="ibeam",  # shows text-select cursor
			insertwidth=0,  # no blinking caret
			selectbackground="#264f78",
			selectforeground="#ffffff",
		)
		# Block typing but allow mouse selection and Cmd/Ctrl+A
		self._console_text.bind("<Key>",
		                        lambda e: None if e.state & 0x08 or e.keysym in
		                                          ("Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next")
		                        else "break")
		self._console_text.pack(fill="both", expand=True)
		for level, colour in LOG_COLORS.items():
			self._console_text.tag_config(level, foreground=colour)

		# Footer strip
		ctk.CTkFrame(console_outer, height=1,
		             fg_color=("gray75", "gray28"), corner_radius=0).pack(fill="x")
		ftr = ctk.CTkFrame(console_outer, height=28,
		                   fg_color=("gray85", "gray17"), corner_radius=0)
		ftr.pack(fill="x")
		ftr.pack_propagate(False)

		ctk.CTkButton(ftr, text="clear", width=50, height=22,
		              fg_color="transparent",
		              text_color=("gray50", "gray55"),
		              hover_color=("gray80", "gray25"),
		              font=ctk.CTkFont(size=11, family="Courier"),
		              command=self._clear_console).pack(side="left", padx=(8, 0), pady=3)
		ctk.CTkButton(ftr, text="copy logs", width=80, height=22,
		              fg_color="transparent",
		              text_color=("gray50", "gray55"),
		              hover_color=("gray80", "gray25"),
		              font=ctk.CTkFont(size=11, family="Courier"),
		              command=self._copy_logs).pack(side="left", padx=2, pady=3)

		self._status_var = ctk.StringVar()
		ctk.CTkLabel(ftr, textvariable=self._status_var,
		             font=ctk.CTkFont(size=10, family="Courier"),
		             text_color=("gray50", "gray55"),
		             ).pack(side="right", padx=10)

	def _console_hdr_color(self):
		"""Return the correct bg hex for console header tk widgets."""
		mode = ctk.get_appearance_mode()
		return "#2b2b2b" if mode == "Dark" else "#d9d9d9"

	# ── Console helpers ───────────────────────────────────────────────────

	def _log(self, message: str, level: str = "info"):
		"""Enqueue a timestamped log message for the console.

		Args:
			message: Text to display.
			level: Colour tag key from LOG_COLORS (default "info").
		"""
		self._log_queue.put((datetime.now().strftime("%H:%M:%S"), message, level))

	def _poll_log_queue(self):
		"""Drain the log queue and schedule the next poll in 100 ms."""
		try:
			while True:
				item = self._log_queue.get_nowait()
				if item[0] == _DECODE_DONE:
					self._on_decode_finished()
					continue
				ts, msg, lvl = item
				self._line_count += 1
				self._console_text.insert("end", ts, "time")
				self._console_text.insert("end", f"  {msg}\n", lvl)
				self._console_text.see("end")
				n = self._line_count
				self._lc_canvas.itemconfig(
					self._lc_item, text=f"{n} line{'s' if n != 1 else ''}")
		except queue.Empty:
			pass
		self.after(100, self._poll_log_queue)

	def _clear_console(self):
		"""Clear all console text and reset the line counter."""
		self._console_text.delete("1.0", "end")
		self._line_count = 0
		self._lc_canvas.itemconfig(self._lc_item, text="0 lines")
		self._status_var.set("")

	def _copy_logs(self):
		"""Copy all console text to the system clipboard."""
		import platform, subprocess
		text = self._console_text.get("1.0", "end").strip()
		if not text:
			return
		try:
			if platform.system() == "Darwin":
				# macOS: tkinter clipboard is unreliable; use pbcopy
				proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
				proc.communicate(text.encode("utf-8"))
			else:
				self.clipboard_clear()
				self.clipboard_append(text)
				self.update()  # flush so clipboard persists after focus loss
		except Exception:
			# Fallback
			self.clipboard_clear()
			self.clipboard_append(text)
		self._status_var.set("copied!")
		self.after(1500, lambda: self._status_var.set(""))

	def _set_dot(self, color: str):
		"""Update the status dot colour in the console header.

		Args:
			color: Hex colour string (e.g. "#4ec94e" for green).
		"""
		self._dot_canvas.itemconfig(self._dot_item, fill=color)

	# ── File management ───────────────────────────────────────────────────

	def _browse_file(self):
		"""Open a file picker and load the chosen file."""
		path_str = filedialog.askopenfilename(
			title="Select a source code file",
			filetypes=[
				("All supported",
				 "*.py *.pyw *.js *.mjs *.ts *.jsx *.tsx "
				 "*.java *.kt *.scala *.groovy "
				 "*.c *.h *.cpp *.cc *.cs *.fs "
				 "*.go *.rs *.rb *.php *.swift *.dart *.lua "
				 "*.r *.rmd *.Rmd *.jl *.sql *.m "
				 "*.ex *.exs *.erl *.hs *.ml *.clj "
				 "*.sh *.bash *.zsh *.fish *.ps1 "
				 "*.html *.css *.scss *.sass "
				 "*.yaml *.yml *.json *.toml *.xml "
				 "*.ipynb *.qmd *.md "
				 "*.dockerfile *.tf *.proto"),
				("Notebooks", "*.ipynb *.qmd *.rmd *.Rmd"),
				("All files", "*.*"),
			],
		)
		if path_str:
			self._load_file(Path(path_str))

	def _load_file(self, path: Path):
		"""Validate and load a source file into the drop zone.

		Args:
			path: Path to the source file to load.
		"""
		ext = path.suffix.lower()
		# Reject known-unsupported formats immediately with a clear message
		if ext in _UNSUPPORTED_FORMATS and ext not in EXTENSION_MAP:
			kind = _UNSUPPORTED_FORMATS[ext]
			messagebox.showerror(
				"Unsupported file type",
				f"'{path.name}' is a {kind}.\n\n"
				f"decode-for-humans only works with source code files\n"
				f"such as .py  .js  .ts  .go  .rs  .java  .sql  .r  and 40+ more.",
			)
			return
		self._source_path = path
		self._build_file_loaded_row()
		self._decode_btn.configure(state="normal")
		self._clear_console()
		lang = detect_language(path)
		self._log(f"Loaded → {path.name}", "success")
		self._log(f"Detected: {lang}", "info")
		if lang == "Unknown":
			self._log("  ⚠ Language not recognised — AI may give a generic explanation", "warn")
		self._log("Ready — press Decode to continue", "muted")
		self._set_dot("#4ec94e")

	def _clear_file(self):
		"""Clear the loaded file and restore the empty drop zone."""
		self._source_path = None
		self._build_drop_zone()
		self._decode_btn.configure(state="disabled")
		self._clear_console()
		self._log("File cleared", "muted")
		self._set_dot("#5a5a5a")

	# ── Settings ──────────────────────────────────────────────────────────

	def _open_settings(self):
		"""Open the Settings window."""
		SettingsWindow(self, on_close=self._refresh_provider_badge)

	def _downloads_folder(self) -> "Path":
		"""Return the Downloads folder, falling back to home if it does not exist."""
		d = Path.home() / "Downloads"
		return d if d.exists() else Path.home()

	def _open_batch(self):
		"""Open the batch decode window if a provider is configured."""
		cfg = load_config()
		name = cfg.get("active_provider", "")
		key = cfg.get("keys", {}).get(name, "")
		if not name or not key:
			messagebox.showwarning("No provider",
			                       "Please connect a provider in Settings before using batch mode.")
			return
		BatchWindow(self, provider_name=name, api_key=key,
		            log_fn=self._log, downloads=self._downloads_folder())

	def _refresh_provider_badge(self):
		"""Update the provider button with the active provider name and brand colour."""
		cfg = load_config()
		name = cfg.get("active_provider", "")

		if name and name in PROVIDER_BRANDS:
			brand = PROVIDER_BRANDS[name]
			icon = make_provider_icon(name, size=20)
			self._provider_btn.configure(
				text=f"  {name}",
				image=icon,
				compound="left",
				text_color=(brand["bg"], brand["bg"]),
				border_color=(brand["bg"], brand["bg"]),
				hover_color=("gray80", "gray25"),
			)
			self._brand_stripe.configure(fg_color=(brand["bg"], brand["bg"]))
		else:
			self._provider_btn.configure(
				text="No provider set",
				image=None,
				compound="left",
				text_color=("gray40", "gray60"),
				border_color=("gray70", "gray40"),
				hover_color=("gray80", "gray25"),
			)
			self._brand_stripe.configure(fg_color=("gray85", "gray20"))

	# ── Decode pipeline ───────────────────────────────────────────────────

	def _start_decode(self):
		"""Validate state and launch the decode worker thread."""
		if self._running or not self._source_path:
			return
		cfg = load_config()
		provider_name = cfg.get("active_provider", "")
		api_key = cfg.get("keys", {}).get(provider_name, "")
		if not provider_name or not api_key:
			messagebox.showwarning("No API key",
			                       "No provider key is set.\n\nOpen Settings (⚙) to add one.")
			return
		self._running = True
		self._decode_btn.configure(state="disabled", text="Decoding…")
		self._progress.pack(fill="x", padx=20, pady=(4, 0))
		self._progress.start()
		self._set_dot("#d4974a")
		self._clear_console()
		threading.Thread(
			target=self._decode_worker,
			args=(self._source_path, provider_name, api_key,
			      self._include_source.get(), self._export_txt.get()),
			daemon=True,
		).start()

	def _decode_worker(self, source_path, provider_name, api_key, include_source, export_txt=True):
		"""Background thread: read, prompt, call AI, write outputs, signal done.

		Args:
			source_path: Path to the source file to decode.
			provider_name: Active provider display name.
			api_key: API key for the provider.
			include_source: Whether to append source code to the output.
			export_txt: Whether to also write a plain-text output file.
		"""
		import time
		downloads = Path.home() / "Downloads"
		folder = downloads if downloads.exists() else Path.home()
		out = folder / (source_path.stem + "_explanation.md")
		try:
			# ── Read ──────────────────────────────────────────────────────
			self._log("Reading file…", "info")
			code = read_file(source_path)
			language = detect_language(source_path, content=code)
			lines = len(code.splitlines())
			size_kb = round(source_path.stat().st_size / 1024, 1)
			self._log(f"  Language : {language}", "dim")
			self._log(f"  Size     : {size_kb} KB  ·  {lines:,} lines", "dim")

			# ── Prompt ────────────────────────────────────────────────────
			self._log("Building prompt…", "info")
			prompt = build_prompt(source_path.name, language, code)
			chars = len(prompt)
			self._log(f"  Prompt   : {chars:,} chars  (~{chars // 4:,} tokens)", "dim")
			if chars > 60_000:
				self._log("  Note: long file — code was trimmed to fit AI limits", "warn")

			# ── AI call ───────────────────────────────────────────────────
			self._log(f"Connecting to {provider_name}…", "info")
			provider = get_provider(provider_name, api_key)
			self._log("Waiting for AI response  (this can take 30–90s)…", "info")
			t0 = time.time()
			explanation = provider.explain(prompt)
			elapsed = round(time.time() - t0, 1)
			words = len(explanation.split())
			sections = len(explanation.split("## ")) - 1
			self._log(f"  Response : ~{words:,} words  in {elapsed}s", "success")
			self._log(f"  Sections : {sections} found in AI output", "dim")

			# ── Markdown ──────────────────────────────────────────────────
			self._log("Building Markdown…", "info")
			build_md(
				output_path=out,
				filename=source_path.name,
				language=language,
				code=code,
				explanation=explanation,
				include_source=include_source,
			)
			self._log(f"MD  saved → {out.name}", "success")

			# ── Plain text ─────────────────────────────────────────────────
			if export_txt:
				txt_out = out.with_suffix(".txt")
				self._log("Building plain-text copy…", "info")
				build_txt(
					output_path=txt_out,
					filename=source_path.name,
					language=language,
					code=code,
					explanation=explanation,
					include_source=include_source,
				)
				self._log(f"TXT saved → {txt_out.name}", "success")

			self._log("Done! ✓", "success")

		except FileNotFoundError as e:
			self._log(f"File not found: {e}", "error")
		except ValueError as e:
			self._log(f"Validation error: {e}", "error")
		except Exception as e:
			self._log(_friendly_api_error(e), "error")
		finally:
			self._log_queue.put((_DECODE_DONE, "", ""))

	def _on_decode_finished(self):
		"""Stop the progress bar and re-enable the decode button."""
		self._running = False
		self._progress.stop()
		self._progress.pack_forget()
		self._decode_btn.configure(state="normal", text="Decode →")
		self._set_dot("#4ec94e")
		self._status_var.set("last run complete")


# ---------------------------------------------------------------------------
# Batch window
# ---------------------------------------------------------------------------

class BatchWindow(ctk.CTkToplevel):
	"""Folder batch-decode — scan, preview token estimates, decode all."""

	# Supported extensions (same set as EXTENSION_MAP)
	_EXTS = set(EXTENSION_MAP.keys())

	def __init__(self, parent, provider_name: str, api_key: str,
	             log_fn, downloads: "Path"):
		super().__init__(parent)
		self.title("Batch Decode")
		self.geometry("740x780")
		self.resizable(True, True)
		self.minsize(620, 600)
		self.grab_set()
		self.lift()
		self.focus_force()

		self._provider_name = provider_name
		self._api_key = api_key
		self._log_main = log_fn  # log to main window console
		self._downloads = downloads
		self._folder: "Path | None" = None
		self._file_rows: dict = {}  # path → {"var", "status", "tok_lbl"}
		self._token_ests: dict = {}  # path → int
		self._running = False
		self._include_source = ctk.BooleanVar(value=True)
		self._export_txt = ctk.BooleanVar(value=True)

		self._build_ui()

	# ── UI construction ───────────────────────────────────────────────────

	def _build_ui(self):
		"""Build the batch window layout with folder picker, file list, and controls."""
		# Header
		hdr = ctk.CTkFrame(self, fg_color=("gray88", "gray15"),
		                   corner_radius=0, height=56)
		hdr.pack(fill="x")
		hdr.pack_propagate(False)
		brand = PROVIDER_BRANDS.get(self._provider_name, {})
		icon = make_provider_icon(self._provider_name, size=22)
		ctk.CTkLabel(hdr, text="Batch Decode",
		             font=ctk.CTkFont(size=15, weight="bold")).pack(
			side="left", padx=18, pady=14)
		ctk.CTkLabel(hdr, image=icon, text=f"  {self._provider_name}",
		             compound="left",
		             text_color=(brand.get("bg", "gray"), brand.get("bg", "gray")),
		             font=ctk.CTkFont(size=11)).pack(side="right", padx=14)

		body = ctk.CTkFrame(self, fg_color="transparent")
		body.pack(fill="both", expand=True, padx=18, pady=12)

		# Folder picker
		pick = ctk.CTkFrame(body, fg_color="transparent")
		pick.pack(fill="x", pady=(0, 8))
		self._folder_lbl = ctk.CTkLabel(pick, text="No folder selected",
		                                text_color="gray50", anchor="w")
		self._folder_lbl.pack(side="left", fill="x", expand=True)
		ctk.CTkButton(pick, text="Browse…", width=100,
		              command=self._browse).pack(side="right")

		# Column headers
		col_hdr = ctk.CTkFrame(body, fg_color="transparent")
		col_hdr.pack(fill="x")
		for text, anchor, expand, width in [
			("File", "w", True, 0),
			("Language", "w", False, 110),
			("Lines", "e", False, 60),
			("", "w", False, 28),  # status icon column
		]:
			ctk.CTkLabel(col_hdr, text=text, font=ctk.CTkFont(size=10),
			             text_color="gray50", anchor=anchor,
			             width=width).pack(side="left",
			                               fill="x" if expand else None,
			                               expand=expand)

		# File list
		self._list = ctk.CTkScrollableFrame(body, fg_color=("gray92", "gray18"),
		                                    corner_radius=8, height=380)
		self._list.pack(fill="x", pady=(2, 8))
		self._empty_lbl = ctk.CTkLabel(self._list,
		                               text="Browse a folder to scan for source files",
		                               text_color="gray50")
		self._empty_lbl.pack(pady=(16, 6))
		batch_ext_groups = [
			("Notebooks", "Jupyter (.ipynb)  ·  Quarto (.qmd)  ·  R Markdown (.rmd)  ·  Markdown (.md)"),
			("Data Science", "Julia (.jl)  ·  Python (.py)  ·  R (.r)  ·  SQL (.sql)"),
			("JS / TS", "JavaScript (.js)  ·  JSX (.jsx)  ·  TypeScript (.ts)  ·  TSX (.tsx)"),
			("JVM & .NET", "C# (.cs)  ·  Groovy  ·  Java  ·  Kotlin  ·  Scala  ·  VB"),
			("Systems", "C  ·  C++  ·  Go  ·  Rust  ·  Swift"),
			("Scripting", "Dart  ·  Elixir  ·  Erlang  ·  Lua  ·  Perl  ·  PHP  ·  Ruby"),
			("Shell & Infra", "Bash  ·  Dockerfile  ·  Fish  ·  PowerShell  ·  Terraform  ·  Zsh"),
			("Web", "CSS  ·  HTML  ·  Less  ·  Sass  ·  SCSS"),
			("Config", "INI  ·  JSON  ·  TOML  ·  XML  ·  YAML"),
		]
		for label, items in batch_ext_groups:
			row = ctk.CTkFrame(self._list, fg_color="transparent")
			row.pack(fill="x", padx=8, pady=0)
			ctk.CTkLabel(row, text=f"{label}:",
			             font=ctk.CTkFont(size=9, weight="bold"),
			             text_color="gray45", width=80, anchor="e",
			             ).pack(side="left", padx=(0, 4))
			ctk.CTkLabel(row, text=items,
			             font=ctk.CTkFont(size=9),
			             text_color="gray55", anchor="w",
			             ).pack(side="left")

		# Options row
		opt = ctk.CTkFrame(body, fg_color="transparent")
		opt.pack(fill="x", pady=(0, 4))
		ctk.CTkCheckBox(opt, text="Include source appendix",
		                variable=self._include_source).pack(side="left")
		ctk.CTkCheckBox(opt, text="Also export .txt",
		                variable=self._export_txt).pack(side="left", padx=16)

		# Summary
		self._summary_lbl = ctk.CTkLabel(body, text="", anchor="w",
		                                 font=ctk.CTkFont(size=11),
		                                 text_color="gray50")
		self._summary_lbl.pack(anchor="w")

		# Progress bar (hidden until running)
		self._prog = ctk.CTkProgressBar(body, mode="indeterminate")

		# Footer
		foot = ctk.CTkFrame(self, fg_color="transparent", height=56)
		foot.pack(side="bottom", fill="x", padx=18, pady=8)
		foot.pack_propagate(False)
		ctk.CTkButton(foot, text="Close", width=100,
		              fg_color=("gray80", "gray25"),
		              text_color=("gray20", "gray80"),
		              hover_color=("gray70", "gray35"),
		              command=self.destroy).pack(side="left", pady=8)
		self._start_btn = ctk.CTkButton(foot, text="Start batch →", width=150,
		                                state="disabled", command=self._confirm_start)
		self._start_btn.pack(side="right", pady=8)

	# ── Folder scanning ───────────────────────────────────────────────────

	def _browse(self):
		"""Open a folder picker and trigger a file scan."""
		folder = filedialog.askdirectory(title="Select folder to batch decode")
		if not folder:
			return
		self._folder = Path(folder)
		self._folder_lbl.configure(text=str(self._folder), text_color=("gray20", "gray80"))
		self._scan()

	def _scan(self):
		"""Find supported files and populate the list in a background thread."""
		for w in self._list.winfo_children():
			w.destroy()
		self._file_rows.clear()
		self._token_ests.clear()
		self._start_btn.configure(state="disabled")
		self._summary_lbl.configure(text="Scanning…")

		def worker():
			files = sorted(
				p for p in self._folder.rglob("*")
				if p.is_file() and p.suffix.lower() in self._EXTS
			)
			self.after(0, lambda: self._populate(files))

		threading.Thread(target=worker, daemon=True).start()

	def _populate(self, files: list):
		"""Render file rows and kick off background token estimation."""
		if not files:
			ctk.CTkLabel(self._list, text="No supported source files found.",
			             text_color="gray50").pack(pady=40)
			self._summary_lbl.configure(text="")
			return

		self._empty_lbl = None
		for path in files:
			self._add_file_row(path)

		self._summary_lbl.configure(text=f"Found {len(files)} files — estimating tokens…")
		# Estimate tokens in background
		threading.Thread(target=self._estimate_all, args=(files,), daemon=True).start()

	def _add_file_row(self, path: "Path"):
		"""Render a single file row in the batch list with checkbox, name, language, and line count.

		Args:
			path: Path to the source file being listed.
		"""
		row = ctk.CTkFrame(self._list, fg_color="transparent")
		row.pack(fill="x", padx=4, pady=1)

		var = ctk.BooleanVar(value=True)
		ctk.CTkCheckBox(row, text="", variable=var, width=24,
		                command=self._update_summary).pack(side="left", padx=(4, 2))

		# Filename (truncated)
		name = path.name
		if len(name) > 32:
			name = name[:29] + "…"
		ctk.CTkLabel(row, text=name, anchor="w",
		             font=ctk.CTkFont(size=11)).pack(side="left", fill="x", expand=True)

		# Language
		try:
			lang = detect_language(path)
		except Exception:
			lang = "?"
		ctk.CTkLabel(row, text=lang, anchor="w", width=110,
		             font=ctk.CTkFont(size=10),
		             text_color="gray50").pack(side="left")

		# Lines
		try:
			n_lines = path.read_bytes().count(b"\n")
		except Exception:
			n_lines = 0
		ctk.CTkLabel(row, text=f"{n_lines:,}", anchor="e", width=55,
		             font=ctk.CTkFont(size=10, family="Courier"),
		             text_color="gray50").pack(side="left")

		# Status icon
		status_lbl = ctk.CTkLabel(row, text="", width=28, anchor="w",
		                          font=ctk.CTkFont(size=11))
		status_lbl.pack(side="left", padx=(4, 0))

		self._file_rows[path] = {"var": var, "status": status_lbl}

	def _estimate_all(self, files: list):
		"""Estimate tokens for each file using the real prompt — matches console display."""
		for path in files:
			try:
				code = read_file(path)
				language = detect_language(path, content=code)
				prompt = build_prompt(path.name, language, code)
				# Input tokens + estimated output (~2000 tokens of explanation)
				est = len(prompt) // 4 + 2000
			except Exception:
				try:
					# Fallback: raw byte size / 4
					est = path.stat().st_size // 4 + 2000
				except Exception:
					est = 2000
			self._token_ests[path] = est
			# No per-row token display — just update summary when done

		self.after(0, self._update_summary)

	def _update_summary(self):
		"""Recompute selected file count and token estimate and update the summary label."""
		selected = [p for p, r in self._file_rows.items() if r["var"].get()]
		n = len(selected)
		tok = sum(self._token_ests.get(p, 2000) for p in selected)
		mins = max(1, round(n * 50 / 60))  # ~50s per file
		self._summary_lbl.configure(
			text=f"{n} of {len(self._file_rows)} files selected  ·  "
			     f"~{tok:,} total tokens  ·  ~{mins} min estimated"
		)
		self._start_btn.configure(state="normal" if n > 0 else "disabled")

	# ── Batch execution ───────────────────────────────────────────────────

	def _confirm_start(self):
		"""Show a confirmation dialog and start the batch if confirmed."""
		selected = [p for p, r in self._file_rows.items() if r["var"].get()]
		if not selected:
			return
		tok = sum(self._token_ests.get(p, 2000) for p in selected)
		mins = max(1, round(len(selected) * 50 / 60))
		proceed = messagebox.askyesno(
			"Confirm batch decode",
			f"Decode {len(selected)} file{'s' if len(selected) != 1 else ''}?\n\n"
			f"  Estimated tokens : ~{tok:,}\n"
			f"  Estimated time   : ~{mins} minute{'s' if mins != 1 else ''}\n"
			f"  Provider         : {self._provider_name}\n\n"
			f"Each file produces an MD{' and TXT' if self._export_txt.get() else ''} "
			f"in your Downloads folder.\n\n"
			f"Proceed?",
			parent=self,
		)
		if not proceed:
			return
		self._run_batch(selected)

	def _run_batch(self, files: list):
		"""Hide the window and launch the batch worker thread.

		Args:
			files: List of Paths to decode.
		"""
		self._running = True
		# Hide immediately so the main console is accessible for copy/clear
		self.withdraw()
		threading.Thread(target=self._batch_worker,
		                 args=(files,), daemon=True).start()

	def _batch_worker(self, files: list):
		"""Background thread: decode each file and log results to the main console.

		Args:
			files: List of Paths to decode in order.
		"""
		import time
		downloads = self._downloads
		ok = 0
		for i, path in enumerate(files, 1):
			self.after(0, lambda p=path: self._set_status(p, "⏳", "gray60"))
			label = f"[{i}/{len(files)}] {path.name}"
			self._log_main(f"Batch {label}", "info")
			try:
				code = read_file(path)
				language = detect_language(path, content=code)
				prompt = build_prompt(path.name, language, code)
				self._log_main(f"  Language: {language}  ·  ~{len(prompt) // 4:,} tokens", "dim")

				provider = get_provider(self._provider_name, self._api_key)
				t0 = time.time()
				explanation = provider.explain(prompt)
				elapsed = round(time.time() - t0, 1)
				self._log_main(f"  Response: ~{len(explanation.split()):,} words in {elapsed}s", "success")

				out = downloads / (path.stem + "_explanation.md")
				build_md(output_path=out, filename=path.name, language=language,
				         code=code, explanation=explanation,
				         include_source=self._include_source.get())
				self._log_main(f"  MD  saved → {out.name}", "success")

				if self._export_txt.get():
					txt = out.with_suffix(".txt")
					build_txt(output_path=txt, filename=path.name, language=language,
					          code=code, explanation=explanation,
					          include_source=self._include_source.get())
					self._log_main(f"  TXT saved → {txt.name}", "success")

				self.after(0, lambda p=path: self._set_status(p, "✓", "#4ec94e"))
				ok += 1

			except Exception as exc:
				self._log_main(f"  ✗ {_friendly_api_error(exc)}", "error")
				self.after(0, lambda p=path: self._set_status(p, "✗", "#f14c4c"))

		self._log_main(
			f"Batch complete  ·  {ok}/{len(files)} succeeded", "success"
		)
		self.after(0, self._on_batch_done)

	def _set_status(self, path: "Path", icon: str, color: str):
		"""Update the status icon for a file row in the batch list.

		Args:
			path: The file whose row should be updated.
			icon: Unicode icon to display (e.g. "✓", "✗", "⏳").
			color: Hex colour string for the icon.
		"""
		try:
			if path in self._file_rows:
				self._file_rows[path]["status"].configure(text=icon, text_color=color)
		except Exception:
			pass  # window may be hidden

	def _on_batch_done(self):
		"""Mark the batch as complete and close the window."""
		self._running = False
		try:
			self.destroy()
		except Exception:
			pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
	app = DecodeForHumansApp()
	app.mainloop()


if __name__ == "__main__":
	main()
