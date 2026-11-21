"""LLM / transcription / vision clients (OpenAI-compatible) with provider fallback."""

import base64
import logging
import time
from typing import Any

import httpx
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.config import settings
from app.llm_metrics import LLMMetricsCallbackHandler, MetricsRecorder, _error_info
from app.llm_registry import LLMError, LLMTarget

logger = logging.getLogger(__name__)

__all__ = [
    "LLMError",
    "LLMTarget",
    "build_chat_model",
    "transcribe_audio",
    "describe_image",
]


def build_chat_model(
    target: LLMTarget,
    capability: str = "chat",
    *,
    index: int = 0,
    temperature: float = 0.2,
    timeout: float | None = None,
) -> ChatOpenAI:
    """LangChain chat model for one target; metadata lets the metrics handler identify it."""
    model = target.model_for(capability)
    if not model:
        raise LLMError(f"Target sem modelo para {capability}")
    return ChatOpenAI(
        model=model,
        api_key=target.api_key,
        base_url=target.base_url,
        temperature=temperature,
        timeout=timeout or settings.llm_request_timeout_seconds,
        max_retries=1,
        metadata={
            "operation": capability,
            "target_index": index,
            "provider": target.provider,
            "config_id": str(target.config_id) if target.config_id else None,
            "model": model,
        },
    )


async def _call_transcription(
    client: httpx.AsyncClient,
    target: LLMTarget,
    model: str | None,
    audio: bytes,
    filename: str,
    mime_type: str,
    language: str,
) -> httpx.Response:
    base = target.base_url.rstrip("/")
    files = {"file": (filename, audio, mime_type)}
    if target.provider == "elevenlabs":
        lang = "por" if language == "pt" else language
        return await client.post(
            f"{base}/speech-to-text",
            headers={"xi-api-key": target.api_key},
            data={"model_id": model, "language_code": lang},
            files=files,
        )
    return await client.post(
        f"{base}/audio/transcriptions",
        headers={"Authorization": f"Bearer {target.api_key}"},
        data={"model": model, "language": language},
        files=files,
    )


async def transcribe_audio(
    audio: bytes,
    *,
    chain: list[LLMTarget],
    recorder: MetricsRecorder | None = None,
    filename: str = "audio.ogg",
    mime_type: str = "audio/ogg",
    language: str = "pt",
    timeout: float = 90.0,
) -> str:
    """Transcribe audio (OpenAI-compatible or ElevenLabs), trying each target in order."""
    if not chain:
        raise LLMError("Nenhum provedor de transcrição configurado")

    last_error: Exception | None = None
    for index, target in enumerate(chain):
        model = target.model_for("transcription")
        started = time.perf_counter()
        base_call = {
            "operation": "transcription",
            "provider": target.provider,
            "model": model,
            "llm_config_id": target.config_id,
            "attempt": index + 1,
            "is_fallback": index > 0,
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await _call_transcription(
                    client, target, model, audio, filename, mime_type, language
                )
            if resp.status_code >= 400:
                err = LLMError(f"Transcrição HTTP {resp.status_code}: {resp.text[:300]}")
                err.status_code = resp.status_code  # type: ignore[attr-defined]
                raise err
            text = (resp.json().get("text") or "").strip()
        except Exception as exc:
            last_error = exc
            logger.warning("Transcrição falhou em %s/%s: %s", target.provider, model, exc)
            if recorder:
                recorder.record_call(
                    **base_call,
                    status="error",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    **_error_info(exc),
                )
            continue

        if recorder:
            recorder.record_call(
                **base_call, latency_ms=int((time.perf_counter() - started) * 1000)
            )
        return text

    raise LLMError(f"Transcrição falhou em todos os provedores: {last_error}")


_VISION_PROMPT = (
    "Você analisa comprovantes, notas fiscais e recibos brasileiros. Descreva em português, "
    "de forma objetiva, os dados financeiros visíveis: estabelecimento, itens principais, "
    "valor total (R$), data, forma de pagamento (crédito/débito/pix/dinheiro) e banco/cartão "
    "se aparecerem. Não invente informações ausentes; diga 'não informado' quando faltar."
)


async def describe_image(
    image: bytes,
    *,
    chain: list[LLMTarget],
    recorder: MetricsRecorder | None = None,
    mime_type: str = "image/jpeg",
    caption: str | None = None,
    timeout: float = 90.0,
) -> str:
    """Extract financial details from a receipt image, falling back across targets."""
    if not chain:
        raise LLMError("Nenhum provedor de visão configurado")

    data_uri = f"data:{mime_type};base64,{base64.b64encode(image).decode('ascii')}"
    text = _VISION_PROMPT + (f"\nLegenda do usuário: {caption}" if caption else "")
    message = HumanMessage(
        content=[
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]
    )
    config: dict[str, Any] = {}
    if recorder:
        config["callbacks"] = [LLMMetricsCallbackHandler(recorder)]

    last_error: Exception | None = None
    for index, target in enumerate(chain):
        try:
            result = await build_chat_model(
                target, "vision", index=index, timeout=timeout
            ).ainvoke([message], config=config)
        except Exception as exc:
            last_error = exc
            logger.warning("Visão falhou em %s: %s", target.provider, exc)
            continue
        content = result.content
        return (content if isinstance(content, str) else str(content)).strip()

    raise LLMError(f"Visão falhou em todos os provedores: {last_error}")
