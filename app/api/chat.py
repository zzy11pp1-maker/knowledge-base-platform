"""同步问答和真正的 SSE；身份验证在发送响应头之前完成。"""

import json
import logging
from contextlib import aclosing

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sse_starlette import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, require
from app.core.business_dependencies import get_knowledge_service
from app.core.exceptions import PlatformError
from app.db.models import Conversation, KnowledgeBase, KnowledgeBaseMember
from app.db.session import session_factory
from app.integrations.llm_client import CompatibleLLMClient
from app.schemas.business import ChatInput, ChatOutput, ConversationCreate, ConversationOutput
from app.services.access_control import kb_access
from app.services.chat import ChatService
from app.services.conversations import readable_messages

router = APIRouter()
LOGGER = logging.getLogger(__name__)


def get_chat_service(knowledge=Depends(get_knowledge_service)):
    return ChatService(knowledge, CompatibleLLMClient())


@router.post("/conversations", response_model=ConversationOutput, status_code=201)
def create_conversation(body: ConversationCreate, user: Principal = Depends(require("chat:use"))):
    with session_factory()() as db:
        kb_access(db, user, body.kb_id)
        row = Conversation(tenant_id=user.tenant_id, user_id=user.id, **body.model_dump())
        db.add(row)
        db.commit()
        return row


@router.get("/conversations", response_model=list[ConversationOutput])
def conversations(user: Principal = Depends(require("chat:use"))):
    with session_factory()() as db:
        return list(
            db.scalars(
                select(Conversation)
                .join(KnowledgeBase)
                .join(KnowledgeBaseMember)
                .where(
                    Conversation.tenant_id == user.tenant_id,
                    Conversation.user_id == user.id,
                    KnowledgeBase.is_deleted.is_(False),
                    KnowledgeBaseMember.user_id == user.id,
                )
                .order_by(Conversation.created_at)
            )
        )


@router.get("/knowledge-bases/{kb_id}/suggestions")
def suggestions(kb_id: str, user: Principal = Depends(require("chat:use"))):
    """只从当前用户仍可访问的已发布 FAQ 生成问题建议。"""

    from app.db.models import FAQEntry
    from app.services.faq import FAQService
    knowledge = get_knowledge_service()
    service = FAQService(knowledge.embedder)
    with session_factory()() as db:
        kb_access(db, user, kb_id)
        entries = list(db.scalars(select(FAQEntry).where(
            FAQEntry.tenant_id == user.tenant_id, FAQEntry.kb_id == kb_id,
            FAQEntry.enabled.is_(True),
        ).order_by(FAQEntry.hit_count.desc()).limit(8)))
        return [{"id": row.id, "question": row.question} for row in entries
                if service.safe_citations(db, user, row)]


@router.get("/conversations/{conversation_id}/messages")
def messages(conversation_id: str, user: Principal = Depends(require("chat:use"))):
    from app.core.auth import enforce

    enforce(user, "knowledge:read")
    with session_factory()() as db:
        return readable_messages(db, user, conversation_id)


@router.post("/chat", response_model=ChatOutput)
async def chat(
    body: ChatInput, user: Principal = Depends(require("chat:use")), service=Depends(get_chat_service)
):
    turn = await run_in_threadpool(service.begin, user, body)
    async with aclosing(service.events(user, body, turn)) as events:
        async for event, data in events:
            if event == "done":
                return data
    raise HTTPException(502, "问答未完成")


@router.post("/conversations/{conversation_id}/messages", response_model=ChatOutput)
async def send_message(
    conversation_id: str,
    body: ChatInput,
    user: Principal = Depends(require("chat:use")),
    service=Depends(get_chat_service),
):
    if body.conversation_id is not None and body.conversation_id != conversation_id:
        raise HTTPException(422, "会话参数不一致")
    return await chat(body.model_copy(update={"conversation_id": conversation_id}), user, service)


@router.post("/chat/stream")
async def chat_stream(
    body: ChatInput, user: Principal = Depends(require("chat:use")), service=Depends(get_chat_service)
):
    turn = await run_in_threadpool(service.begin, user, body)

    async def generate():
        try:
            async with aclosing(service.events(user, body, turn)) as events:
                async for event, data in events:
                    yield {"event": event, "data": json.dumps(data, ensure_ascii=False)}
        except (PlatformError, HTTPException) as exc:
            code = exc.code if isinstance(exc, PlatformError) else "access_or_request_error"
            LOGGER.warning("流式回答失败 code=%s", code)
            yield {
                "event": "error",
                "data": json.dumps(
                    {"code": code, "message": "回答未完成，请检查服务或访问权限"}, ensure_ascii=False
                ),
            }
        except Exception:
            LOGGER.error("流式回答发生未预期错误")
            yield {
                "event": "error",
                "data": json.dumps({"code": "internal_error", "message": "回答未完成"}, ensure_ascii=False),
            }

    return EventSourceResponse(
        generate(),
        ping=15,
        send_timeout=30,
        headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
    )
