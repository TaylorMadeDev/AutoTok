from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
import traceback
import webbrowser
from datetime import datetime, timedelta
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from autotok.config import (
    APP_DIR,
    OpenRouterConfig,
    load_openrouter_config,
    migrate_plaintext_secret,
    save_openrouter_config,
)
from autotok.engine import RenderOptions, generate_preview_image, render_story_series, synthesize_speech
from autotok.jobs import append_history, find_duplicate_story, load_history, load_queue, save_queue, seen_permalinks
from autotok.library import MUSIC_DIR, VIDEO_DIR, scan_music, scan_videos, video_categories
from autotok.preferences import AppPreferences, load_preferences, save_preferences
from autotok.publishing import (
    create_private_test,
    duplicate_published_video,
    enqueue_publish,
    load_publish_queue,
    run_community_upload,
    setup_uploader,
    update_publish_item,
    uploader_status,
)
from autotok.reddit import StoryPost, fetch_random_story, fetch_stories
from autotok.studio import (
    analytics_summary,
    apply_preset,
    generate_hook_variants,
    import_analytics_csv,
    load_drafts,
    load_post_package,
    load_presets,
    notify_desktop,
    preflight_video,
    save_post_package,
    save_preset,
    save_draft,
    save_rights_record,
)
from autotok.voices import DEFAULT_FLUX_VOICE, FLUX_VOICES, valid_flux_voice


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

COLORS = {
    "window": "#090B12",
    "panel": "#11141D",
    "panel_2": "#171B27",
    "border": "#282D3C",
    "text": "#F4F6FC",
    "muted": "#9299AC",
    "accent": "#7C5CFC",
    "accent_hover": "#6B49F2",
    "orange": "#FF6246",
    "success": "#37D69B",
    "danger": "#FF647C",
}

VIDEO_LIBRARY_DIR = VIDEO_DIR


