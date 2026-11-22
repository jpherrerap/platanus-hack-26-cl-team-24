"""Turns attachments into text so the guard can cite them like any other draft text."""
import base64
import binascii
import hashlib
import os
from typing import Literal

from pydantic import Field

from .claude import _request
from .model import Model
from .prompts import TRANSCRIBE_PROMPT

VISUAL = {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf"}
AUDIO = {"audio/mpeg", "audio/wav", "audio/ogg", "audio/webm", "audio/mp4"}


class Attachment(Model):
    name: str = Field(min_length=1, max_length=200)
    media_type: Literal[tuple(sorted(VISUAL | AUDIO))]
    # Anthropic caps images at 5 MB; base64 adds a third.
    data: str = Field(min_length=1, max_length=7_000_000)

    def raw(self):
        try:
            return base64.b64decode(self.data, validate=True)
        except binascii.Error as exc:
            raise ValueError("Adjunto con base64 inválido") from exc

    def digest(self):
        return hashlib.sha256(self.raw()).hexdigest()


class Transcript(Model):
    text: str = Field(min_length=1, max_length=12000)


def stt_key():
    if os.environ.get("ELEVENLABS_API_KEY"):
        return os.environ["ELEVENLABS_API_KEY"]
    try:
        import keyring
        return keyring.get_password("isde-guard", "elevenlabs")
    except Exception:
        return None


def transcribe(item: Attachment, *, http=None) -> str:
    if item.media_type in VISUAL:
        kind = "document" if item.media_type == "application/pdf" else "image"
        block = {"type": kind, "source": {"type": "base64", "media_type": item.media_type, "data": item.data}}
        return _request(TRANSCRIBE_PROMPT, {"adjunto": item.name}, Transcript, None, None, media=[block]).text
    key = stt_key()
    if not key:
        raise ValueError("Configura ELEVENLABS_API_KEY para transcribir audio")
    if http is None:
        import httpx
        http = httpx.Client(timeout=60)
    try:
        response = http.post("https://api.elevenlabs.io/v1/speech-to-text", headers={"xi-api-key": key},
                             data={"model_id": os.environ.get("ISDE_STT_MODEL", "scribe_v2")},
                             files={"file": (item.name, item.raw(), item.media_type)})
    except Exception as exc:
        raise ValueError("ElevenLabs no respondió") from exc
    if response.status_code != 200:
        raise ValueError(f"ElevenLabs no transcribió el audio ({response.status_code})")
    return Transcript(text=response.json()["text"]).text


def with_attachments(draft: str, attachments: list[Attachment], **kwargs) -> str | None:
    """None means an audio could not be transcribed; the caller must not deliver it."""
    # shortcut: the recipient's inbox shows the transcript, not the file; the integration delivers the bytes it sent.
    parts = [draft]
    for item in attachments:
        try:
            parts.append(f"[Adjunto: {item.name}]\n{transcribe(item, **kwargs)}")
        except ValueError:
            # Speech-to-text is optional: without it every audio is rejected instead of failing the request.
            if item.media_type in AUDIO:
                return None
            raise
    return "\n\n".join(parts)
