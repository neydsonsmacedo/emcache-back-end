"""Channel-agnostic agent orchestration.

Entry:  handle_message(user_id, channel, message | media)
Output: AgentResponse(reply, transaction, actions, ...)
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

from langchain.agents import create_agent
from langchain.agents.middleware import ModelFallbackMiddleware
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app import db, financial_service as fs, llm_client, llm_registry
from app.llm_metrics import LLMMetricsCallbackHandler, MetricsRecorder
from app.agent_prompt import build_system_prompt
from app.agent_tools import ToolContext, build_tools, dump_result

logger = logging.getLogger(__name__)

RECURSION_LIMIT = 12  # ~5 tool rounds
HISTORY_LIMIT = 20
FALLBACK_REPLY = "Desculpe, tive um problema ao processar sua mensagem. Pode tentar novamente?"


@dataclass
class Media:
    kind: str  # 'audio' | 'image'
    data: bytes
    mime_type: str
    filename: str | None = None


@dataclass
class AgentResponse:
    reply: str
    session_id: str
    input_type: str = "text"
    transcript: str | None = None
    transaction: dict[str, Any] | None = None
    actions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reply": self.reply,
            "session_id": self.session_id,
            "input_type": self.input_type,
            "transcript": self.transcript,
            "transaction": self.transaction,
            "actions": self.actions,
        }


async def _normalize_input(
    message: str | None,
    media: Media | None,
    *,
    user_id: UUID | str,
    recorder: MetricsRecorder,
) -> tuple[str, str, str | None]:
    """Return (text_for_llm, input_type, transcript)."""
    text = (message or "").strip()
    if media is None:
        return text, "text", None
    if media.kind == "audio":
        chain = await llm_registry.resolve_llm_chain(user_id, "transcription")
        transcript = await llm_client.transcribe_audio(
            media.data,
            chain=chain,
            recorder=recorder,
            filename=media.filename or "audio.ogg",
            mime_type=media.mime_type,
        )
        return transcript, "audio", transcript
    if media.kind == "image":
        chain = await llm_registry.resolve_llm_chain(user_id, "vision")
        description = await llm_client.describe_image(
            media.data,
            chain=chain,
            recorder=recorder,
            mime_type=media.mime_type,
            caption=text or None,
        )
        combined = f"[Imagem de comprovante enviada]\n{description}"
        if text:
            combined += f"\nMensagem do usuário: {text}"
        return combined, "image", description
    raise ValueError(f"Tipo de mídia inválido: {media.kind}")


async def _load_pending(session: dict[str, Any], user_id: UUID | str) -> dict[str, Any] | None:
    pending_id = session.get("pending_transaction_id")
    if not pending_id:
        return None
    tx = await fs.get_manual_transaction(pending_id, user_id=user_id)
    if tx is None or tx["status"] != "pendente":
        await db.set_session_pending_transaction(session["id"], None)
        return None
    return tx


def _history_messages(rows: list[dict[str, Any]]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for r in rows:
        if r["role"] == "user":
            out.append(HumanMessage(content=r["content"]))
        elif r["role"] == "assistant":
            out.append(AIMessage(content=r["content"]))
    return out


def _text_of(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content.strip()
    return "".join(
        b.get("text", "") if isinstance(b, dict) else str(b) for b in content
    ).strip()


async def _sync_session_pending(
    session_id: UUID | str, ctx: ToolContext, previous_pending_id: str | None
) -> dict[str, Any] | None:
    """Update the session's pending transaction based on what tools touched. Returns final tx."""
    if not ctx.touched_transactions:
        return None
    last = ctx.touched_transactions[-1]
    if last["status"] == "pendente":
        await db.set_session_pending_transaction(session_id, last["id"])
    elif previous_pending_id and str(last["id"]) == previous_pending_id:
        await db.set_session_pending_transaction(session_id, None)
    return last


async def handle_message(
    *,
    user_id: UUID | str,
    channel: str,
    message: str | None = None,
    media: Media | None = None,
    external_session_id: str | None = None,
    today: date | None = None,
) -> AgentResponse:
    """Process one user message and persist run/LLM metrics (never breaks the reply)."""
    recorder = MetricsRecorder(user_id=user_id, channel=channel)
    try:
        response = await _handle(
            recorder=recorder,
            user_id=user_id,
            channel=channel,
            message=message,
            media=media,
            external_session_id=external_session_id,
            today=today,
        )
    except Exception as exc:
        recorder.status = "error"
        recorder.set_error(exc)
        await recorder.flush()
        raise
    await recorder.flush()
    return response


