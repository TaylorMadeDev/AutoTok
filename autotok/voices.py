from __future__ import annotations


# Deepgram Flux TTS English catalog. Keep exact API identifiers in the UI so
# saved preferences can be sent directly to OpenRouter's speech endpoint.
FLUX_VOICES: tuple[str, ...] = (
    "flux-alexis-en",
    "flux-bree-en",
    "flux-brittany-en",
    "flux-brooke-en",
    "flux-bruce-en",
    "flux-cliff-en",
    "flux-cole-en",
    "flux-colin-en",
    "flux-conor-en",
    "flux-donovan-en",
    "flux-drew-en",
    "flux-elise-en",
    "flux-gemma-en",
    "flux-haley-en",
    "flux-hannah-en",
    "flux-heather-en",
    "flux-jack-en",
    "flux-kai-en",
    "flux-kelsey-en",
    "flux-kit-en",
    "flux-maeve-en",
    "flux-marcelo-en",
    "flux-marcus-en",
    "flux-meena-en",
    "flux-meghan-en",
    "flux-miles-en",
    "flux-naveen-en",
    "flux-paige-en",
    "flux-priya-en",
    "flux-rufus-en",
    "flux-sean-en",
    "flux-sharon-en",
    "flux-sienna-en",
    "flux-tanner-en",
    "flux-wade-en",
    "flux-wes-en",
)

DEFAULT_FLUX_VOICE = "flux-wes-en"


def valid_flux_voice(value: str) -> str:
    return value if value in FLUX_VOICES else DEFAULT_FLUX_VOICE
