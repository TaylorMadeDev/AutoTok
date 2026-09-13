from __future__ import annotations

import configparser
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import keyring
except ImportError:  # The app can still explain how to finish setup.
    keyring = None

from .voices import DEFAULT_FLUX_VOICE, valid_flux_voice


APP_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent.parent
)
CONFIG_PATH = APP_DIR / "config.ini"
LEGACY_CONFIG_PATH = APP_DIR.parent / "aistory" / "config.ini"
KEYRING_MARKER = "__windows_credential_manager__"
KEY_MODES = ("single", "rotate", "random")

KEYRING_SERVICE = "AutoTok OpenRouter"
KEYRING_USERNAME = "api-key"
KEYRING_KEYS_USERNAME = "api-keys"
AZURE_KEYRING_SERVICE = "AutoTok Azure Speech"
AZURE_KEYRING_USERNAME = "speech-key"
AZURE_KEYRING_KEYS_USERNAME = "speech-keys"
ELEVENLABS_KEYRING_SERVICE = "AutoTok ElevenLabs"
ELEVENLABS_KEYRING_USERNAME = "api-key"
ELEVENLABS_KEYRING_KEYS_USERNAME = "api-keys"


def normalize_key_mode(value: str) -> str:
    value = value.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {"auto_rotate": "rotate", "random_key": "random", "use_one_key": "single"}
    value = aliases.get(value, value)
    return value if value in KEY_MODES else "single"