async def _handle(
    *,
    recorder: MetricsRecorder,
    user_id: UUID | str,
    channel: str,
    message: str | None,
    media: Media | None,
    external_session_id: str | None,
    today: date | None,
) -> AgentResponse:
    session = await db.get_or_create_chat_session(
        user_id=user_id, channel=channel, external_session_id=external_session_id
    )
    session_id = session["id"]
    recorder.session_id = str(session_id)

    try:
        text, input_type, transcript = await _normalize_input(
            message, media, user_id=user_id, recorder=recorder
        )
    except (llm_client.LLMError, ValueError) as exc:
        logger.warning("Falha ao processar mídia: %s", exc)
        recorder.status = "error"
        recorder.set_error(exc)
        return AgentResponse(
            reply="Não consegui entender o arquivo enviado. Pode mandar em texto?",
            session_id=str(session_id),
        )
    recorder.input_type = input_type
    if not text:
        return AgentResponse(
            reply="Não recebi nenhum conteúdo. Pode repetir?", session_id=str(session_id)
        )

    pending = await _load_pending(session, user_id)
    history = await db.list_chat_messages(session_id, limit=HISTORY_LIMIT)
    institutions = await db.list_institutions()

    system = build_system_prompt(
        today=today or date.today(), institutions=institutions, pending=pending
    )
    messages: list[BaseMessage] = [*_history_messages(history), HumanMessage(content=text)]

    await db.append_chat_message(
        session_id=session_id,
        user_id=user_id,
        role="user",
        content=text,
        channel=channel,
        input_type=input_type,
    )

    pending_id = str(pending["id"]) if pending else None
    ctx = ToolContext(
        user_id=user_id,
        channel=channel,
        input_type=input_type,
        raw_input=text,
        pending_transaction_id=pending_id,
    )
    actions: list[dict[str, Any]] = []
    reply = ""

    try:
        chain = await llm_registry.resolve_llm_chain(user_id, "chat")
        if not chain:
            raise llm_client.LLMError("Nenhum provedor de LLM configurado")
        models = [llm_client.build_chat_model(t, "chat", index=i) for i, t in enumerate(chain)]
        middleware = [ModelFallbackMiddleware(*models[1:])] if len(models) > 1 else []
        agent = create_agent(
            models[0],
            tools=build_tools(ctx),
            system_prompt=system,
            middleware=middleware,
        )
        result = await agent.ainvoke(
            {"messages": messages},
            config={
                "recursion_limit": RECURSION_LIMIT,
                "run_name": "otto_agent",
                "callbacks": [LLMMetricsCallbackHandler(recorder)],
            },
        )
        new_messages = result["messages"][len(messages):]
        for m in new_messages:
            if isinstance(m, ToolMessage):
                actions.append({"tool": m.name, "ok": '"error"' not in str(m.content)})
        for m in reversed(new_messages):
            if isinstance(m, AIMessage) and not m.tool_calls:
                reply = _text_of(m)
                break
    except Exception as exc:
        logger.exception("Erro na execução do agente")
        recorder.set_error(exc)
        reply = ""

    if not reply:
        reply = FALLBACK_REPLY
        recorder.status = "fallback_reply"

    tx = await _sync_session_pending(session_id, ctx, pending_id)
    if tx:
        recorder.transaction_id = str(tx["id"])
    tx_meta = (
        {
            "id": str(tx["id"]),
            "status": tx["status"],
            "dados_faltantes": tx["dados_faltantes"] or [],
        }
        if tx
        else None
    )
    await db.append_chat_message(
        session_id=session_id,
        user_id=user_id,
        role="assistant",
        content=reply,
        channel=channel,
        metadata={"actions": actions, "transaction": tx_meta},
    )

    return AgentResponse(
        reply=reply,
        session_id=str(session_id),
        input_type=input_type,
        transcript=transcript,
        transaction=json.loads(dump_result(tx)) if tx else None,
        actions=actions,
    )
