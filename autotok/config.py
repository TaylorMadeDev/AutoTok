from __future__ import annotations

import configparser
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import keyring
except ImportError:  # The app can still explain how to finish setup.
    keyring = None

from .voices import DEFAULT_FLUX_VOICE, valid_flux_voice


# A frozen PyInstaller build keeps its user-editable configuration beside the
# executable.  Source checkouts retain the repository-root layout.
APP_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent.parent
)
CONFIG_PATH = APP_DIR / "config.ini"
LEGACY_CONFIG_PATH = APP_DIR.parent / "aistory" / "config.ini"
KEYRING_SERVICE = "AutoTok OpenRouter"
KEYRING_USERNAME = "api-key"
AZURE_KEYRING_SERVICE = "AutoTok Azure Speech"
AZURE_KEYRING_USERNAME = "speech-key"
KEYRING_MARKER = "__windows_credential_manager__"


@dataclass(slots=True)
class OpenRouterConfig:
    provider: str = "flux"
    api_key: str = ""
    model: str = "deepgram/flux-tts:free"
    voice: str = DEFAULT_FLUX_VOICE
    http_referer: str = ""
    source: Path | None = None
    azure_key: str = ""
    azure_region: str = "uksouth"
    azure_voice: str = "en-GB-SoniaNeural"

    @property
    def configured(self) -> bool:
        if self.provider == "azure":
            return bool(self.azure_key.strip() and self.azure_region.strip() and self.azure_voice.strip())
        return bool(self.api_key.strip())


def _read(path: Path) -> OpenRouterConfig | None:
    if not path.exists():
        return None
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    if not parser.has_section("openrouter") and not parser.has_section("voice"):
        return None
    stored_key = parser.get("openrouter", "api_key", fallback="").strip() if parser.has_section("openrouter") else ""
    if stored_key == KEYRING_MARKER:
        stored_key = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME) if keyring else ""
    azure_key = parser.get("azure_speech", "api_key", fallback="").strip() if parser.has_section("azure_speech") else ""
    if azure_key == KEYRING_MARKER:
        azure_key = keyring.get_password(AZURE_KEYRING_SERVICE, AZURE_KEYRING_USERNAME) if keyring else ""
    return OpenRouterConfig(
        provider=parser.get("voice", "provider", fallback="flux").strip().lower() if parser.has_section("voice") else "flux",
        api_key=stored_key or "",
        model=parser.get(
            "openrouter", "model", fallback="deepgram/flux-tts:free"
        ).strip(),
        voice=valid_flux_voice(parser.get(
            "openrouter", "voice", fallback=DEFAULT_FLUX_VOICE
        ).strip()),
        http_referer=parser.get("openrouter", "http_referer", fallback="").strip(),
        source=path,
        azure_key=azure_key or "",
        azure_region=parser.get("azure_speech", "region", fallback="uksouth").strip() if parser.has_section("azure_speech") else "uksouth",
        azure_voice=parser.get("azure_speech", "voice", fallback="en-GB-SoniaNeural").strip() if parser.has_section("azure_speech") else "en-GB-SoniaNeural",
    )


def load_openrouter_config() -> OpenRouterConfig:
    """Prefer AutoTok config, then reuse the sibling aistory configuration."""
    for path in (CONFIG_PATH, LEGACY_CONFIG_PATH):
        loaded = _read(path)
        if loaded and (loaded.configured or path == CONFIG_PATH):
            return loaded
    return OpenRouterConfig(source=CONFIG_PATH)


def save_openrouter_config(config: OpenRouterConfig) -> Path:
    stored_key = config.api_key.strip()
    if stored_key and keyring is not None:
        try:
            keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, stored_key)
            stored_key = KEYRING_MARKER
        except Exception:
            # Some minimal Python installations have no usable keyring backend.
            # Falling back preserves functionality; config.ini remains git-ignored.
            pass
    parser = configparser.ConfigParser()
    azure_key = config.azure_key.strip()
    if azure_key and keyring is not None:
        try:
            keyring.set_password(AZURE_KEYRING_SERVICE, AZURE_KEYRING_USERNAME, azure_key)
            azure_key = KEYRING_MARKER
        except Exception:
            pass
    parser["voice"] = {"provider": config.provider.strip().lower() or "flux"}
    parser["openrouter"] = {
        "api_key": stored_key,
        "model": config.model.strip() or "deepgram/flux-tts:free",
        "voice": valid_flux_voice(config.voice.strip()),
        "http_referer": config.http_referer.strip(),
    }
    parser["azure_speech"] = {
        "api_key": azure_key,
        "region": config.azure_region.strip() or "uksouth",
        "voice": config.azure_voice.strip() or "en-GB-SoniaNeural",
    }
    with CONFIG_PATH.open("w", encoding="utf-8") as handle:
        parser.write(handle)
    return CONFIG_PATH


def migrate_plaintext_secret() -> bool:
    """Move an existing AutoTok plaintext key into Windows Credential Manager."""
    if keyring is None or not CONFIG_PATH.exists():
        return False
    parser = configparser.ConfigParser()
    parser.read(CONFIG_PATH, encoding="utf-8")
    raw = parser.get("openrouter", "api_key", fallback="").strip()
    azure_raw = parser.get("azure_speech", "api_key", fallback="").strip() if parser.has_section("azure_speech") else ""
    if (not raw or raw == KEYRING_MARKER) and (not azure_raw or azure_raw == KEYRING_MARKER):
        return False
    current = _read(CONFIG_PATH)
    if not current:
        return False
    save_openrouter_config(current)
    return True