class AdvancedSettingsDialog(ctk.CTkToplevel):
    def __init__(self, master: "AutoTokApp"):
        super().__init__(master)
        self.master_app = master
        self.title("AutoTok production settings")
        self.geometry("650x760")
        self.minsize(590, 680)
        self.configure(fg_color=COLORS["window"])
        self.transient(master)
        self.grab_set()
        prefs = master.preferences

        ctk.CTkLabel(self, text="Production settings", font=ctk.CTkFont(size=25, weight="bold"), text_color=COLORS["text"]).pack(anchor="w", padx=28, pady=(24, 4))
        ctk.CTkLabel(self, text="These settings are saved automatically for future projects.", text_color=COLORS["muted"]).pack(anchor="w", padx=28, pady=(0, 14))
        body = ctk.CTkScrollableFrame(self, fg_color=COLORS["panel"], corner_radius=14)
        body.pack(fill="both", expand=True, padx=26, pady=(0, 14))

        self.caption_style = self._option(body, "CAPTION STYLE", ["Karaoke", "Purple Pop", "Classic"], prefs.caption_style)
        self.caption_words_label = self._slider_label(body, "WORDS PER CAPTION", prefs.caption_words, " words")
        self.caption_words = ctk.CTkSlider(
            body, from_=3, to=10, number_of_steps=7, progress_color=COLORS["accent"],
            button_color=COLORS["accent"],
            command=lambda value: self.caption_words_label.configure(text=f"WORDS PER CAPTION  •  {value:.0f} words"),
        )
        self.caption_words.set(prefs.caption_words)
        self.caption_words.pack(fill="x", padx=18, pady=(0, 4))
        self.part_length = self._option(body, "AUTOMATIC PART LENGTH", ["60 seconds", "90 seconds", "3 minutes", "Full story"], prefs.part_length)
        self.profanity = self._option(body, "PROFANITY MODE", ["Uncensored", "Softened", "Platform-safe"], prefs.profanity_mode)
        self.whisper_model = self._option(body, "WHISPER ALIGNMENT MODEL", ["tiny.en", "base.en", "small.en"], prefs.whisper_model)

        self.align_var = ctk.BooleanVar(value=prefs.align_captions)
        ctk.CTkSwitch(body, text="Word-level Whisper alignment", variable=self.align_var, progress_color=COLORS["accent"]).pack(anchor="w", padx=18, pady=(18, 8))
        self.scene_var = ctk.BooleanVar(value=prefs.scene_aware)
        ctk.CTkSwitch(body, text="Scene-aware footage cuts", variable=self.scene_var, progress_color=COLORS["accent"]).pack(anchor="w", padx=18, pady=8)
        self.music_var = ctk.BooleanVar(value=prefs.auto_music)
        ctk.CTkSwitch(body, text="Auto-pick background music", variable=self.music_var, progress_color=COLORS["accent"]).pack(anchor="w", padx=18, pady=8)
        self.smart_match_var = ctk.BooleanVar(value=prefs.smart_match_background)
        ctk.CTkSwitch(body, text="Match footage category to story mood", variable=self.smart_match_var, progress_color=COLORS["accent"]).pack(anchor="w", padx=18, pady=8)
        self.audio_polish_var = ctk.BooleanVar(value=prefs.audio_polish)
        ctk.CTkSwitch(body, text="Polish narration loudness and fades", variable=self.audio_polish_var, progress_color=COLORS["accent"]).pack(anchor="w", padx=18, pady=8)
        self.cover_var = ctk.BooleanVar(value=prefs.bake_tiktok_cover)
        ctk.CTkSwitch(body, text="Add a short selectable TikTok cover frame", variable=self.cover_var, progress_color=COLORS["accent"]).pack(anchor="w", padx=18, pady=8)
        self.duplicate_var = ctk.BooleanVar(value=prefs.duplicate_check)
        ctk.CTkSwitch(body, text="Warn before rendering duplicate stories", variable=self.duplicate_var, progress_color=COLORS["accent"]).pack(anchor="w", padx=18, pady=8)
        self.notifications_var = ctk.BooleanVar(value=prefs.desktop_notifications)
        ctk.CTkSwitch(body, text="Desktop notifications for failures", variable=self.notifications_var, progress_color=COLORS["accent"]).pack(anchor="w", padx=18, pady=8)

        self.speed_label = self._slider_label(body, "VOICE SPEED", prefs.voice_speed, "×")
        self.speed = ctk.CTkSlider(body, from_=0.85, to=1.15, number_of_steps=6, progress_color=COLORS["accent"], command=lambda v: self.speed_label.configure(text=f"VOICE SPEED  •  {v:.2f}×"))
        self.speed.set(prefs.voice_speed)
        self.speed.pack(fill="x", padx=18, pady=(0, 10))
        self.music_label = self._slider_label(body, "MUSIC VOLUME", prefs.music_volume * 100, "%")
        self.music_volume = ctk.CTkSlider(body, from_=0, to=25, number_of_steps=25, progress_color=COLORS["accent"], command=lambda v: self.music_label.configure(text=f"MUSIC VOLUME  •  {v:.0f}%"))
        self.music_volume.set(prefs.music_volume * 100)
        self.music_volume.pack(fill="x", padx=18, pady=(0, 12))

        folders = ctk.CTkFrame(body, fg_color="transparent")
        folders.pack(fill="x", padx=18, pady=(4, 18))
        ctk.CTkButton(folders, text=f"Open music folder ({len(scan_music())})", fg_color=COLORS["panel_2"], command=lambda: self._open_folder(MUSIC_DIR)).pack(side="left", expand=True, fill="x", padx=(0, 5))
        ctk.CTkButton(folders, text="Edit pronunciations", fg_color=COLORS["panel_2"], command=lambda: self._open_file(APP_DIR / "pronunciations.json")).pack(side="left", expand=True, fill="x", padx=(5, 0))

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=26, pady=(0, 20))
        ctk.CTkButton(actions, text="Cancel", fg_color="transparent", border_width=1, border_color=COLORS["border"], command=self.destroy).pack(side="left")
        ctk.CTkButton(actions, text="Save settings", fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"], command=self._save).pack(side="right")

    def _option(self, parent, label: str, values: list[str], value: str) -> ctk.StringVar:
        ctk.CTkLabel(parent, text=label, text_color=COLORS["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=18, pady=(16, 6))
        variable = ctk.StringVar(value=value if value in values else values[0])
        ctk.CTkOptionMenu(parent, values=values, variable=variable, fg_color=COLORS["panel_2"], button_color=COLORS["accent"], button_hover_color=COLORS["accent_hover"]).pack(fill="x", padx=18)
        return variable

    def _slider_label(self, parent, label: str, value: float, suffix: str):
        shown = f"{value:.2f}" if suffix == "×" else f"{value:.0f}"
        widget = ctk.CTkLabel(parent, text=f"{label}  •  {shown}{suffix}", text_color=COLORS["muted"], font=ctk.CTkFont(size=10, weight="bold"))
        widget.pack(anchor="w", padx=18, pady=(16, 6))
        return widget

    def _open_folder(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)  # type: ignore[attr-defined]

    def _open_file(self, path: Path) -> None:
        os.startfile(path)  # type: ignore[attr-defined]

    def _save(self) -> None:
        prefs = self.master_app.preferences
        prefs.caption_style = self.caption_style.get()
        prefs.caption_words = int(round(self.caption_words.get()))
        prefs.part_length = self.part_length.get()
        prefs.profanity_mode = self.profanity.get()
        prefs.whisper_model = self.whisper_model.get()
        prefs.align_captions = self.align_var.get()
        prefs.scene_aware = self.scene_var.get()
        prefs.auto_music = self.music_var.get()
        prefs.smart_match_background = self.smart_match_var.get()
        prefs.audio_polish = self.audio_polish_var.get()
        prefs.bake_tiktok_cover = self.cover_var.get()
        prefs.duplicate_check = self.duplicate_var.get()
        prefs.desktop_notifications = self.notifications_var.get()
        prefs.voice_speed = round(self.speed.get(), 2)
        prefs.music_volume = self.music_volume.get() / 100.0
        save_preferences(prefs)
        self.master_app._log("Production settings saved")
        self.destroy()


class PreviewDialog(ctk.CTkToplevel):
    def __init__(self, master: "AutoTokApp", post: StoryPost):
        super().__init__(master)
        self.master_app = master
        self.post = post
        self.title("Safe-zone caption preview")
        self.geometry("520x910")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["window"])
        self.transient(master)
        self.grab_set()
        self.preview_path = APP_DIR / ".autotok" / "preview.jpg"
        videos = scan_videos(master.preferences.video_category)
        self.background = videos[0] if videos else None

        ctk.CTkLabel(self, text="Drag the preview to move captions", text_color=COLORS["text"], font=ctk.CTkFont(size=19, weight="bold")).pack(pady=(16, 3))
        ctk.CTkLabel(self, text="The gold box shows the TikTok interface-safe area.", text_color=COLORS["muted"], font=ctk.CTkFont(size=11)).pack(pady=(0, 8))
        self.image_label = ctk.CTkLabel(self, text="")
        self.image_label.pack()
        self.image_label.bind("<ButtonRelease-1>", self._drag_position)
        self.position_label = ctk.CTkLabel(self, text="", text_color=COLORS["muted"])
        self.position_label.pack(pady=(8, 2))
        self.style_var = ctk.StringVar(value=master.preferences.caption_style)
        ctk.CTkSegmentedButton(
            self, values=["Karaoke", "Purple Pop", "Classic"], variable=self.style_var,
            command=lambda _value: self._render(), selected_color=COLORS["accent"],
        ).pack(fill="x", padx=58, pady=(2, 8))
        self.slider = ctk.CTkSlider(self, from_=0.35, to=0.76, number_of_steps=41, progress_color=COLORS["accent"], command=self._slider_changed)
        self.slider.set(master.preferences.caption_position)
        self.slider.pack(fill="x", padx=58, pady=(0, 10))
        ctk.CTkButton(self, text="Use this position", fg_color=COLORS["accent"], command=self._save).pack(pady=(0, 14))
        self._render()

    def _render(self) -> None:
        position = self.slider.get()
        generate_preview_image(
            self.post, self.preview_path, self.background, position,
            self.style_var.get(), True,
        )
        image = Image.open(self.preview_path)
        self.preview_image = ctk.CTkImage(light_image=image, dark_image=image, size=(405, 720))
        self.image_label.configure(image=self.preview_image)
        self.position_label.configure(text=f"Caption position: {position * 100:.0f}% from top")

    def _slider_changed(self, _value: float) -> None:
        if hasattr(self, "_refresh_job"):
            try:
                self.after_cancel(self._refresh_job)
            except Exception:
                pass
        self._refresh_job = self.after(180, self._render)

    def _drag_position(self, event) -> None:
        position = max(0.35, min(0.76, event.y / 720.0))
        self.slider.set(position)
        self._render()

    def _save(self) -> None:
        self.master_app.preferences.caption_position = round(self.slider.get(), 3)
        self.master_app.preferences.caption_style = self.style_var.get()
        save_preferences(self.master_app.preferences)
        if hasattr(self.master_app, "position_slider"):
            self.master_app.position_slider.set(self.master_app.preferences.caption_position)
            self.master_app._caption_position_changed(self.master_app.preferences.caption_position)
        self.destroy()


class ListDialog(ctk.CTkToplevel):
    def __init__(self, master: "AutoTokApp", mode: str):
        super().__init__(master)
        self.master_app = master
        self.mode = mode
        self.title("Render queue" if mode == "queue" else "Render history")
        self.geometry("720x560")
        self.configure(fg_color=COLORS["window"])
        ctk.CTkLabel(self, text=self.title(), text_color=COLORS["text"], font=ctk.CTkFont(size=24, weight="bold")).pack(anchor="w", padx=24, pady=(22, 12))
        self.body = ctk.CTkScrollableFrame(self, fg_color=COLORS["panel"])
        self.body.pack(fill="both", expand=True, padx=24, pady=(0, 14))
        self._refresh()
        if mode == "queue":
            ctk.CTkButton(self, text="Clear queue", fg_color=COLORS["danger"], command=self._clear).pack(pady=(0, 18))

    def _refresh(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        items = self.master_app.render_queue if self.mode == "queue" else load_history()
        if not items:
            ctk.CTkLabel(self.body, text="Nothing here yet.", text_color=COLORS["muted"]).pack(pady=30)
            return
        for index, item in enumerate(items):
            title = item.title if self.mode == "queue" else item.get("title", "Untitled")
            detail = f"{item.subreddit} • {item.word_count} words" if self.mode == "queue" else f"{item.get('created_at', '')} • {item.get('status', '')}"
            row = ctk.CTkFrame(self.body, fg_color=COLORS["panel_2"], corner_radius=10)
            row.pack(fill="x", padx=8, pady=5)
            text = ctk.CTkFrame(row, fg_color="transparent")
            text.pack(side="left", fill="x", expand=True, padx=12, pady=9)
            ctk.CTkLabel(text, text=title[:80], anchor="w", text_color=COLORS["text"], font=ctk.CTkFont(size=12, weight="bold")).pack(fill="x")
            ctk.CTkLabel(text, text=detail, anchor="w", text_color=COLORS["muted"], font=ctk.CTkFont(size=10)).pack(fill="x")
            if self.mode == "queue":
                ctk.CTkButton(row, text="Remove", width=72, fg_color="transparent", hover_color=COLORS["danger"], command=lambda i=index: self._remove(i)).pack(side="right", padx=10)
            else:
                outputs = [Path(path) for path in item.get("outputs", []) if Path(path).exists()]
                if outputs:
                    ctk.CTkButton(row, text="Review", width=72, fg_color=COLORS["accent"], command=lambda files=outputs: self.master_app._open_publish(files)).pack(side="right", padx=10)

    def _remove(self, index: int) -> None:
        self.master_app.render_queue.pop(index)
        self.master_app._save_queue_and_refresh()
        self._refresh()

    def _clear(self) -> None:
        self.master_app.render_queue.clear()
        self.master_app._save_queue_and_refresh()
        self._refresh()


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, master: "AutoTokApp", current: OpenRouterConfig):
        super().__init__(master)
        self.master_app = master
        self.title("Voice & API settings")
        self.geometry("620x760")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["window"])
        self.transient(master)
        self.grab_set()

        ctk.CTkLabel(
            self, text="Voice & API settings", font=ctk.CTkFont(size=24, weight="bold"),
            text_color=COLORS["text"]
        ).pack(anchor="w", padx=30, pady=(28, 5))
        ctk.CTkLabel(
            self,
            text="Choose OpenRouter Flux or Azure Speech. Keys are stored in Windows Credential Manager when available.",
            justify="left", text_color=COLORS["muted"], font=ctk.CTkFont(size=13)
        ).pack(anchor="w", padx=30, pady=(0, 20))

        self.provider_var = ctk.StringVar(value="Azure Speech" if current.provider == "azure" else "OpenRouter Flux")
        ctk.CTkSegmentedButton(
            self, values=["OpenRouter Flux", "Azure Speech"], variable=self.provider_var,
            selected_color=COLORS["accent"], selected_hover_color=COLORS["accent_hover"],
        ).pack(fill="x", padx=30, pady=(0, 12))
        form = ctk.CTkScrollableFrame(self, fg_color=COLORS["panel"], corner_radius=14, border_width=1, border_color=COLORS["border"], height=420)
        form.pack(fill="x", padx=30)
        self.key_entry = self._field(form, "OPENROUTER API KEY", current.api_key, show="•")
        self.model_entry = self._field(form, "FLUX MODEL", current.model)
        self.voice_entry = self._field(form, "FLUX VOICE ID", current.voice)
        self.azure_key_entry = self._field(form, "AZURE SPEECH KEY", current.azure_key, show="•")
        self.azure_region_entry = self._field(form, "AZURE REGION", current.azure_region)
        self.azure_voice_entry = self._field(form, "AZURE VOICE", current.azure_voice)
        source_text = f"Loaded from: {current.source}" if current.source else "No saved key detected"
        ctk.CTkLabel(form, text=source_text, text_color=COLORS["muted"], font=ctk.CTkFont(size=11), wraplength=470).pack(anchor="w", padx=20, pady=(0, 18))

        voices = ctk.CTkFrame(self, fg_color="transparent")
        voices.pack(fill="x", padx=30, pady=(12, 0))
        ctk.CTkLabel(voices, text="ALL FLUX VOICES", text_color=COLORS["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(side="left")
        self.voice_picker = ctk.CTkOptionMenu(
            voices,
            values=list(FLUX_VOICES),
            command=self._pick_voice,
            fg_color=COLORS["panel_2"], button_color=COLORS["accent"],
        )
        self.voice_picker.set(valid_flux_voice(current.voice))
        self.voice_picker.pack(side="right")
        self.test_status = ctk.CTkLabel(self, text="", text_color=COLORS["muted"], font=ctk.CTkFont(size=11))
        self.test_status.pack(anchor="w", padx=30, pady=(8, 0))

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=30, pady=24)
        ctk.CTkButton(actions, text="Get an OpenRouter key", fg_color="transparent", border_width=1, border_color=COLORS["border"], command=lambda: webbrowser.open("https://openrouter.ai/settings/keys")).pack(side="left")
        ctk.CTkButton(actions, text="Test voice", fg_color=COLORS["panel_2"], command=self._test_voice).pack(side="left", padx=8)
        ctk.CTkButton(actions, text="Save settings", fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"], command=self._save).pack(side="right")

    def _field(self, parent, label: str, value: str, show: str = "") -> ctk.CTkEntry:
        ctk.CTkLabel(parent, text=label, text_color=COLORS["muted"], font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w", padx=20, pady=(16, 6))
        entry = ctk.CTkEntry(parent, height=40, fg_color=COLORS["panel_2"], border_color=COLORS["border"], text_color=COLORS["text"], show=show)
        entry.pack(fill="x", padx=20)
        entry.insert(0, value)
        return entry

    def _save(self) -> None:
        config = OpenRouterConfig(
            provider="azure" if self.provider_var.get() == "Azure Speech" else "flux",
            api_key=self.key_entry.get().strip(),
            model=self.model_entry.get().strip(),
            voice=self.voice_entry.get().strip(),
            azure_key=self.azure_key_entry.get().strip(),
            azure_region=self.azure_region_entry.get().strip(),
            azure_voice=self.azure_voice_entry.get().strip(),
        )
        if not config.configured:
            messagebox.showwarning("Missing voice settings", "Complete the selected provider, or cancel to keep using the Windows voice fallback.", parent=self)
            return
        save_openrouter_config(config)
        self.master_app.tts_config = load_openrouter_config()
        self.master_app.update_key_badge()
        self.destroy()

    def _pick_voice(self, voice: str) -> None:
        self.voice_entry.delete(0, "end")
        self.voice_entry.insert(0, voice)

    def _test_voice(self) -> None:
        config = OpenRouterConfig(
            provider="azure" if self.provider_var.get() == "Azure Speech" else "flux",
            api_key=self.key_entry.get().strip(), model=self.model_entry.get().strip(),
            voice=self.voice_entry.get().strip(),
            azure_key=self.azure_key_entry.get().strip(), azure_region=self.azure_region_entry.get().strip(),
            azure_voice=self.azure_voice_entry.get().strip(),
        )
        if not config.configured:
            self.test_status.configure(text="Enter an API key first.", text_color=COLORS["danger"])
            return
        self.test_status.configure(text="Generating voice preview…", text_color=COLORS["muted"])

        def worker() -> None:
            try:
                preview_dir = APP_DIR / ".autotok" / "voice_previews"
                preview_dir.mkdir(parents=True, exist_ok=True)
                path, _engine = synthesize_speech(
                    "Here is your AutoTok voice preview.", config,
                    preview_dir / config.voice, allow_local_fallback=False,
                    speed=self.master_app.preferences.voice_speed,
                )
                self.after(0, lambda: self._voice_test_done(path, None))
            except Exception as exc:
                self.after(0, lambda: self._voice_test_done(None, str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _voice_test_done(self, path: Path | None, error: str | None) -> None:
        if error:
            self.test_status.configure(text=error, text_color=COLORS["danger"])
        elif path:
            self.test_status.configure(text="Voice preview ready and playing.", text_color=COLORS["success"])
            os.startfile(path)  # type: ignore[attr-defined]


class StudioDialog(ctk.CTkToplevel):
    """One home for infrequent controls, keeping the main creator uncluttered."""

    def __init__(self, master: "AutoTokApp"):
        super().__init__(master)
        self.master_app = master
        self.title("AutoTok Studio")
        self.geometry("850x650")
        self.minsize(760, 580)
        self.configure(fg_color=COLORS["window"])
        self.transient(master)

        ctk.CTkLabel(self, text="Studio", text_color=COLORS["text"], font=ctk.CTkFont(size=26, weight="bold")).pack(anchor="w", padx=26, pady=(22, 4))
        ctk.CTkLabel(self, text="Queue, presets, analytics, assets, and publishing setup live here.", text_color=COLORS["muted"]).pack(anchor="w", padx=26, pady=(0, 12))
        self.tabs = ctk.CTkTabview(self, fg_color=COLORS["panel"], segmented_button_selected_color=COLORS["accent"])
        self.tabs.pack(fill="both", expand=True, padx=24, pady=(0, 22))
        for name in ("Queue", "Presets", "Analytics", "Library", "Publishing"):
            self.tabs.add(name)
        self._build_queue()
        self._build_presets()
        self._build_analytics()
        self._build_library()
        self._build_publishing()

    def _clear(self, parent) -> None:
        for child in parent.winfo_children():
            child.destroy()

    def _build_queue(self) -> None:
        tab = self.tabs.tab("Queue")
        self._clear(tab)
        render_count = len(self.master_app.render_queue)
        publish_items = load_publish_queue()
        ctk.CTkLabel(tab, text=f"Render queue  •  {render_count}", text_color=COLORS["text"], font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=16, pady=(16, 6))
        ctk.CTkButton(tab, text="Open render queue", width=160, fg_color=COLORS["panel_2"], command=lambda: ListDialog(self.master_app, "queue")).pack(anchor="w", padx=16)
        draft_row = ctk.CTkFrame(tab, fg_color="transparent")
        draft_row.pack(fill="x", padx=16, pady=(8, 2))
        ctk.CTkButton(draft_row, text="Save editor as draft", width=160, fg_color=COLORS["panel_2"], command=self._save_draft).pack(side="left")
        drafts = load_drafts()
        if drafts:
            labels = [f"{item.get('post', {}).get('title', 'Untitled')[:55]}  •  {item.get('saved_at', '')}" for item in drafts]
            self.draft_map = dict(zip(labels, drafts))
            self.draft_var = ctk.StringVar(value=labels[0])
            ctk.CTkOptionMenu(draft_row, values=labels, variable=self.draft_var, fg_color=COLORS["panel_2"], button_color=COLORS["accent"]).pack(side="left", fill="x", expand=True, padx=8)
            ctk.CTkButton(draft_row, text="Load", width=70, fg_color=COLORS["accent"], command=self._load_draft).pack(side="right")
        ctk.CTkLabel(tab, text=f"Publish queue  •  {len(publish_items)}", text_color=COLORS["text"], font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=16, pady=(20, 6))
        body = ctk.CTkScrollableFrame(tab, fg_color=COLORS["panel_2"], height=300)
        body.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        if not publish_items:
            ctk.CTkLabel(body, text="Approved, private-test, scheduled, and published videos will appear here.", text_color=COLORS["muted"]).pack(pady=26)
        for item in publish_items:
            row = ctk.CTkFrame(body, fg_color=COLORS["panel"])
            row.pack(fill="x", pady=4)
            ctk.CTkLabel(row, text=Path(item.get("video", "Video")).name, anchor="w", text_color=COLORS["text"], font=ctk.CTkFont(weight="bold")).pack(side="left", fill="x", expand=True, padx=10, pady=9)
            ctk.CTkLabel(row, text=str(item.get("status", "unknown")).upper(), text_color=COLORS["success"] if item.get("status") in {"published", "private-test"} else COLORS["muted"]).pack(side="right", padx=10)

    def _save_draft(self) -> None:
        post = self.master_app._current_editor_post()
        if not post:
            return
        save_draft(post, self.master_app.preferences)
        self.master_app._log(f"Draft saved: {post.title[:55]}")
        self._build_queue()

    def _load_draft(self) -> None:
        item = getattr(self, "draft_map", {}).get(self.draft_var.get())
        if not item:
            return
        try:
            post = StoryPost(**item["post"])
            preference_values = {key: getattr(AppPreferences(), key) for key in AppPreferences.__dataclass_fields__}
            preference_values.update({
                key: value for key, value in item.get("preferences", {}).items()
                if key in AppPreferences.__dataclass_fields__
            })
            self.master_app.preferences = AppPreferences(**preference_values)
        except (KeyError, TypeError):
            messagebox.showerror("Draft", "This draft could not be read.", parent=self)
            return
        self.master_app._show_post(post, "Draft loaded")
        self.master_app._apply_preferences_to_ui()
        save_preferences(self.master_app.preferences)
        self.destroy()

    def _build_presets(self) -> None:
        tab = self.tabs.tab("Presets")
        ctk.CTkLabel(tab, text="One-click production recipes", text_color=COLORS["text"], font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=18, pady=(18, 4))
        ctk.CTkLabel(tab, text="Presets replace a wall of switches on the creator screen.", text_color=COLORS["muted"]).pack(anchor="w", padx=18, pady=(0, 14))
        self.preset_var = ctk.StringVar(value=self.master_app.preferences.active_preset)
        self.preset_menu = ctk.CTkOptionMenu(tab, values=list(load_presets()), variable=self.preset_var, fg_color=COLORS["panel_2"], button_color=COLORS["accent"])
        self.preset_menu.pack(fill="x", padx=18)
        ctk.CTkButton(tab, text="Apply preset", fg_color=COLORS["accent"], command=self._apply_preset).pack(anchor="w", padx=18, pady=10)
        ctk.CTkFrame(tab, height=1, fg_color=COLORS["border"]).pack(fill="x", padx=18, pady=12)
        self.preset_name = ctk.CTkEntry(tab, placeholder_text="My production preset", fg_color=COLORS["panel_2"], border_color=COLORS["border"])
        self.preset_name.pack(fill="x", padx=18)
        ctk.CTkButton(tab, text="Save current settings as preset", fg_color=COLORS["panel_2"], command=self._save_preset).pack(anchor="w", padx=18, pady=10)

    def _apply_preset(self) -> None:
        name = self.preset_var.get()
        self.master_app.preferences = apply_preset(self.master_app.preferences, name)
        self.master_app.preferences.active_preset = name
        save_preferences(self.master_app.preferences)
        self.master_app._apply_preferences_to_ui()
        self.master_app._log(f"Applied preset: {name}")
        messagebox.showinfo("Preset applied", f"{name} is now active.", parent=self)

    def _save_preset(self) -> None:
        name = self.preset_name.get().strip()
        if not name:
            messagebox.showwarning("Preset name", "Give this preset a short name.", parent=self)
            return
        save_preset(name, self.master_app.preferences)
        self.preset_menu.configure(values=list(load_presets()))
        self.preset_var.set(name)

    def _build_analytics(self) -> None:
        tab = self.tabs.tab("Analytics")
        self.analytics_body = ctk.CTkFrame(tab, fg_color="transparent")
        self.analytics_body.pack(fill="both", expand=True)
        self._refresh_analytics()

    def _refresh_analytics(self) -> None:
        body = self.analytics_body
        self._clear(body)
        summary = analytics_summary()
        ctk.CTkLabel(body, text="Performance dashboard", text_color=COLORS["text"], font=ctk.CTkFont(size=17, weight="bold")).pack(anchor="w", padx=18, pady=(18, 4))
        ctk.CTkLabel(body, text=f"{summary['rows']} videos  •  {summary['views']:,} views  •  {summary['engagement']:.2f}% engagement", text_color=COLORS["success"]).pack(anchor="w", padx=18)
        ctk.CTkButton(body, text="Import TikTok CSV", fg_color=COLORS["accent"], command=self._import_analytics).pack(anchor="w", padx=18, pady=14)
        ctk.CTkLabel(body, text="TOP VIDEOS", text_color=COLORS["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=18, pady=(8, 5))
        for row in summary["top"]:
            title = row.get("video_title") or row.get("title") or row.get("video_link") or "Untitled video"
            views = row.get("views") or row.get("video_views") or "0"
            ctk.CTkLabel(body, text=f"{views} views  •  {str(title)[:80]}", anchor="w", text_color=COLORS["text"]).pack(fill="x", padx=18, pady=3)

    def _import_analytics(self) -> None:
        selected = filedialog.askopenfilename(title="Import TikTok analytics", filetypes=[("CSV files", "*.csv")])
        if selected:
            try:
                count = import_analytics_csv(Path(selected))
                self._refresh_analytics()
                self.master_app._log(f"Imported {count} analytics rows")
            except Exception as exc:
                messagebox.showerror("Analytics import failed", str(exc), parent=self)

    def _build_library(self) -> None:
        tab = self.tabs.tab("Library")
        videos, tracks = scan_videos(), scan_music()
        ctk.CTkLabel(tab, text="Asset library", text_color=COLORS["text"], font=ctk.CTkFont(size=17, weight="bold")).pack(anchor="w", padx=18, pady=(18, 4))
        ctk.CTkLabel(tab, text=f"{len(videos)} background videos  •  {len(tracks)} music tracks  •  {len(video_categories()) - 1} categories", text_color=COLORS["muted"]).pack(anchor="w", padx=18, pady=(0, 14))
        ctk.CTkButton(tab, text="Open videos folder", fg_color=COLORS["panel_2"], command=lambda: os.startfile(VIDEO_DIR)).pack(anchor="w", padx=18, pady=5)  # type: ignore[attr-defined]
        ctk.CTkButton(tab, text="Open music folder", fg_color=COLORS["panel_2"], command=lambda: os.startfile(MUSIC_DIR)).pack(anchor="w", padx=18, pady=5)  # type: ignore[attr-defined]
        ctk.CTkLabel(tab, text="Tip: name folders Minecraft, Racing, Cooking, Nature, or Satisfying.\nAutoTok can match those categories to each story automatically.", justify="left", text_color=COLORS["muted"]).pack(anchor="w", padx=18, pady=18)

    def _build_publishing(self) -> None:
        tab = self.tabs.tab("Publishing")
        self.publish_status = ctk.CTkLabel(tab, text="", justify="left", text_color=COLORS["muted"], wraplength=690)
        self.publish_status.pack(anchor="w", padx=18, pady=(18, 10))
        ctk.CTkButton(tab, text="Set up / update community uploader", fg_color=COLORS["accent"], command=self._setup_uploader).pack(anchor="w", padx=18)
        ctk.CTkLabel(tab, text="Experimental: this third-party uploader controls a visible Chromium browser. AutoTok disables optional stealth mode and never stores your TikTok password. You handle login and any account challenge yourself.", justify="left", wraplength=690, text_color=COLORS["orange"]).pack(anchor="w", padx=18, pady=16)
        ctk.CTkButton(tab, text="Production settings", fg_color=COLORS["panel_2"], command=lambda: AdvancedSettingsDialog(self.master_app)).pack(anchor="w", padx=18, pady=5)
        self._refresh_uploader_status()

    def _refresh_uploader_status(self) -> None:
        status = uploader_status()
        state = f"Ready • tiktokautouploader {status['version']}" if status["ready"] else ("Package installed; Chromium setup incomplete" if status["installed"] else "Not installed")
        node = "Node.js detected" if status["node"] else "Node.js missing"
        self.publish_status.configure(text=f"Community uploader: {state}\n{node}\nIsolated Python: {status['python']}", text_color=COLORS["success"] if status["ready"] else COLORS["muted"])

    def _setup_uploader(self) -> None:
        if self.master_app.busy:
            return
        self.master_app._set_busy(True, "Setting up community uploader…")
        self.publish_status.configure(text="Preparing isolated uploader environment…", text_color=COLORS["muted"])

        def worker() -> None:
            try:
                setup_uploader(lambda message: self.master_app.events.put(("log", message)))
                self.master_app.events.put(("uploader_setup_done", self))
            except Exception as exc:
                self.master_app.events.put(("error", "Uploader setup failed", str(exc)))
            finally:
                self.master_app.events.put(("idle",))

        threading.Thread(target=worker, daemon=True).start()


class PublishDialog(ctk.CTkToplevel):
    def __init__(self, master: "AutoTokApp", outputs: list[Path]):
        super().__init__(master)
        self.master_app = master
        self.outputs = [path for path in outputs if path.exists()]
        self.title("Review & Publish")
        self.geometry("820x780")
        self.minsize(740, 680)
        self.configure(fg_color=COLORS["window"])
        self.transient(master)
        if not self.outputs:
            self.after(10, self.destroy)
            return

        ctk.CTkLabel(self, text="Review & Publish", text_color=COLORS["text"], font=ctk.CTkFont(size=25, weight="bold")).pack(anchor="w", padx=26, pady=(22, 3))
        ctk.CTkLabel(self, text="Nothing leaves this computer until you approve it here.", text_color=COLORS["muted"]).pack(anchor="w", padx=26, pady=(0, 12))
        self.video_var = ctk.StringVar(value=str(self.outputs[0]))
        ctk.CTkOptionMenu(self, values=[str(path) for path in self.outputs], variable=self.video_var, command=lambda _v: self._load_video(), fg_color=COLORS["panel_2"], button_color=COLORS["accent"]).pack(fill="x", padx=26)
        self.tabs = ctk.CTkTabview(self, fg_color=COLORS["panel"], segmented_button_selected_color=COLORS["accent"])
        self.tabs.pack(fill="both", expand=True, padx=24, pady=12)
        for name in ("Review", "Publish", "Advanced"):
            self.tabs.add(name)
        self._build_review()
        self._build_publish()
        self._build_advanced()
        self._load_video()

    @property
    def video(self) -> Path:
        return Path(self.video_var.get())

    def _build_review(self) -> None:
        tab = self.tabs.tab("Review")
        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.pack(fill="x", padx=14, pady=(14, 8))
        ctk.CTkButton(actions, text="Play video", fg_color=COLORS["accent"], command=lambda: os.startfile(self.video)).pack(side="left")  # type: ignore[attr-defined]
        ctk.CTkButton(actions, text="Preview hook voice", fg_color=COLORS["panel_2"], command=self._preview_hook_voice).pack(side="left", padx=8)
        ctk.CTkButton(actions, text="Open captions", fg_color=COLORS["panel_2"], command=self._open_captions).pack(side="left")
        ctk.CTkLabel(tab, text="HOOK VARIANT", text_color=COLORS["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=14, pady=(10, 5))
        self.hook_var = ctk.StringVar(value="")
        self.hook_menu = ctk.CTkOptionMenu(tab, values=["Loading…"], variable=self.hook_var, fg_color=COLORS["panel_2"], button_color=COLORS["accent"])
        self.hook_menu.pack(fill="x", padx=14)
        ctk.CTkLabel(tab, text="PUBLISH DESCRIPTION", text_color=COLORS["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=14, pady=(14, 5))
        self.description = ctk.CTkTextbox(tab, height=120, fg_color=COLORS["panel_2"], border_width=1, border_color=COLORS["border"])
        self.description.pack(fill="x", padx=14)
        ctk.CTkLabel(tab, text="HASHTAGS", text_color=COLORS["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=14, pady=(14, 5))
        self.hashtags = ctk.CTkEntry(tab, fg_color=COLORS["panel_2"], border_color=COLORS["border"])
        self.hashtags.pack(fill="x", padx=14)
        self.preflight_label = ctk.CTkLabel(tab, text="", justify="left", anchor="w", text_color=COLORS["muted"], font=ctk.CTkFont(family="Consolas", size=11))
        self.preflight_label.pack(fill="x", padx=14, pady=14)

    def _build_publish(self) -> None:
        tab = self.tabs.tab("Publish")
        self.provider = ctk.StringVar(value=self.master_app.preferences.default_publish_provider)
        self.privacy = ctk.StringVar(value=self.master_app.preferences.default_privacy)
        ctk.CTkLabel(tab, text="DESTINATION", text_color=COLORS["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=14, pady=(14, 5))
        ctk.CTkOptionMenu(tab, values=["Export only", "Community uploader"], variable=self.provider, fg_color=COLORS["panel_2"], button_color=COLORS["accent"]).pack(fill="x", padx=14)
        ctk.CTkLabel(tab, text="MODE", text_color=COLORS["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=14, pady=(14, 5))
        ctk.CTkOptionMenu(tab, values=["Private test", "Public upload"], variable=self.privacy, fg_color=COLORS["panel_2"], button_color=COLORS["accent"]).pack(fill="x", padx=14)
        self.account = ctk.CTkEntry(tab, placeholder_text="TikTok account/profile name", fg_color=COLORS["panel_2"], border_color=COLORS["border"])
        self.account.pack(fill="x", padx=14, pady=(14, 0))
        self.account.insert(0, self.master_app.preferences.tiktok_account)
        self.schedule = ctk.CTkEntry(tab, placeholder_text="Optional schedule: 2026-09-14 18:30", fg_color=COLORS["panel_2"], border_color=COLORS["border"])
        self.schedule.pack(fill="x", padx=14, pady=10)
        ctk.CTkLabel(tab, text="Private test saves an approval package and opens the video locally. Public upload opens a visible TikTok browser only after final confirmation.", wraplength=670, justify="left", text_color=COLORS["muted"]).pack(anchor="w", padx=14, pady=4)

        self.source_rights = ctk.BooleanVar(value=False)
        self.footage_rights = ctk.BooleanVar(value=False)
        self.music_rights = ctk.BooleanVar(value=False)
        for text, variable in (
            ("I reviewed the Reddit source and may use this story", self.source_rights),
            ("I have permission or a licence for the footage", self.footage_rights),
            ("I have permission or a licence for the music/voice", self.music_rights),
        ):
            ctk.CTkCheckBox(tab, text=text, variable=variable, fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"]).pack(anchor="w", padx=14, pady=5)
        self.action_button = ctk.CTkButton(tab, text="APPROVE PRIVATE TEST", height=46, fg_color=COLORS["orange"], hover_color="#E85239", command=self._approve)
        self.action_button.pack(fill="x", padx=14, pady=16)
        self.privacy.trace_add("write", lambda *_: self.action_button.configure(text="APPROVE PRIVATE TEST" if self.privacy.get() == "Private test" else "REVIEW & UPLOAD"))

    def _build_advanced(self) -> None:
        tab = self.tabs.tab("Advanced")
        self.copyright_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(tab, text="Run TikTok copyright check", variable=self.copyright_var, progress_color=COLORS["accent"]).pack(anchor="w", padx=14, pady=(18, 8))
        self.sound = ctk.CTkEntry(tab, placeholder_text="Optional TikTok sound name", fg_color=COLORS["panel_2"], border_color=COLORS["border"])
        self.sound.pack(fill="x", padx=14, pady=8)
        self.sound_mode = ctk.StringVar(value="search")
        ctk.CTkOptionMenu(tab, values=["search", "favorites"], variable=self.sound_mode, fg_color=COLORS["panel_2"], button_color=COLORS["accent"]).pack(fill="x", padx=14, pady=8)
        ctk.CTkLabel(tab, text="Browser visibility and challenge handling are intentionally fixed: visible window, no optional stealth mode, and you complete login/CAPTCHA yourself.", wraplength=670, justify="left", text_color=COLORS["orange"]).pack(anchor="w", padx=14, pady=18)

    def _open_captions(self) -> None:
        path = self.video.with_suffix(".srt")
        if path.exists():
            os.startfile(path)  # type: ignore[attr-defined]

    def _preview_hook_voice(self) -> None:
        hook = self.hook_var.get().split(" • ", 1)[-1].strip()
        if not hook:
            return
        self.preflight_label.configure(text="Generating a short hook voice preview…", text_color=COLORS["muted"])

        def worker() -> None:
            try:
                preview_dir = APP_DIR / ".autotok" / "hook_previews"
                preview_dir.mkdir(parents=True, exist_ok=True)
                path, _engine = synthesize_speech(
                    hook, self.master_app.tts_config, preview_dir / "selected_hook",
                    speed=self.master_app.preferences.voice_speed,
                )
                self.after(0, lambda preview=path: os.startfile(preview))  # type: ignore[attr-defined]
                self.after(0, self._load_video)
            except Exception as exc:
                self.after(0, lambda error=str(exc): messagebox.showerror("Hook preview failed", error, parent=self))

        threading.Thread(target=worker, daemon=True).start()

    def _load_video(self) -> None:
        data = load_post_package(self.video)
        hooks = data.get("hook_variants") or []
        if not hooks:
            post = self.master_app._current_editor_post(show_warning=False)
            hooks = generate_hook_variants(post) if post else [{"text": data.get("hook", "Watch until the end…"), "score": 50}]
        choices = [f"{item.get('score', 0)} • {item.get('text', '')}" for item in hooks]
        self.hook_menu.configure(values=choices or ["No hook"])
        self.hook_var.set(choices[0] if choices else "No hook")
        self.description.delete("1.0", "end")
        self.description.insert("1.0", data.get("description", ""))
        self.hashtags.delete(0, "end")
        self.hashtags.insert(0, " ".join(data.get("hashtags", [])))
        series = data.get("series", {}) if isinstance(data.get("series"), dict) else {}
        part = int(series.get("part", 1) or 1)
        total = int(series.get("total", 1) or 1)
        if total > 1:
            suggested = datetime.now() + timedelta(minutes=15, hours=(part - 1) * self.master_app.preferences.schedule_spacing_hours)
            if suggested - datetime.now() <= timedelta(days=10):
                self.schedule.delete(0, "end")
                self.schedule.insert(0, suggested.strftime("%Y-%m-%d %H:%M"))
        result = preflight_video(self.video, self.master_app.preferences.caption_position)
        symbols = {"pass": "✓", "warning": "!", "error": "×"}
        lines = [f"{symbols[item['level']]} {item['label']}: {item['detail']}" for item in result["checks"]]
        headline = "READY TO PUBLISH" if result["ok"] else "FIX REQUIRED"
        self.preflight_label.configure(text=f"{headline}\n" + "\n".join(lines), text_color=COLORS["success"] if result["ok"] else COLORS["danger"])
        self.preflight = result

    def _payload(self) -> dict:
        schedule = self.schedule.get().strip()
        if schedule:
            schedule = datetime.fromisoformat(schedule).isoformat(timespec="minutes")
            delta = datetime.fromisoformat(schedule) - datetime.now()
            if delta.total_seconds() <= 0 or delta.total_seconds() > 10 * 24 * 3600:
                raise ValueError("Schedule must be in the future and no more than 10 days away.")
        hook = self.hook_var.get().split(" • ", 1)[-1]
        tags = [tag if tag.startswith("#") else f"#{tag}" for tag in re.split(r"[\s,]+", self.hashtags.get().strip()) if tag]
        package = load_post_package(self.video)
        description = self.description.get("1.0", "end-1c").strip()
        if hook and not description.lower().startswith(hook.lower()):
            description = f"{hook}\n\n{description}".strip()
        return {
            **package,
            "video": str(self.video.resolve()),
            "description": description,
            "hashtags": tags,
            "hook": hook,
            "account": self.account.get().strip(),
            "schedule": schedule,
            "cover": package.get("thumbnail", ""),
            "copyright_check": self.copyright_var.get(),
            "sound": self.sound.get().strip(),
            "sound_search_mode": self.sound_mode.get(),
            "privacy_mode": self.privacy.get(),
            "provider": self.provider.get(),
        }

    def _approve(self) -> None:
        if not self.preflight.get("ok"):
            messagebox.showerror("Preflight failed", "Fix the failed media checks before publishing.", parent=self)
            return
        if not all((self.source_rights.get(), self.footage_rights.get(), self.music_rights.get())):
            messagebox.showwarning("Rights confirmation", "Confirm the story, footage, and audio rights before approval.", parent=self)
            return
        try:
            payload = self._payload()
        except ValueError as exc:
            messagebox.showwarning("Schedule", str(exc), parent=self)
            return
        save_post_package(self.video, payload)
        save_rights_record(self.video, {
            "source": payload.get("source", ""), "story_confirmed": True,
            "footage_confirmed": True, "music_voice_confirmed": True,
        })
        self.master_app.preferences.tiktok_account = payload["account"]
        self.master_app.preferences.default_privacy = self.privacy.get()
        self.master_app.preferences.default_publish_provider = self.provider.get()
        save_preferences(self.master_app.preferences)

        if self.privacy.get() == "Private test":
            package = create_private_test(payload)
            enqueue_publish(payload, "private-test")
            self.master_app._log(f"Approval package saved: {package.name}")
            os.startfile(self.video)  # type: ignore[attr-defined]
            messagebox.showinfo("Private test ready", "Approval package saved. The video is opening for your final local review.", parent=self)
            self.destroy()
            return
        if self.provider.get() == "Export only":
            enqueue_publish(payload, "approved")
            self.master_app._log(f"Export approved: {self.video.name}")
            messagebox.showinfo("Export approved", "The posting package is saved beside the video and marked approved in Studio.", parent=self)
            self.destroy()
            return

        if not payload["account"]:
            messagebox.showwarning("TikTok account", "Enter the account/profile name used to store the browser session.", parent=self)
            return
        duplicate = duplicate_published_video(self.video)
        if duplicate and not messagebox.askyesno("Duplicate upload", "This exact video was already sent or tested. Upload it again?", parent=self):
            return
        warning = "This uses an experimental third-party browser uploader. A visible browser will open. Review every field and complete login or any challenge yourself. Continue?"
        if not messagebox.askyesno("Final upload approval", warning, parent=self):
            return
        item = enqueue_publish(payload, "publishing")
        self.action_button.configure(state="disabled", text="OPENING TIKTOK…")
        self.master_app._start_publish(item, self)


class AutoTokApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("AutoTok — AI Story Studio")
        self.geometry("1460x900")
        self.minsize(1180, 760)
        self.configure(fg_color=COLORS["window"])
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.events: queue.Queue[tuple] = queue.Queue()
        VIDEO_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        MUSIC_DIR.mkdir(parents=True, exist_ok=True)
        migrate_plaintext_secret()
        self.preferences = load_preferences()
        self.render_queue = load_queue()
        self.background_files: list[Path] = []
        self.current_post = StoryPost("r/TIFU", "", "")
        self.tts_config = load_openrouter_config()
        self.busy = False
        self.last_outputs: list[Path] = []

        self._build_header()
        self._build_source_panel()
        self._build_editor()
        self._build_render_panel()
        self.after(100, self._drain_events)

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, height=68, corner_radius=0, fg_color=COLORS["panel"], border_width=0)
        header.grid(row=0, column=0, columnspan=3, sticky="nsew")
        header.grid_columnconfigure(1, weight=1)
        logo = ctk.CTkFrame(header, fg_color="transparent")
        logo.grid(row=0, column=0, padx=24, pady=13, sticky="w")
        ctk.CTkLabel(logo, text="●", text_color=COLORS["orange"], font=ctk.CTkFont(size=24)).pack(side="left", padx=(0, 9))
        ctk.CTkLabel(logo, text="AutoTok", text_color=COLORS["text"], font=ctk.CTkFont(size=23, weight="bold")).pack(side="left")
        ctk.CTkLabel(logo, text="STORY STUDIO", text_color=COLORS["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(side="left", padx=12, pady=(6, 0))

        self.key_badge = ctk.CTkButton(header, height=34, width=160, corner_radius=17, command=self._open_settings, font=ctk.CTkFont(size=12, weight="bold"))
        ctk.CTkButton(
            header, text="STUDIO", height=34, fg_color="transparent",
            hover_color=COLORS["panel_2"], border_width=1, border_color=COLORS["border"],
            command=lambda: StudioDialog(self), font=ctk.CTkFont(size=11, weight="bold")
        ).grid(row=0, column=2, padx=(8, 0))
        self.key_badge.grid(row=0, column=3, padx=24)
        self.update_key_badge()

    def update_key_badge(self) -> None:
        if self.tts_config.configured:
            provider = "AZURE" if self.tts_config.provider == "azure" else "FLUX"
            self.key_badge.configure(text=f"●  {provider} CONFIGURED", fg_color="#173329", hover_color="#204538", text_color=COLORS["success"])
        else:
            self.key_badge.configure(text="SET UP AI VOICE", fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"], text_color="white")

    def _section_title(self, parent, number: str, title: str, subtitle: str) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(18, 10))
        ctk.CTkLabel(row, text=number, width=30, height=30, corner_radius=15, fg_color=COLORS["accent"], text_color="white", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")
        block = ctk.CTkFrame(row, fg_color="transparent")
        block.pack(side="left", padx=10)
        ctk.CTkLabel(block, text=title, text_color=COLORS["text"], font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(block, text=subtitle, text_color=COLORS["muted"], font=ctk.CTkFont(size=11)).pack(anchor="w")

    def _build_source_panel(self) -> None:
        panel = ctk.CTkScrollableFrame(self, width=244, corner_radius=0, fg_color=COLORS["panel"])
        panel.grid(row=1, column=0, sticky="nsew")

        self._section_title(panel, "1", "Find a story", "Pull a text post from Reddit")
        ctk.CTkLabel(panel, text="SUBREDDIT", text_color=COLORS["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=20, pady=(5, 5))
        self.subreddit_entry = ctk.CTkEntry(panel, height=42, placeholder_text="TIFU", fg_color=COLORS["panel_2"], border_color=COLORS["border"])
        self.subreddit_entry.pack(fill="x", padx=20)
        self.subreddit_entry.insert(0, "TIFU")

        pair = ctk.CTkFrame(panel, fg_color="transparent")
        pair.pack(fill="x", padx=20, pady=12)
        pair.grid_columnconfigure((0, 1), weight=1)
        self.sort_var = ctk.StringVar(value="top")
        self.time_var = ctk.StringVar(value="week")
        ctk.CTkOptionMenu(pair, values=["hot", "top", "new", "rising"], variable=self.sort_var, fg_color=COLORS["panel_2"], button_color=COLORS["accent"], button_hover_color=COLORS["accent_hover"]).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        ctk.CTkOptionMenu(pair, values=["day", "week", "month", "year", "all"], variable=self.time_var, fg_color=COLORS["panel_2"], button_color=COLORS["accent"], button_hover_color=COLORS["accent_hover"]).grid(row=0, column=1, sticky="ew", padx=(5, 0))

        self.fetch_button = ctk.CTkButton(panel, text="↻  Pull random story", height=43, fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"], command=self._fetch_story, font=ctk.CTkFont(size=13, weight="bold"))
        self.fetch_button.pack(fill="x", padx=20)
        self.queue_five_button = ctk.CTkButton(panel, text="＋  Queue 5 stories", height=34, fg_color="transparent", hover_color=COLORS["panel_2"], command=self._fetch_five_stories)
        self.queue_five_button.pack(fill="x", padx=20, pady=(6, 0))
        ctk.CTkLabel(panel, text="Try: TIFU · TrueOffMyChest · PettyRevenge", justify="left", text_color=COLORS["muted"], font=ctk.CTkFont(size=10), wraplength=210).pack(anchor="w", padx=20, pady=8)

        divider = ctk.CTkFrame(panel, height=1, fg_color=COLORS["border"])
        divider.pack(fill="x", padx=20, pady=8)
        self._section_title(panel, "2", "Add footage", "Gameplay or satisfying clips")
        self.auto_video_var = ctk.BooleanVar(value=True)
        self.auto_video_switch = ctk.CTkSwitch(
            panel, text="Auto-pick from videos folder", variable=self.auto_video_var,
            command=self._toggle_auto_video, progress_color=COLORS["accent"],
            button_color="#FFFFFF", font=ctk.CTkFont(size=12, weight="bold")
        )
        self.auto_video_switch.pack(anchor="w", padx=20, pady=(0, 9))
        categories = video_categories()
        self.category_var = ctk.StringVar(value=self.preferences.video_category if self.preferences.video_category in categories else "All")
        self.category_menu = ctk.CTkOptionMenu(
            panel, values=categories, variable=self.category_var,
            command=self._category_changed, fg_color=COLORS["panel_2"],
            button_color=COLORS["accent"], button_hover_color=COLORS["accent_hover"],
        )
        self.category_menu.pack(fill="x", padx=20, pady=(0, 8))
        footage_actions = ctk.CTkFrame(panel, fg_color="transparent")
        footage_actions.pack(fill="x", padx=20)
        footage_actions.grid_columnconfigure((0, 1), weight=1)
        self.library_button = ctk.CTkButton(
            footage_actions, text="Open folder", height=38, fg_color=COLORS["panel_2"],
            hover_color=COLORS["border"], command=self._open_video_library
        )
        self.library_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.footage_button = ctk.CTkButton(
            footage_actions, text="Choose files", height=38, fg_color="transparent",
            hover_color=COLORS["panel_2"], border_width=1, border_color=COLORS["border"],
            command=self._choose_videos
        )
        self.footage_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.footage_label = ctk.CTkLabel(panel, text="", justify="left", text_color=COLORS["muted"], font=ctk.CTkFont(size=11), wraplength=245)
        self.footage_label.pack(anchor="w", padx=20, pady=7)
        self._toggle_auto_video()

        # Caption layout, timing, presets, music, and voice controls live in
        # the Studio dialog so the main screen stays focused on the edit.

    def _build_editor(self) -> None:
        center = ctk.CTkFrame(self, fg_color=COLORS["window"], corner_radius=0)
        center.grid(row=1, column=1, sticky="nsew", padx=18, pady=18)
        center.grid_rowconfigure(3, weight=1)
        center.grid_columnconfigure(0, weight=1)

        heading = ctk.CTkFrame(center, fg_color="transparent")
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ctk.CTkLabel(heading, text="Story editor", text_color=COLORS["text"], font=ctk.CTkFont(size=26, weight="bold")).pack(side="left")
        self.word_count_label = ctk.CTkLabel(heading, text="0 words", text_color=COLORS["muted"], font=ctk.CTkFont(size=12))
        self.word_count_label.pack(side="right", padx=(8, 0))
        ctk.CTkButton(heading, text="Preview", width=82, height=32, fg_color=COLORS["panel_2"], command=self._open_preview).pack(side="right", padx=4)
        ctk.CTkButton(heading, text="＋ Queue", width=82, height=32, fg_color=COLORS["accent"], command=self._add_current_to_queue).pack(side="right", padx=4)

        self.source_line = ctk.CTkLabel(center, text="Write a story or pull one from Reddit", anchor="w", text_color=COLORS["muted"], fg_color=COLORS["panel"], corner_radius=10, padx=14, height=38)
        self.source_line.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        self.title_entry = ctk.CTkEntry(center, height=54, placeholder_text="Story title…", fg_color=COLORS["panel"], border_color=COLORS["border"], border_width=1, font=ctk.CTkFont(size=17, weight="bold"))
        self.title_entry.grid(row=2, column=0, sticky="ew", pady=(0, 10))

        self.story_text = ctk.CTkTextbox(center, fg_color=COLORS["panel"], border_color=COLORS["border"], border_width=1, corner_radius=12, font=ctk.CTkFont(family="Segoe UI", size=15), wrap="word", spacing3=6, padx=18, pady=16)
        self.story_text.grid(row=3, column=0, sticky="nsew")
        self.story_text.insert("1.0", "Paste or write your story here, or pull a suitable text post from Reddit. AutoTok will dub it, time the captions, crop your background footage to 9:16, and render the finished video.")
        self.story_text.bind("<KeyRelease>", self._update_word_count)
        self._update_word_count()

    def _build_render_panel(self) -> None:
        panel = ctk.CTkFrame(self, width=288, corner_radius=0, fg_color=COLORS["panel"])
        panel.grid(row=1, column=2, sticky="nsew")
        panel.grid_propagate(False)
        ctk.CTkLabel(panel, text="Export", text_color=COLORS["text"], font=ctk.CTkFont(size=23, weight="bold")).pack(anchor="w", padx=22, pady=(24, 4))
        ctk.CTkLabel(panel, text="Ready for TikTok, Reels & Shorts", text_color=COLORS["muted"], font=ctk.CTkFont(size=11)).pack(anchor="w", padx=22, pady=(0, 18))

        settings = ctk.CTkFrame(panel, fg_color=COLORS["panel_2"], corner_radius=12)
        settings.pack(fill="x", padx=20)
        ctk.CTkLabel(settings, text="QUALITY", text_color=COLORS["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=14, pady=(14, 7))
        self.quality_var = ctk.StringVar(value="Full HD")
        ctk.CTkSegmentedButton(settings, values=["Draft", "Full HD"], variable=self.quality_var, selected_color=COLORS["accent"], selected_hover_color=COLORS["accent_hover"]).pack(fill="x", padx=14)
        ctk.CTkLabel(settings, text="FORMAT", text_color=COLORS["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=14, pady=(15, 7))
        ctk.CTkLabel(settings, text="9:16 vertical  •  MP4  •  H.264", text_color=COLORS["text"], font=ctk.CTkFont(size=12)).pack(anchor="w", padx=14, pady=(0, 14))

        self.output_path = APP_DIR / "output" / "autotok_story.mp4"
        self.output_button = ctk.CTkButton(panel, text=f"Save as  •  {self.output_path.name}", height=40, fg_color="transparent", hover_color=COLORS["panel_2"], border_width=1, border_color=COLORS["border"], command=self._choose_output)
        self.output_button.pack(fill="x", padx=20, pady=(16, 10))
        self.render_button = ctk.CTkButton(panel, text="▶  CREATE VIDEO", height=52, fg_color=COLORS["orange"], hover_color="#E85239", command=self._start_render, font=ctk.CTkFont(size=14, weight="bold"))
        self.render_button.pack(fill="x", padx=20)
        batch_actions = ctk.CTkFrame(panel, fg_color="transparent")
        batch_actions.pack(fill="x", padx=20, pady=(8, 0))
        batch_actions.grid_columnconfigure((0, 1), weight=1)
        self.queue_button = ctk.CTkButton(batch_actions, text="", height=36, fg_color=COLORS["panel_2"], command=self._start_queue)
        self.queue_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ctk.CTkButton(batch_actions, text="History", height=36, fg_color="transparent", border_width=1, border_color=COLORS["border"], command=lambda: ListDialog(self, "history")).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self._save_queue_and_refresh()
        self.publish_button = ctk.CTkButton(
            panel, text="REVIEW & PUBLISH", height=42, fg_color=COLORS["panel_2"],
            hover_color=COLORS["accent"], state="disabled", command=lambda: self._open_publish(self.last_outputs),
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.publish_button.pack(fill="x", padx=20, pady=(8, 0))

        self.progress = ctk.CTkProgressBar(panel, height=8, progress_color=COLORS["accent"], fg_color=COLORS["border"])
        self.progress.set(0)
        self.progress.pack(fill="x", padx=20, pady=(20, 8))
        self.status_label = ctk.CTkLabel(panel, text="Ready to create", text_color=COLORS["muted"], font=ctk.CTkFont(size=11), wraplength=260)
        self.status_label.pack(anchor="w", padx=20)

        ctk.CTkLabel(panel, text="ACTIVITY", text_color=COLORS["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=22, pady=(24, 7))
        self.log_box = ctk.CTkTextbox(panel, height=130, fg_color=COLORS["window"], border_width=0, corner_radius=10, text_color=COLORS["muted"], font=ctk.CTkFont(family="Consolas", size=11), activate_scrollbars=True)
        self.log_box.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        self._log("AutoTok Studio ready")
        if self.tts_config.configured:
            self._log("Flux voice configuration found (not yet validated)")
        else:
            self._log("No API key; Windows voice fallback enabled")

        self.open_output_button = ctk.CTkButton(panel, text="Open output folder", height=36, fg_color="transparent", hover_color=COLORS["panel_2"], command=self._open_output_folder)
        self.open_output_button.pack(fill="x", padx=20, pady=(0, 14))

    def _caption_words_changed(self, value: float) -> None:
        words = int(round(value))
        self.preferences.caption_words = words
        if hasattr(self, "words_label"):
            self.words_label.configure(text=f"{words} words per caption")

    def _caption_position_changed(self, value: float) -> None:
        self.preferences.caption_position = float(value)
        if hasattr(self, "position_label"):
            self.position_label.configure(text=f"Caption height  •  {value * 100:.0f}%")

    def _category_changed(self, value: str) -> None:
        self.preferences.video_category = value
        save_preferences(self.preferences)
        self._toggle_auto_video()

    def _update_word_count(self, _event=None) -> None:
        count = len(self.story_text.get("1.0", "end-1c").split())
        minutes = count / 155.0
        self.word_count_label.configure(text=f"{count} words  •  ~{minutes:.1f} min")

    def _set_busy(self, busy: bool, status: str | None = None) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.fetch_button.configure(state=state)
        self.render_button.configure(state=state)
        if hasattr(self, "queue_five_button"):
            self.queue_five_button.configure(state=state)
        if hasattr(self, "queue_button"):
            self.queue_button.configure(state=state)
        if status:
            self.status_label.configure(text=status, text_color=COLORS["muted"])

    def _fetch_story(self) -> None:
        if self.busy:
            return
        subreddit = self.subreddit_entry.get().strip()
        self._set_busy(True, "Searching Reddit…")
        self.progress.set(0.08)
        self._log(f"Searching r/{subreddit.lstrip('r/')}…")

        def worker() -> None:
            try:
                post = fetch_random_story(
                    subreddit=subreddit, sort=self.sort_var.get(), timeframe=self.time_var.get(),
                    min_words=100, max_words=900, exclude_permalinks=seen_permalinks(),
                )
                self.events.put(("story", post))
            except Exception as exc:
                self.events.put(("error", "Could not pull a Reddit story", str(exc)))
            finally:
                self.events.put(("idle",))

        threading.Thread(target=worker, daemon=True).start()

    def _fetch_five_stories(self) -> None:
        if self.busy:
            return
        subreddit = self.subreddit_entry.get().strip()
        self._set_busy(True, "Building story queue…")
        self._log(f"Finding five stories from r/{subreddit.lstrip('r/')}…")

        def worker() -> None:
            try:
                posts = fetch_stories(
                    subreddit=subreddit, sort=self.sort_var.get(), timeframe=self.time_var.get(),
                    min_words=100, max_words=900, limit=80,
                )
                seen = seen_permalinks()
                posts = [post for post in posts if post.permalink not in seen][:5]
                if not posts:
                    raise RuntimeError("No suitable stories were found for the queue.")
                self.events.put(("queued_posts", posts))
            except Exception as exc:
                self.events.put(("error", "Could not build queue", str(exc)))
            finally:
                self.events.put(("idle",))

        threading.Thread(target=worker, daemon=True).start()

    def _choose_videos(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Choose background footage",
            filetypes=[("Video files", "*.mp4 *.mov *.m4v *.webm"), ("All files", "*.*")],
        )
        if not paths:
            return
        self.auto_video_var.set(False)
        self.background_files = [Path(path) for path in paths]
        self._toggle_auto_video()
        self._log(f"Selected {len(self.background_files)} background clip(s)")

    def _video_library_files(self) -> list[Path]:
        return scan_videos(self.category_var.get() if hasattr(self, "category_var") else "All")

    def _toggle_auto_video(self) -> None:
        auto = self.auto_video_var.get()
        self.footage_button.configure(state="disabled" if auto else "normal")
        if auto:
            count = len(self._video_library_files())
            if count:
                self.footage_label.configure(
                    text=f"{count} library clip(s) available\nOne will be chosen randomly per render",
                    text_color=COLORS["success"],
                )
            else:
                self.footage_label.configure(
                    text="videos folder is empty\nAdd clips or use Choose files",
                    text_color=COLORS["muted"],
                )
        elif self.background_files:
            names = ", ".join(path.name for path in self.background_files[:2])
            if len(self.background_files) > 2:
                names += f" +{len(self.background_files) - 2} more"
            self.footage_label.configure(
                text=f"{len(self.background_files)} selected\n{names}",
                text_color=COLORS["success"],
            )
        else:
            self.footage_label.configure(
                text="No clips selected\nA gradient background will be used",
                text_color=COLORS["muted"],
            )

    def _open_video_library(self) -> None:
        VIDEO_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(VIDEO_LIBRARY_DIR)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(VIDEO_LIBRARY_DIR)])
        self.after(750, self._refresh_video_library)

    def _refresh_video_library(self) -> None:
        categories = video_categories()
        self.category_menu.configure(values=categories)
        if self.category_var.get() not in categories:
            self.category_var.set("All")
        self._toggle_auto_video()

    def _current_editor_post(self, show_warning: bool = True) -> StoryPost | None:
        title = self.title_entry.get().strip()
        body = self.story_text.get("1.0", "end-1c").strip()
        if not title or len(body.split()) < 8:
            if show_warning:
                messagebox.showwarning("Story needed", "Add a title and a story of at least a few sentences.", parent=self)
            return None
        subreddit = self.current_post.subreddit if self.current_post.title == title else f"r/{self.subreddit_entry.get().strip().removeprefix('r/')}"
        return StoryPost(
            subreddit=subreddit, title=title, body=body,
            author=self.current_post.author, score=self.current_post.score,
            comments=self.current_post.comments, permalink=self.current_post.permalink,
        )

    def _show_post(self, post: StoryPost, status: str = "Story loaded") -> None:
        self.current_post = post
        self.title_entry.delete(0, "end")
        self.title_entry.insert(0, post.title)
        self.story_text.delete("1.0", "end")
        self.story_text.insert("1.0", post.body)
        self.source_line.configure(text=f"{post.subreddit}  •  u/{post.author}  •  {_compact(post.score)} points  •  {_compact(post.comments)} comments")
        self._update_word_count()
        self.progress.set(0)
        self.status_label.configure(text=status)

    def _add_current_to_queue(self) -> None:
        post = self._current_editor_post()
        if not post:
            return
        if any(existing.permalink and existing.permalink == post.permalink for existing in self.render_queue):
            self._log("That Reddit post is already in the queue")
            return
        self.render_queue.append(post)
        self._save_queue_and_refresh()
        self._log(f"Queued: {post.title[:55]}")

    def _save_queue_and_refresh(self) -> None:
        save_queue(self.render_queue)
        if hasattr(self, "queue_button"):
            self.queue_button.configure(text=f"Render queue ({len(self.render_queue)})")

    def _open_preview(self) -> None:
        post = self._current_editor_post()
        if post:
            PreviewDialog(self, post)

    def _choose_output(self) -> None:
        selected = filedialog.asksaveasfilename(
            title="Save finished TikTok",
            initialdir=str(self.output_path.parent), initialfile=self.output_path.name,
            defaultextension=".mp4", filetypes=[("MP4 video", "*.mp4")]
        )
        if selected:
            self.output_path = Path(selected)
            self.output_button.configure(text=f"Save as  •  {self.output_path.name}")

    def _start_render(self) -> None:
        if self.busy:
            return
        post = self._current_editor_post()
        if not post:
            return
        if self.preferences.duplicate_check:
            duplicate = find_duplicate_story(post)
            if duplicate and not messagebox.askyesno(
                "Duplicate story",
                f"This story appears in render history from {duplicate.get('created_at', 'an earlier run')}. Render it again?",
                parent=self,
            ):
                return
        options = self._make_render_options(self.output_path)
        self._run_single_post(post, options)

    def _make_render_options(self, output_path: Path) -> RenderOptions:
        if self.quality_var.get() == "Draft":
            width, height, preset = 540, 960, "ultrafast"
        else:
            width, height, preset = 1080, 1920, "medium"
        save_preferences(self.preferences)
        return RenderOptions(
            output_path=output_path,
            background_files=self.background_files.copy() if not self.auto_video_var.get() else [],
            width=width, height=height, preset=preset,
            caption_words=self.preferences.caption_words,
            caption_position=self.preferences.caption_position,
            caption_style=self.preferences.caption_style,
            align_captions=self.preferences.align_captions,
            whisper_model=self.preferences.whisper_model,
            part_seconds=self.preferences.part_seconds,
            profanity_mode=self.preferences.profanity_mode,
            voice_speed=self.preferences.voice_speed,
            auto_music=self.preferences.auto_music,
            music_volume=self.preferences.music_volume,
            auto_pick_background=self.auto_video_var.get(),
            video_category=self.category_var.get(),
            scene_aware=self.preferences.scene_aware,
            smart_match_background=self.preferences.smart_match_background,
            audio_polish=self.preferences.audio_polish,
            bake_tiktok_cover=self.preferences.bake_tiktok_cover,
        )

    def _apply_preferences_to_ui(self) -> None:
        if hasattr(self, "position_slider"):
            self.position_slider.set(self.preferences.caption_position)
            self._caption_position_changed(self.preferences.caption_position)
        if hasattr(self, "category_menu"):
            categories = video_categories()
            self.category_menu.configure(values=categories)
            category = self.preferences.video_category if self.preferences.video_category in categories else "All"
            self.category_var.set(category)
            self._toggle_auto_video()

    def _open_publish(self, outputs: list[Path]) -> None:
        existing = [Path(path) for path in outputs if Path(path).exists()]
        if not existing:
            messagebox.showwarning("No video", "Render a video first, or open one from History.", parent=self)
            return
        PublishDialog(self, existing)

    def _start_publish(self, item: dict, dialog: PublishDialog) -> None:
        self._set_busy(True, "Opening TikTok uploader…")
        self._log(f"Publishing {Path(item['video']).name} with explicit approval")

        def worker() -> None:
            try:
                run_community_upload(item["payload"], lambda message: self.events.put(("log", f"TikTok: {message}")))
                final_status = "scheduled" if item["payload"].get("schedule") else "published"
                update_publish_item(item["id"], final_status)
                self.events.put(("publish_done", item["video"], dialog, final_status))
            except Exception as exc:
                update_publish_item(item["id"], "failed", str(exc))
                self.events.put(("publish_error", str(exc), dialog))
            finally:
                self.events.put(("idle",))

        threading.Thread(target=worker, daemon=True).start()

    def _run_single_post(self, post: StoryPost, options: RenderOptions) -> None:
        tts = self.tts_config
        self._set_busy(True, "Starting render…")
        self.progress.set(0.01)
        self._log(f"Creating {options.width}×{options.height} video • {self.preferences.part_length}")

        def progress(value: float, message: str) -> None:
            self.events.put(("progress", value, message))

        def worker() -> None:
            try:
                outputs = render_story_series(post, tts, options, progress)
                append_history(post, outputs)
                self.events.put(("done", outputs))
            except Exception as exc:
                traceback.print_exc()
                append_history(post, [], status="failed", error=str(exc))
                self.events.put(("error", "Video render failed", str(exc)))
            finally:
                self.events.put(("idle",))

        threading.Thread(target=worker, daemon=True).start()

    def _start_queue(self) -> None:
        if self.busy:
            return
        if not self.render_queue:
            ListDialog(self, "queue")
            return
        posts = self.render_queue.copy()
        template = self._make_render_options(self.output_path)
        self._set_busy(True, f"Rendering queue: 0/{len(posts)}")
        self._log(f"Starting batch of {len(posts)} stories")

        def worker() -> None:
            failed: list[StoryPost] = []
            completed_outputs: list[Path] = []
            for index, post in enumerate(posts):
                slug = re.sub(r"[^a-z0-9]+", "_", post.title.lower()).strip("_")[:48] or f"story_{index + 1}"
                output = template.output_path.parent / f"{slug}.mp4"
                options = replace(template, output_path=output)
                try:
                    outputs = render_story_series(
                        post, self.tts_config, options,
                        lambda value, message, i=index: self.events.put(("progress", (i + value) / len(posts), f"Queue {i + 1}/{len(posts)} • {message}")),
                    )
                    completed_outputs.extend(outputs)
                    append_history(post, outputs)
                except Exception as exc:
                    failed.append(post)
                    append_history(post, [], status="failed", error=str(exc))
                    self.events.put(("log", f"Queue item failed: {post.title[:40]} — {exc}"))
            self.events.put(("batch_done", completed_outputs, failed))
            self.events.put(("idle",))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "story":
                    post: StoryPost = event[1]
                    self._show_post(post, "Story loaded — edit anything you like")
                    self._log(f"Loaded “{post.title[:48]}” ({post.word_count} words)")
                elif kind == "queued_posts":
                    added = 0
                    existing_links = {post.permalink for post in self.render_queue if post.permalink}
                    for post in event[1]:
                        if not post.permalink or post.permalink not in existing_links:
                            self.render_queue.append(post)
                            existing_links.add(post.permalink)
                            added += 1
                    self._save_queue_and_refresh()
                    self.progress.set(0)
                    self.status_label.configure(text=f"Added {added} stories to the queue")
                    self._log(f"Queue now contains {len(self.render_queue)} stories")
                elif kind == "progress":
                    self.progress.set(event[1])
                    self.status_label.configure(text=event[2])
                    self._log(event[2])
                elif kind == "done":
                    outputs: list[Path] = event[1]
                    self.last_outputs = outputs
                    self.publish_button.configure(state="normal", fg_color=COLORS["accent"])
                    self.progress.set(1)
                    summary = outputs[0].name if len(outputs) == 1 else f"{len(outputs)} video parts"
                    self.status_label.configure(text=f"Finished — {summary}", text_color=COLORS["success"])
                    for output in outputs:
                        self._log(f"Saved {output}")
                    messagebox.showinfo("Video ready", f"Created {len(outputs)} video file(s) plus captions, thumbnails, and posting copy.\n\n{outputs[0].parent}", parent=self)
                elif kind == "batch_done":
                    outputs: list[Path] = event[1]
                    failed: list[StoryPost] = event[2]
                    self.last_outputs = outputs
                    if outputs:
                        self.publish_button.configure(state="normal", fg_color=COLORS["accent"])
                    self.render_queue = failed
                    self._save_queue_and_refresh()
                    self.progress.set(1 if outputs else 0)
                    self.status_label.configure(
                        text=f"Batch finished • {len(outputs)} videos • {len(failed)} failed",
                        text_color=COLORS["success"] if not failed else COLORS["orange"],
                    )
                    messagebox.showinfo("Batch complete", f"Created {len(outputs)} videos.\nFailed stories remaining in queue: {len(failed)}", parent=self)
                elif kind == "uploader_setup_done":
                    dialog = event[1]
                    if dialog.winfo_exists():
                        dialog._refresh_uploader_status()
                    self._log("Community uploader and Chromium are ready")
                    messagebox.showinfo("Uploader ready", "The isolated community uploader is installed. First upload will open a visible login window.", parent=self)
                elif kind == "publish_done":
                    video, dialog, publish_status = event[1], event[2], event[3]
                    verb = "Scheduled" if publish_status == "scheduled" else "Published"
                    self.status_label.configure(text=f"{verb} — {Path(video).name}", text_color=COLORS["success"])
                    self._log(f"TikTok uploader finished ({publish_status}): {video}")
                    if dialog.winfo_exists():
                        dialog.destroy()
                    if self.preferences.desktop_notifications and not self.preferences.notification_failures_only:
                        notify_desktop("AutoTok upload complete", Path(video).name)
                    messagebox.showinfo(f"{verb} successfully", "The uploader reported success. Confirm the post in TikTok before closing your browser session.", parent=self)
                elif kind == "publish_error":
                    error, dialog = event[1], event[2]
                    self.status_label.configure(text="TikTok upload failed", text_color=COLORS["danger"])
                    self._log(f"UPLOAD ERROR: {error}")
                    if dialog.winfo_exists():
                        dialog.action_button.configure(state="normal", text="RETRY UPLOAD")
                    if self.preferences.desktop_notifications:
                        notify_desktop("AutoTok upload failed", error)
                    messagebox.showerror("Upload failed", error, parent=self)
                elif kind == "log":
                    self._log(event[1])
                elif kind == "error":
                    self.progress.set(0)
                    self.status_label.configure(text=event[1], text_color=COLORS["danger"])
                    self._log(f"ERROR: {event[2]}")
                    messagebox.showerror(event[1], event[2], parent=self)
                    if self.preferences.desktop_notifications:
                        notify_desktop(event[1], event[2])
                elif kind == "idle":
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"• {message}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _open_settings(self) -> None:
        SettingsDialog(self, self.tts_config)

    def _open_output_folder(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(self.output_path.parent)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(self.output_path.parent)])


def _compact(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}m"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


if __name__ == "__main__":
    AutoTokApp().mainloop()
