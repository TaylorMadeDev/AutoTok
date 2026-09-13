from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import webbrowser
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from autotok.config import APP_DIR, OpenRouterConfig, load_openrouter_config, save_openrouter_config
from autotok.preferences import load_preferences, save_preferences
from autotok.voices import DEFAULT_FLUX_VOICE, FLUX_VOICES, valid_flux_voice


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

COLORS = {
    "window": "#090B12", "panel": "#11141D", "panel_2": "#171B27",
    "border": "#282D3C", "text": "#F4F6FC", "muted": "#9299AC",
    "accent": "#7C5CFC", "accent_hover": "#6B49F2", "orange": "#FF6246",
    "success": "#37D69B", "danger": "#FF647C",
}


class SetupWizard(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("AutoTok Setup")
        self.geometry("900x700")
        self.minsize(820, 640)
        self.configure(fg_color=COLORS["window"])
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.events: queue.Queue[tuple] = queue.Queue()
        self.page = "features"
        self.completed: dict[str, bool] = {}
        self.busy = False
        self.tested_voice = False
        self.signed_in = False
        self.video_import: Path | None = None
        self.music_import: Path | None = None

        self.features = {
            "reddit": ctk.BooleanVar(value=True),
            "alignment": ctk.BooleanVar(value=True),
            "publishing": ctk.BooleanVar(value=True),
            "analytics": ctk.BooleanVar(value=True),
            "music": ctk.BooleanVar(value=True),
            "notifications": ctk.BooleanVar(value=True),
        }
        current = load_openrouter_config()
        self.voice_provider = ctk.StringVar(value={"azure": "Azure Speech", "elevenlabs": "ElevenLabs"}.get(current.provider, "OpenRouter Flux"))
        self.skip_voice = ctk.BooleanVar(value=False)
        self.account = ctk.StringVar(value=load_preferences().tiktok_account)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self.content = ctk.CTkFrame(self, fg_color=COLORS["window"], corner_radius=0)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(1, weight=1)
        self.footer = ctk.CTkFrame(self, fg_color=COLORS["panel"], corner_radius=0, height=72)
        self.footer.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.back_button = ctk.CTkButton(self.footer, text="Back", fg_color="transparent", border_width=1, border_color=COLORS["border"], command=self._back)
        self.back_button.pack(side="left", padx=22, pady=16)
        self.next_button = ctk.CTkButton(self.footer, text="Install selected features", width=190, fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"], command=self._next)
        self.next_button.pack(side="right", padx=22, pady=16)
        self.later_button = ctk.CTkButton(self.footer, text="Do it later", fg_color="transparent", hover_color=COLORS["panel_2"], command=self._do_later)
        self.later_button.pack(side="right", pady=16)
        self._show_features()
        self.after(100, self._drain_events)

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, width=245, fg_color=COLORS["panel"], corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        ctk.CTkLabel(sidebar, text="●", text_color=COLORS["orange"], font=ctk.CTkFont(size=28)).pack(anchor="w", padx=24, pady=(30, 2))
        ctk.CTkLabel(sidebar, text="AutoTok Setup", text_color=COLORS["text"], font=ctk.CTkFont(size=23, weight="bold")).pack(anchor="w", padx=24)
        ctk.CTkLabel(sidebar, text="Everything, one step at a time.", text_color=COLORS["muted"]).pack(anchor="w", padx=24, pady=(2, 28))
        self.step_labels: dict[str, ctk.CTkLabel] = {}
        for index, (key, label) in enumerate((
            ("features", "Choose features"), ("install", "Install components"),
            ("provider", "Choose AI voice"), ("voice", "Connect & test voice"),
            ("tiktok", "TikTok sign-in"), ("library", "Folders & defaults"),
            ("finish", "Ready"),
        ), start=1):
            widget = ctk.CTkLabel(sidebar, text=f"{index}   {label}", anchor="w", text_color=COLORS["muted"], height=34)
            widget.pack(fill="x", padx=24, pady=2)
            self.step_labels[key] = widget

    def _clear(self) -> None:
        for child in self.content.winfo_children():
            child.destroy()

    def _heading(self, title: str, subtitle: str) -> ctk.CTkFrame:
        self._clear()
        self.next_button.configure(command=self._next)
        for key, label in self.step_labels.items():
            label.configure(text_color=COLORS["text"] if key == self.page else (COLORS["success"] if self.completed.get(key) else COLORS["muted"]))
        top = ctk.CTkFrame(self.content, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=38, pady=(34, 16))
        ctk.CTkLabel(top, text=title, text_color=COLORS["text"], font=ctk.CTkFont(size=28, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(top, text=subtitle, text_color=COLORS["muted"], wraplength=560, justify="left").pack(anchor="w", pady=(5, 0))
        body = ctk.CTkScrollableFrame(self.content, fg_color=COLORS["panel"], corner_radius=16)
        body.grid(row=1, column=0, sticky="nsew", padx=36, pady=(0, 22))
        self.back_button.configure(state="disabled" if self.page == "features" else "normal")
        self.later_button.pack_forget()
        return body

    def _choice(self, parent, key: str, title: str, detail: str, mandatory: bool = False) -> None:
        row = ctk.CTkFrame(parent, fg_color=COLORS["panel_2"], corner_radius=12)
        row.pack(fill="x", padx=12, pady=6)
        variable = ctk.BooleanVar(value=True) if mandatory else self.features[key]
        checkbox = ctk.CTkCheckBox(row, text=title, variable=variable, fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"], font=ctk.CTkFont(size=14, weight="bold"))
        checkbox.pack(anchor="w", padx=16, pady=(13, 2))
        if mandatory:
            checkbox.configure(state="disabled")
        ctk.CTkLabel(row, text=detail, text_color=COLORS["muted"], wraplength=520, justify="left").pack(anchor="w", padx=44, pady=(0, 13))

    def _show_features(self) -> None:
        self.page = "features"
        body = self._heading("Choose what AutoTok installs", "The core story editor is always included. Optional features can be added later by running setup.bat again.")
        self._choice(body, "reddit", "Core Story Studio", "Rendering, Reddit stories, captions, presets, drafts, preflight, and video library.", True)
        self._choice(body, "alignment", "Precise word-aligned captions", "Installs Faster Whisper. Larger download, but captions follow the spoken words accurately.")
        self._choice(body, "publishing", "TikTok auto-upload", "Installs TikTokAutoUploader and isolated Chromium, then offers a visible sign-in page.")
        self._choice(body, "analytics", "Analytics and content history", "CSV performance dashboard, duplicate detection, rights records, and queues.")
        self._choice(body, "music", "Music and smart footage tools", "Background music mixing, scene cuts, story matching, and asset folders.")
        self._choice(body, "notifications", "Failure notifications", "Quiet Windows notifications when a render or upload needs attention.")
        self.next_button.configure(text="Install selected features", state="normal")

    def _show_install(self) -> None:
        self.page = "install"
        body = self._heading("Installing components", "AutoTok keeps the experimental uploader in its own environment so it cannot interfere with video rendering.")
        self.install_status = ctk.CTkLabel(body, text="Preparing…", text_color=COLORS["text"], justify="left", wraplength=530)
        self.install_status.pack(anchor="w", padx=18, pady=(18, 10))
        self.install_progress = ctk.CTkProgressBar(body, progress_color=COLORS["accent"], fg_color=COLORS["border"])
        self.install_progress.set(0.03)
        self.install_progress.pack(fill="x", padx=18, pady=8)
        self.install_log = ctk.CTkTextbox(body, height=260, fg_color=COLORS["window"], text_color=COLORS["muted"], font=ctk.CTkFont(family="Consolas", size=11))
        self.install_log.pack(fill="both", expand=True, padx=18, pady=(8, 18))
        self.next_button.configure(text="Installing…", state="disabled")
        self.back_button.configure(state="disabled")
        self._set_busy(True)
        selected_alignment = self.features["alignment"].get()
        selected_publishing = self.features["publishing"].get()

        def worker() -> None:
            try:
                self.events.put(("install_progress", 0.1, "Installing core video studio…"))
                self._pip_install(["-r", str(APP_DIR / "requirements-core.txt")])
                if selected_alignment:
                    self.events.put(("install_progress", 0.45, "Installing precise caption alignment…"))
                    self._pip_install(["faster-whisper>=1.2.0"])
                if selected_publishing:
                    self.events.put(("install_progress", 0.7, "Installing isolated TikTok uploader and Chromium…"))
                    self._ensure_node()
                    from autotok.publishing import setup_uploader
                    setup_uploader(lambda message: self.events.put(("install_log", message)))
                self.events.put(("install_done",))
            except Exception as exc:
                self.events.put(("install_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _pip_install(self, arguments: list[str]) -> None:
        process = subprocess.Popen(
            [sys.executable, "-m", "pip", "install", "--upgrade", *arguments],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(APP_DIR),
        )
        if process.stdout:
            for line in process.stdout:
                if line.strip():
                    self.events.put(("install_log", line.strip()[-240:]))
        if process.wait():
            raise RuntimeError("A Python component failed to install. See the setup log above.")

    def _ensure_node(self) -> None:
        if shutil.which("node") and shutil.which("npm"):
            return
        winget = shutil.which("winget")
        if not winget:
            raise RuntimeError("TikTok auto-upload needs Node.js. Install Node.js LTS, then rerun setup.")
        self.events.put(("install_log", "Node.js is missing; installing the LTS release for auto-upload…"))
        result = subprocess.run(
            [winget, "install", "--id", "OpenJS.NodeJS.LTS", "-e", "--accept-package-agreements", "--accept-source-agreements"],
            capture_output=True, text=True, timeout=1200,
        )
        node_dir = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs"
        if node_dir.exists():
            os.environ["PATH"] = str(node_dir) + os.pathsep + os.environ.get("PATH", "")
        if result.returncode or not shutil.which("node"):
            raise RuntimeError("Node.js installation did not finish. Install Node.js LTS and rerun setup.")

    def _show_provider(self) -> None:
        self.page = "provider"
        body = self._heading("Choose your AI voice", "You can switch later from the green voice badge inside AutoTok.")
        ctk.CTkSegmentedButton(body, values=["OpenRouter Flux", "Azure Speech", "ElevenLabs"], variable=self.voice_provider, selected_color=COLORS["accent"], selected_hover_color=COLORS["accent_hover"]).pack(fill="x", padx=18, pady=(22, 12))
        ctk.CTkLabel(body, text="OPENROUTER FLUX\nUses deepgram/flux-tts through OpenRouter.\n\nAZURE SPEECH\nUses an Azure Speech resource key and regional endpoint.\n\nELEVENLABS\nUses an ElevenLabs API key, voice ID, and speech model.", justify="left", wraplength=520, text_color=COLORS["muted"]).pack(anchor="w", padx=18, pady=12)
        self.next_button.configure(text="Configure voice", state="normal")

    def _show_voice(self) -> None:
        self.page = "voice"
        self.tested_voice = False
        current = load_openrouter_config()
        provider = self.voice_provider.get()
        body = self._heading(f"Connect {provider}", "The Next button unlocks after a successful live audio test. Choose Do it later to use the Windows voice fallback.")
        self.voice_fields: dict[str, ctk.CTkEntry] = {}
        if provider == "OpenRouter Flux":
            self._field(body, "OPENROUTER API KEY", current.api_key, "key", show="•")
            self._field(body, "MODEL", current.model or "deepgram/flux-tts:free", "model")
            self._voice_dropdown(body, valid_flux_voice(current.voice))
            ctk.CTkButton(body, text="Get an OpenRouter key", fg_color=COLORS["panel_2"], command=lambda: webbrowser.open("https://openrouter.ai/settings/keys")).pack(anchor="w", padx=18, pady=8)
        elif provider == "Azure Speech":
            self._field(body, "AZURE SPEECH KEY", current.azure_key, "key", show="•")
            self._field(body, "RESOURCE REGION", current.azure_region or "uksouth", "region")
            self._field(body, "NEURAL VOICE", current.azure_voice or "en-GB-SoniaNeural", "voice")
            ctk.CTkButton(body, text="Open Azure Speech setup", fg_color=COLORS["panel_2"], command=lambda: webbrowser.open("https://portal.azure.com/#create/Microsoft.CognitiveServicesSpeechServices")).pack(anchor="w", padx=18, pady=8)
        else:
            self._field(body, "ELEVENLABS API KEY", current.elevenlabs_key, "key", show="•")
            self._field(body, "VOICE ID", current.elevenlabs_voice or "JBFqnCBsd6RMkjVDRZzb", "voice")
            self._field(body, "MODEL", current.elevenlabs_model or "eleven_multilingual_v2", "model")
            ctk.CTkButton(body, text="Get an ElevenLabs key", fg_color=COLORS["panel_2"], command=lambda: webbrowser.open("https://elevenlabs.io/app/settings/api-keys")).pack(anchor="w", padx=18, pady=8)
        self.voice_status = ctk.CTkLabel(body, text="Not tested yet", text_color=COLORS["muted"], wraplength=520)
        self.voice_status.pack(anchor="w", padx=18, pady=(10, 5))
        ctk.CTkButton(body, text="Test API and play sample", fg_color=COLORS["accent"], command=self._test_voice).pack(anchor="w", padx=18, pady=(4, 18))
        self.next_button.configure(text="Voice test required", state="disabled")
        self.later_button.pack(side="right", pady=16)

    def _field(self, parent, label: str, value: str, key: str, show: str = "") -> None:
        ctk.CTkLabel(parent, text=label, text_color=COLORS["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=18, pady=(14, 5))
        entry = ctk.CTkEntry(parent, height=40, fg_color=COLORS["panel_2"], border_color=COLORS["border"], show=show)
        entry.pack(fill="x", padx=18)
        entry.insert(0, value)
        self.voice_fields[key] = entry

    def _voice_dropdown(self, parent, value: str) -> None:
        ctk.CTkLabel(parent, text="FLUX VOICE", text_color=COLORS["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=18, pady=(14, 5))
        menu = ctk.CTkOptionMenu(
            parent, values=list(FLUX_VOICES), fg_color=COLORS["panel_2"],
            button_color=COLORS["accent"], button_hover_color=COLORS["accent_hover"],
            dynamic_resizing=False,
        )
        menu.set(value or DEFAULT_FLUX_VOICE)
        menu.pack(fill="x", padx=18)
        self.voice_fields["voice"] = menu

    def _voice_config(self) -> OpenRouterConfig:
        current = load_openrouter_config()
        if self.voice_provider.get() == "Azure Speech":
            return OpenRouterConfig(
                provider="azure", api_keys=current.api_keys, api_key_mode=current.api_key_mode,
                model=current.model, voice=current.voice,
                azure_key=self.voice_fields["key"].get().strip(), azure_key_mode=current.azure_key_mode,
                azure_region=self.voice_fields["region"].get().strip(),
                azure_voice=self.voice_fields["voice"].get().strip(),
                elevenlabs_keys=current.elevenlabs_keys, elevenlabs_key_mode=current.elevenlabs_key_mode,
                elevenlabs_voice=current.elevenlabs_voice, elevenlabs_model=current.elevenlabs_model,
            )
        if self.voice_provider.get() == "ElevenLabs":
            return OpenRouterConfig(
                provider="elevenlabs", api_keys=current.api_keys, api_key_mode=current.api_key_mode,
                model=current.model, voice=current.voice,
                azure_keys=current.azure_keys, azure_key_mode=current.azure_key_mode,
                azure_region=current.azure_region, azure_voice=current.azure_voice,
                elevenlabs_key=self.voice_fields["key"].get().strip(),
                elevenlabs_key_mode=current.elevenlabs_key_mode,
                elevenlabs_voice=self.voice_fields["voice"].get().strip(),
                elevenlabs_model=self.voice_fields["model"].get().strip(),
            )
        return OpenRouterConfig(
            provider="flux", api_key=self.voice_fields["key"].get().strip(),
            api_key_mode=current.api_key_mode,
            model=self.voice_fields["model"].get().strip(), voice=self.voice_fields["voice"].get().strip(),
            azure_keys=current.azure_keys, azure_key_mode=current.azure_key_mode,
            azure_region=current.azure_region, azure_voice=current.azure_voice,
            elevenlabs_keys=current.elevenlabs_keys, elevenlabs_key_mode=current.elevenlabs_key_mode,
            elevenlabs_voice=current.elevenlabs_voice, elevenlabs_model=current.elevenlabs_model,
        )

    def _test_voice(self) -> None:
        config = self._voice_config()
        if not config.configured:
            self.voice_status.configure(text="Complete the key, region/model, and voice fields first.", text_color=COLORS["danger"])
            return
        self.voice_status.configure(text="Testing the live speech API…", text_color=COLORS["muted"])
        self._set_busy(True)

        def worker() -> None:
            try:
                from autotok.engine import synthesize_speech
                sample, engine = synthesize_speech(
                    "AutoTok is connected and ready to narrate your next story.", config,
                    APP_DIR / ".autotok" / "installer_voice_test", allow_local_fallback=False,
                )
                self.events.put(("voice_ok", config, sample, engine))
            except Exception as exc:
                self.events.put(("voice_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _show_tiktok(self) -> None:
        if not self.features["publishing"].get():
            self.completed["tiktok"] = True
            self._show_library()
            return
        self.page = "tiktok"
        body = self._heading("Sign in to TikTok", "A normal visible Chromium window will open. AutoTok saves session cookies locally—not your password—and you handle login or any challenge yourself.")
        ctk.CTkLabel(body, text="ACCOUNT PROFILE NAME", text_color=COLORS["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=18, pady=(20, 5))
        ctk.CTkEntry(body, textvariable=self.account, placeholder_text="mytiktokaccount", fg_color=COLORS["panel_2"], border_color=COLORS["border"]).pack(fill="x", padx=18)
        self.signin_status = ctk.CTkLabel(body, text="Not signed in", text_color=COLORS["muted"], wraplength=520, justify="left")
        self.signin_status.pack(anchor="w", padx=18, pady=(14, 5))
        ctk.CTkButton(body, text="Open visible TikTok sign-in", fg_color=COLORS["accent"], command=self._sign_in).pack(anchor="w", padx=18, pady=8)
        ctk.CTkLabel(body, text="The community uploader is experimental. Every public upload still requires confirmation in AutoTok's Review & Publish panel.", text_color=COLORS["orange"], wraplength=520, justify="left").pack(anchor="w", padx=18, pady=16)
        self.next_button.configure(text="Sign-in required", state="disabled")
        self.later_button.pack(side="right", pady=16)

    def _sign_in(self) -> None:
        account = self.account.get().strip()
        if not account:
            self.signin_status.configure(text="Enter a profile name first.", text_color=COLORS["danger"])
            return
        self._set_busy(True)
        self.signin_status.configure(text="Browser open — finish signing in there…", text_color=COLORS["muted"])

        def worker() -> None:
            try:
                from autotok.publishing import sign_in_account
                path = sign_in_account(account, lambda text: self.events.put(("signin_progress", text)))
                self.events.put(("signin_ok", path))
            except Exception as exc:
                self.events.put(("signin_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _show_library(self) -> None:
        self.page = "library"
        prefs = load_preferences()
        body = self._heading("Folders and sensible defaults", "Importing copies supported media into AutoTok. Your original folders and files are not changed.")
        self.video_path_label = ctk.CTkLabel(body, text=str(self.video_import or "Use AutoTok\\videos"), text_color=COLORS["muted"], wraplength=510)
        self.video_path_label.pack(anchor="w", padx=18, pady=(18, 4))
        ctk.CTkButton(body, text="Import an existing videos folder", fg_color=COLORS["panel_2"], command=self._choose_videos).pack(anchor="w", padx=18)
        self.music_path_label = ctk.CTkLabel(body, text=str(self.music_import or "Use AutoTok\\music"), text_color=COLORS["muted"], wraplength=510)
        self.music_path_label.pack(anchor="w", padx=18, pady=(16, 4))
        ctk.CTkButton(body, text="Import an existing music folder", fg_color=COLORS["panel_2"], command=self._choose_music).pack(anchor="w", padx=18)
        ctk.CTkLabel(body, text="DEFAULT PART LENGTH", text_color=COLORS["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=18, pady=(18, 5))
        self.part_length = ctk.StringVar(value=prefs.part_length)
        ctk.CTkOptionMenu(body, values=["60 seconds", "90 seconds", "3 minutes", "Full story"], variable=self.part_length, fg_color=COLORS["panel_2"], button_color=COLORS["accent"]).pack(fill="x", padx=18)
        self.shortcut = ctk.BooleanVar(value=True)
        self.launch_after = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(body, text="Create a desktop shortcut", variable=self.shortcut, fg_color=COLORS["accent"]).pack(anchor="w", padx=18, pady=(20, 5))
        ctk.CTkCheckBox(body, text="Launch AutoTok when setup finishes", variable=self.launch_after, fg_color=COLORS["accent"]).pack(anchor="w", padx=18, pady=5)
        self.next_button.configure(text="Apply settings", state="normal")

    def _choose_videos(self) -> None:
        path = filedialog.askdirectory(title="Choose a folder of background videos")
        if path:
            self.video_import = Path(path)
            self.video_path_label.configure(text=path)

    def _choose_music(self) -> None:
        path = filedialog.askdirectory(title="Choose a folder of background music")
        if path:
            self.music_import = Path(path)
            self.music_path_label.configure(text=path)

    def _apply_library(self) -> None:
        video_dir, music_dir = APP_DIR / "videos", APP_DIR / "music"
        video_dir.mkdir(parents=True, exist_ok=True)
        music_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for source, target, extensions in (
            (self.video_import, video_dir, {".mp4", ".mov", ".m4v", ".webm"}),
            (self.music_import, music_dir, {".mp3", ".wav", ".m4a", ".aac", ".ogg"}),
        ):
            if source:
                for path in source.rglob("*"):
                    if path.is_file() and path.suffix.lower() in extensions:
                        destination = target / path.name
                        if not destination.exists():
                            shutil.copy2(path, destination)
                            copied += 1
        prefs = load_preferences()
        prefs.part_length = self.part_length.get()
        prefs.align_captions = self.features["alignment"].get()
        prefs.auto_music = self.features["music"].get()
        prefs.desktop_notifications = self.features["notifications"].get()
        prefs.tiktok_account = self.account.get().strip()
        save_preferences(prefs)
        if self.shortcut.get():
            self._create_shortcut()
        state = {
            "features": {key: value.get() for key, value in self.features.items()},
            "voice_tested": self.tested_voice, "tiktok_signed_in": self.signed_in,
            "media_files_imported": copied,
        }
        (APP_DIR / ".autotok").mkdir(parents=True, exist_ok=True)
        (APP_DIR / ".autotok" / "installation.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

    def _create_shortcut(self) -> None:
        desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
        link = desktop / "AutoTok.lnk"
        launcher = APP_DIR / "run_autotok.bat"
        environment = os.environ.copy()
        environment.update({"AUTOTOK_SHORTCUT": str(link), "AUTOTOK_LAUNCHER": str(launcher), "AUTOTOK_DIR": str(APP_DIR)})
        script = (
            "$w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut($env:AUTOTOK_SHORTCUT);"
            "$s.TargetPath=$env:AUTOTOK_LAUNCHER; $s.WorkingDirectory=$env:AUTOTOK_DIR; $s.Description='AutoTok AI Story Studio'; $s.Save()"
        )
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], env=environment, capture_output=True)

    def _show_finish(self) -> None:
        self.page = "finish"
        body = self._heading("AutoTok is ready", "Setup is complete. Run setup.bat again whenever you want to add an optional feature or change provider.")
        voice = self.voice_provider.get() if self.tested_voice else "Windows fallback / configure later"
        uploader = "installed and signed in" if self.signed_in else ("installed; sign in later" if self.features["publishing"].get() else "not selected")
        ctk.CTkLabel(body, text=f"✓ Core video studio installed\n✓ Voice: {voice}\n✓ TikTok uploader: {uploader}\n✓ Video folder: {APP_DIR / 'videos'}\n✓ Music folder: {APP_DIR / 'music'}", justify="left", text_color=COLORS["success"], font=ctk.CTkFont(size=14)).pack(anchor="w", padx=20, pady=24)
        ctk.CTkLabel(body, text="For public uploads, AutoTok still requires media preflight, rights confirmation, and a final per-video approval.", justify="left", wraplength=520, text_color=COLORS["muted"]).pack(anchor="w", padx=20)
        self.back_button.configure(state="disabled")
        self.next_button.configure(text="Launch AutoTok" if self.launch_after.get() else "Finish", state="normal")

    def _next(self) -> None:
        if self.busy:
            return
        if self.page == "features":
            self.completed["features"] = True
            self._show_install()
        elif self.page == "install":
            self.completed["install"] = True
            self._show_provider()
        elif self.page == "provider":
            self.completed["provider"] = True
            self._show_voice()
        elif self.page == "voice":
            if not self.tested_voice:
                return
            self.completed["voice"] = True
            self._show_tiktok()
        elif self.page == "tiktok":
            if not self.signed_in:
                return
            self.completed["tiktok"] = True
            self._show_library()
        elif self.page == "library":
            try:
                self._apply_library()
            except Exception as exc:
                messagebox.showerror("Could not apply setup", str(exc), parent=self)
                return
            self.completed["library"] = True
            self._show_finish()
        elif self.page == "finish":
            if self.launch_after.get():
                subprocess.Popen([str(APP_DIR / "run_autotok.bat")], cwd=str(APP_DIR), shell=True)
            self.destroy()

    def _back(self) -> None:
        if self.busy:
            return
        if self.page == "provider": self._show_features()
        elif self.page == "voice": self._show_provider()
        elif self.page == "tiktok": self._show_voice()
        elif self.page == "library": self._show_tiktok() if self.features["publishing"].get() else self._show_voice()

    def _do_later(self) -> None:
        if self.busy:
            return
        if self.page == "voice":
            current = load_openrouter_config()
            current.provider = {"Azure Speech": "azure", "ElevenLabs": "elevenlabs"}.get(self.voice_provider.get(), "flux")
            save_openrouter_config(current)
            self.completed["voice"] = True
            self._show_tiktok()
        elif self.page == "tiktok":
            self.completed["tiktok"] = True
            self._show_library()

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        if busy:
            self.next_button.configure(state="disabled")

    def _drain_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "install_log":
                    self.install_log.insert("end", event[1] + "\n")
                    self.install_log.see("end")
                elif kind == "install_progress":
                    self.install_progress.set(event[1])
                    self.install_status.configure(text=event[2])
                elif kind == "install_done":
                    self._set_busy(False)
                    self.install_progress.set(1)
                    self.install_status.configure(text="All selected components are installed.", text_color=COLORS["success"])
                    self.next_button.configure(text="Choose AI voice", state="normal")
                elif kind == "install_error":
                    self._set_busy(False)
                    self.install_status.configure(text=event[1], text_color=COLORS["danger"])
                    self.next_button.configure(text="Retry installation", state="normal", command=self._show_install)
                elif kind == "voice_ok":
                    config, sample, engine = event[1], event[2], event[3]
                    save_openrouter_config(config)
                    self.tested_voice = True
                    self._set_busy(False)
                    self.voice_status.configure(text=f"Connected successfully with {engine}. Playing the sample now.", text_color=COLORS["success"])
                    self.next_button.configure(text="Continue", state="normal", command=self._next)
                    os.startfile(sample)  # type: ignore[attr-defined]
                elif kind == "voice_error":
                    self._set_busy(False)
                    self.voice_status.configure(text=f"Test failed: {event[1]}", text_color=COLORS["danger"])
                elif kind == "signin_progress":
                    self.signin_status.configure(text=event[1], text_color=COLORS["muted"])
                elif kind == "signin_ok":
                    self._set_busy(False)
                    self.signed_in = True
                    self.signin_status.configure(text=f"Signed in. Session saved locally at {event[1]}", text_color=COLORS["success"])
                    self.next_button.configure(text="Continue", state="normal")
                elif kind == "signin_error":
                    self._set_busy(False)
                    self.signin_status.configure(text=event[1], text_color=COLORS["danger"])
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._drain_events)

    def _close(self) -> None:
        if self.busy and not messagebox.askyesno("Setup is running", "Close setup while a component is still running?", parent=self):
            return
        self.destroy()


if __name__ == "__main__":
    SetupWizard().mainloop()