def parse_api_keys(value: str | list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("["):
            try:
                decoded = json.loads(raw)
                value = [str(item) for item in decoded] if isinstance(decoded, list) else [raw]
            except ValueError:
                value = re.split(r"[,;\r\n]+", raw)
        else:
            value = re.split(r"[,;\r\n]+", raw)
    result: list[str] = []
    for item in value:
        key = str(item).strip()
        if key and key not in result and key != KEYRING_MARKER:
            result.append(key)
    return tuple(result)


def _merge_keys(keys: tuple[str, ...] | list[str], legacy: str) -> tuple[str, ...]:
    return parse_api_keys((*parse_api_keys(keys), *parse_api_keys(legacy)))


@dataclass(slots=True)
class OpenRouterConfig:
    provider: str = "flux"
    api_key: str = ""
    api_keys: tuple[str, ...] = ()
    api_key_mode: str = "single"
    model: str = "deepgram/flux-tts:free"
    voice: str = DEFAULT_FLUX_VOICE
    http_referer: str = ""
    source: Path | None = None
    azure_key: str = ""
    azure_keys: tuple[str, ...] = ()
    azure_key_mode: str = "single"
    azure_region: str = "uksouth"
    azure_voice: str = "en-GB-SoniaNeural"
    elevenlabs_key: str = ""
    elevenlabs_keys: tuple[str, ...] = ()
    elevenlabs_key_mode: str = "single"
    elevenlabs_voice: str = "JBFqnCBsd6RMkjVDRZzb"
    elevenlabs_model: str = "eleven_multilingual_v2"

    def __post_init__(self) -> None:
        self.provider = self.provider.strip().lower()
        if self.provider not in {"flux", "azure", "elevenlabs"}:
            self.provider = "flux"
        self.api_keys = _merge_keys(self.api_keys, self.api_key)
        self.azure_keys = _merge_keys(self.azure_keys, self.azure_key)
        self.elevenlabs_keys = _merge_keys(self.elevenlabs_keys, self.elevenlabs_key)
        self.api_key = self.api_keys[0] if self.api_keys else ""
        self.azure_key = self.azure_keys[0] if self.azure_keys else ""
        self.elevenlabs_key = self.elevenlabs_keys[0] if self.elevenlabs_keys else ""
        self.api_key_mode = normalize_key_mode(self.api_key_mode)
        self.azure_key_mode = normalize_key_mode(self.azure_key_mode)
        self.elevenlabs_key_mode = normalize_key_mode(self.elevenlabs_key_mode)

    def keys_for_provider(self, provider: str | None = None) -> tuple[str, ...]:
        selected = (provider or self.provider).strip().lower()
        if selected == "azure":
            return self.azure_keys
        if selected == "elevenlabs":
            return self.elevenlabs_keys
        return self.api_keys

    def key_mode_for_provider(self, provider: str | None = None) -> str:
        selected = (provider or self.provider).strip().lower()
        if selected == "azure":
            return self.azure_key_mode
        if selected == "elevenlabs":
            return self.elevenlabs_key_mode
        return self.api_key_mode

    @property
    def configured(self) -> bool:
        if not self.keys_for_provider():
            return False
        if self.provider == "azure":
            return bool(self.azure_region.strip() and self.azure_voice.strip())
        if self.provider == "elevenlabs":
            return bool(self.elevenlabs_voice.strip() and self.elevenlabs_model.strip())
        return True


def _keyring_get(service: str, username: str) -> str:
    if keyring is None:
        return ""
    try:
        return keyring.get_password(service, username) or ""
    except Exception:
        return ""


def _read_keys(
    parser: configparser.ConfigParser,
    section: str,
    service: str,
    legacy_username: str,
    keys_username: str,
) -> tuple[str, ...]:
    if not parser.has_section(section):
        return ()
    list_value = parser.get(section, "api_keys", fallback="").strip()
    legacy_value = parser.get(section, "api_key", fallback="").strip()
    if list_value == KEYRING_MARKER:
        list_value = _keyring_get(service, keys_username)
    if legacy_value == KEYRING_MARKER:
        legacy_value = _keyring_get(service, legacy_username)
    return _merge_keys(parse_api_keys(list_value), legacy_value)


def _read(path: Path) -> OpenRouterConfig | None:
    if not path.exists():
        return None
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    if not parser.has_section("openrouter") and not parser.has_section("voice"):
        return None
    openrouter_keys = _read_keys(parser, "openrouter", KEYRING_SERVICE, KEYRING_USERNAME, KEYRING_KEYS_USERNAME)
    azure_keys = _read_keys(parser, "azure_speech", AZURE_KEYRING_SERVICE, AZURE_KEYRING_USERNAME, AZURE_KEYRING_KEYS_USERNAME)
    elevenlabs_keys = _read_keys(parser, "elevenlabs", ELEVENLABS_KEYRING_SERVICE, ELEVENLABS_KEYRING_USERNAME, ELEVENLABS_KEYRING_KEYS_USERNAME)
    return OpenRouterConfig(
        provider=parser.get("voice", "provider", fallback="flux").strip().lower() if parser.has_section("voice") else "flux",
        api_keys=openrouter_keys,
        api_key_mode=parser.get("openrouter", "key_mode", fallback="single"),
        model=parser.get("openrouter", "model", fallback="deepgram/flux-tts:free").strip(),
        voice=valid_flux_voice(parser.get("openrouter", "voice", fallback=DEFAULT_FLUX_VOICE).strip()),
        http_referer=parser.get("openrouter", "http_referer", fallback="").strip(),
        source=path,
        azure_keys=azure_keys,
        azure_key_mode=parser.get("azure_speech", "key_mode", fallback="single") if parser.has_section("azure_speech") else "single",
        azure_region=parser.get("azure_speech", "region", fallback="uksouth").strip() if parser.has_section("azure_speech") else "uksouth",
        azure_voice=parser.get("azure_speech", "voice", fallback="en-GB-SoniaNeural").strip() if parser.has_section("azure_speech") else "en-GB-SoniaNeural",
        elevenlabs_keys=elevenlabs_keys,
        elevenlabs_key_mode=parser.get("elevenlabs", "key_mode", fallback="single") if parser.has_section("elevenlabs") else "single",
        elevenlabs_voice=parser.get("elevenlabs", "voice", fallback="JBFqnCBsd6RMkjVDRZzb").strip() if parser.has_section("elevenlabs") else "JBFqnCBsd6RMkjVDRZzb",
        elevenlabs_model=parser.get("elevenlabs", "model", fallback="eleven_multilingual_v2").strip() if parser.has_section("elevenlabs") else "eleven_multilingual_v2",
    )


def load_openrouter_config() -> OpenRouterConfig:
    """Prefer AutoTok config, then reuse the sibling aistory configuration."""
    for path in (CONFIG_PATH, LEGACY_CONFIG_PATH):
        loaded = _read(path)
        if loaded and (loaded.configured or path == CONFIG_PATH):
            return loaded
    return OpenRouterConfig(source=CONFIG_PATH)


def _store_keys(service: str, legacy_username: str, keys_username: str, keys: tuple[str, ...]) -> tuple[str, str]:
    if keys and keyring is not None:
        try:
            keyring.set_password(service, keys_username, json.dumps(list(keys)))
            keyring.set_password(service, legacy_username, keys[0])
            return KEYRING_MARKER, KEYRING_MARKER
        except Exception:
            pass
    return (json.dumps(list(keys)) if keys else ""), (keys[0] if keys else "")


def save_openrouter_config(config: OpenRouterConfig) -> Path:
    openrouter_stored, openrouter_legacy = _store_keys(KEYRING_SERVICE, KEYRING_USERNAME, KEYRING_KEYS_USERNAME, config.api_keys)
    azure_stored, azure_legacy = _store_keys(AZURE_KEYRING_SERVICE, AZURE_KEYRING_USERNAME, AZURE_KEYRING_KEYS_USERNAME, config.azure_keys)
    elevenlabs_stored, elevenlabs_legacy = _store_keys(ELEVENLABS_KEYRING_SERVICE, ELEVENLABS_KEYRING_USERNAME, ELEVENLABS_KEYRING_KEYS_USERNAME, config.elevenlabs_keys)
    parser = configparser.ConfigParser()
    parser["voice"] = {"provider": config.provider}
    parser["openrouter"] = {
        "api_keys": openrouter_stored, "api_key": openrouter_legacy,
        "key_mode": config.api_key_mode,
        "model": config.model.strip() or "deepgram/flux-tts:free",
        "voice": valid_flux_voice(config.voice.strip()),
        "http_referer": config.http_referer.strip(),
    }
    parser["azure_speech"] = {
        "api_keys": azure_stored, "api_key": azure_legacy,
        "key_mode": config.azure_key_mode,
        "region": config.azure_region.strip() or "uksouth",
        "voice": config.azure_voice.strip() or "en-GB-SoniaNeural",
    }
    parser["elevenlabs"] = {
        "api_keys": elevenlabs_stored, "api_key": elevenlabs_legacy,
        "key_mode": config.elevenlabs_key_mode,
        "voice": config.elevenlabs_voice.strip() or "JBFqnCBsd6RMkjVDRZzb",
        "model": config.elevenlabs_model.strip() or "eleven_multilingual_v2",
    }
    with CONFIG_PATH.open("w", encoding="utf-8") as handle:
        parser.write(handle)
    return CONFIG_PATH


def migrate_plaintext_secret() -> bool:
    """Move existing plaintext provider keys into Windows Credential Manager."""
    if keyring is None or not CONFIG_PATH.exists():
        return False
    parser = configparser.ConfigParser()
    parser.read(CONFIG_PATH, encoding="utf-8")
    sections = ("openrouter", "azure_speech", "elevenlabs")
    plaintext_found = any(
        parser.has_section(section)
        and any(
            value and value != KEYRING_MARKER
            for value in (
                parser.get(section, "api_key", fallback="").strip(),
                parser.get(section, "api_keys", fallback="").strip(),
            )
        )
        for section in sections
    )
    if not plaintext_found:
        return False
    current = _read(CONFIG_PATH)
    if not current:
        return False
    save_openrouter_config(current)
    return True
