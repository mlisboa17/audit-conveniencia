"""
Micro-serviço backend em FastAPI para auditoria operacional diária de lojas de conveniência
via WhatsApp (Evolution API) em GRUPOS (@g.us) e visão computacional (Google Gemini 2.0 Flash).
Inclui travas anti-fraude (bloqueio de fotos encaminhadas), validação estrita de ambiente
e rota PWA de captura ao vivo com trava no sensor de câmera do celular.
"""

import json
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any, Dict, Optional

import os
from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import (
    Alert,
    Audit,
    AuditTicket,
    Operator,
    Store,
    async_session_factory,
    add_points_to_operator,
    aplicar_correcao_ticket,
    assign_operator_to_audit,
    buscar_ticket_aberto,
    buscar_ticket_por_id_ou_prefixo,
    buscar_tickets_abertos_loja,
    calculate_audit_points,
    contar_bonus_correcao_turno,
    criar_tickets_pendencia,
    fechar_ticket,
    find_store_by_group_jid,
    get_all_stores,
    get_db_session,
    get_or_create_operator,
    get_ranking_data,
    get_recent_unassigned_audit,
    get_store_by_id,
    has_valid_daily_audit,
    init_db,
    justificar_ticket,
    resolve_shift,
    save_alert,
    save_audit,
    update_store_group_jid,
)
from scheduler import (
    check_afternoon_audits_tolerance_1645,
    check_critical_unresponsive_stores_1700,
    send_executive_summary_1715,
    setup_scheduler,
)
from services.evolution_service import evolution_service
from services.gemini_service import gemini_service

# Configuração de Logs
logging.basicConfig(
    level=logging.INFO if settings.DEBUG else logging.WARNING,
    format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("audit.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Gerenciador de ciclo de vida da aplicação FastAPI:
    1. Inicializa o banco de dados e sincroniza os grupos das lojas.
    2. Configura e inicia o agendador de tarefas em background (APScheduler).
    3. Finaliza conexões e rotinas no encerramento.
    """
    logger.info(">>> Iniciando Micro-serviço de Auditoria Operacional (Travas Anti-Fraude & Grupos)...")
    
    # Inicializa banco de dados SQLite e sincroniza grupos
    await init_db()

    # Inicializa agendador de tarefas
    sched = setup_scheduler()
    sched.start()
    logger.info(">>> APScheduler ativo e monitorando horários 16h45, 17h00 e 17h15.")

    yield

    logger.info(">>> Encerrando Micro-serviço e finalizando o agendador...")
    sched.shutdown()


app = FastAPI(
    title="Audit Conveniência - Micro-serviço Operacional com Anti-Fraude",
    description=(
        "Backend para auditoria operacional de lojas de conveniência via Grupos de WhatsApp "
        "(Evolution API), PWA de captura com trava de sensor e visão computacional (Google Gemini 2.0 Flash)."
    ),
    version="2.1.0",
    lifespan=lifespan,
)

# Configuração de CORS para dashboards e integrações
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class UpdateGroupPayload(BaseModel):
    whatsapp_group_jid: str = Field(
        ...,
        description="ID do grupo de WhatsApp da loja (ex: '120363430718119886@g.us')",
    )


@app.get("/", tags=["Geral"])
async def root():
    """Endpoint raiz com informações e links para a documentação."""
    return {
        "service": "Audit Conveniência - Anti-Fraude & IA Operacional",
        "version": "2.1.0",
        "mode": "WhatsApp Groups (@g.us) + Câmera ao Vivo PWA",
        "anti_fraud": {
            "block_forwarded_photos": True,
            "strict_environment_validation": True,
            "live_camera_capture": True,
        },
        "gemini_model": settings.GEMINI_MODEL,
        "docs_url": "/docs",
        "health_url": "/health",
        "current_time": datetime.now().isoformat(),
    }


@app.get("/health", tags=["Geral"])
async def health_check(session: AsyncSession = Depends(get_db_session)):
    """Verificação de integridade operacional do micro-serviço e banco de dados."""
    stores = await get_all_stores(session)
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "database": "connected",
        "stores_count": len(stores),
        "director_phone": settings.DIRECTOR_PHONE,
        "evolution_api_url": settings.EVOLUTION_API_URL,
        "stores": [
            {"id": s.id, "name": s.name, "group_jid": s.whatsapp_group_jid}
            for s in stores
        ],
    }


@app.get("/test-group", tags=["WhatsApp & Grupos"])
async def send_test_message_to_group(group_jid: Optional[str] = None):
    """
    Envia uma mensagem de confirmação para um grupo (padrão: grupo da Loja 1).
    """
    if not group_jid:
        async with async_session_factory() as _s:
            _store = await _s.get(Store, 1)
            group_jid = _store.whatsapp_group_jid if _store else "120363430718119886@g.us"
    target_group = group_jid
    message = "✅ Bot de Auditoria Conectado e Monitorando este grupo! Podem enviar a foto da auditoria para análise."
    logger.info(f"[Test Group] Enviando mensagem de teste para o grupo {target_group}...")
    success = await evolution_service.send_text_message(target_group, message)
    if success:
        return {
            "status": "success",
            "message": "Mensagem de teste enviada com sucesso ao grupo!",
            "group_jid": target_group,
            "content": message,
        }
    else:
        return {
            "status": "error",
            "message": "Não foi possível enviar a mensagem ao grupo. Verifique a conexão com a Evolution API.",
            "group_jid": target_group,
        }


@app.get("/groups", tags=["WhatsApp & Grupos"])
async def list_whatsapp_groups():
    """Lista todos os grupos do WhatsApp disponíveis na Evolution API."""
    groups = await evolution_service.fetch_all_groups()
    return {
        "count": len(groups),
        "groups": groups,
        "instructions": (
            "Copie o 'id' do grupo desejado (ex: '120363xxx@g.us') e associe à loja "
            "via PUT /api/stores/{store_id}/group."
        ),
    }


# ==========================================================
# DIAGNÓSTICO DO WEBHOOK (últimas chamadas em memória)
# ==========================================================
from collections import deque
from fastapi.responses import Response as _RawResponse

_WEBHOOK_DEBUG: "deque[Dict[str, Any]]" = deque(maxlen=30)


def _resumir_payload_webhook(raw: bytes) -> Dict[str, Any]:
    """Extrai apenas metadados do payload (sem base64/mídia) para diagnóstico."""
    try:
        p = json.loads(raw or b"{}")
    except Exception:
        return {"parse_error": True, "size": len(raw or b"")}
    data = p.get("data", {}) if isinstance(p, dict) else {}
    data = data if isinstance(data, dict) else {}
    key = data.get("key", {}) if isinstance(data.get("key"), dict) else {}
    msg = data.get("message", {}) if isinstance(data.get("message"), dict) else {}
    img = msg.get("imageMessage", {}) if isinstance(msg.get("imageMessage"), dict) else {}
    return {
        "event": p.get("event") if isinstance(p, dict) else None,
        "instance": p.get("instance") if isinstance(p, dict) else None,
        "remoteJid": key.get("remoteJid"),
        "fromMe": key.get("fromMe"),
        "messageType": data.get("messageType"),
        "message_keys": list(msg.keys())[:10],
        "has_base64": bool(msg.get("base64") or img.get("base64")),
        "has_image_url": bool(img.get("url") or data.get("mediaUrl")),
        "size": len(raw or b""),
    }


@app.middleware("http")
async def _registrar_webhook_debug(request: Request, call_next):
    if request.url.path != "/webhook/evolution" or request.method != "POST":
        return await call_next(request)
    raw = await request.body()
    entry: Dict[str, Any] = {"at": datetime.utcnow().isoformat(), **_resumir_payload_webhook(raw)}
    try:
        response = await call_next(request)
    except Exception as exc:
        entry.update({"status": 500, "error": repr(exc)[:500]})
        _WEBHOOK_DEBUG.appendleft(entry)
        raise
    body = b""
    async for chunk in response.body_iterator:
        body += chunk
    entry.update({"status": response.status_code, "response": body[:500].decode("utf-8", "ignore")})
    _WEBHOOK_DEBUG.appendleft(entry)
    return _RawResponse(
        content=body,
        status_code=response.status_code,
        headers=dict(response.headers),
        media_type=response.media_type,
    )


@app.get("/debug/webhooks", tags=["WhatsApp & Webhook"])
async def debug_webhooks():
    """Últimas chamadas recebidas em /webhook/evolution (apenas metadados, sem mídia)."""
    return {"count": len(_WEBHOOK_DEBUG), "items": list(_WEBHOOK_DEBUG)}


async def _evo_call(method: str, path: str, json_body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    import httpx
    url = f"{settings.EVOLUTION_API_URL.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.request(method, url, headers={"apikey": settings.EVOLUTION_API_KEY}, json=json_body)
        try:
            body = r.json()
        except Exception:
            body = r.text[:500]
        return {"http": r.status_code, "body": body}
    except Exception as e:
        return {"http": None, "error": repr(e)[:300]}


WEBHOOK_PUBLIC_URL = "https://auditoria-lojas-auditoria-stack.h43pzs.easypanel.host/webhook/evolution"

INSTANCE_SETTINGS_OFF = {
    "rejectCall": False,
    "msgCall": "",
    "groupsIgnore": False,
    "alwaysOnline": False,
    "readMessages": False,
    "syncFullHistory": False,
    "readStatus": False,
}


@app.get("/debug/evolution", tags=["WhatsApp & Webhook"])
async def debug_evolution():
    """Estado da instância, webhook e settings gravados na Evolution (usa a chave do servidor)."""
    inst = settings.EVOLUTION_INSTANCE_NAME
    return {
        "instance": inst,
        "evolution_url": settings.EVOLUTION_API_URL,
        "api_key_configured": bool(settings.EVOLUTION_API_KEY),
        "connection_state": await _evo_call("GET", f"/instance/connectionState/{inst}"),
        "webhook": await _evo_call("GET", f"/webhook/find/{inst}"),
        "settings": await _evo_call("GET", f"/settings/find/{inst}"),
    }


@app.post("/debug/evolution/fix-webhook", tags=["WhatsApp & Webhook"])
async def fix_evolution_webhook(url: Optional[str] = None):
    """Grava webhook (MESSAGES_UPSERT + base64) e desliga todos os comportamentos automáticos da instância."""
    inst = settings.EVOLUTION_INSTANCE_NAME
    target = url or WEBHOOK_PUBLIC_URL
    body = {
        "webhook": {
            "enabled": True,
            "url": target,
            "byEvents": False,
            "base64": True,
            "events": ["MESSAGES_UPSERT"],
        }
    }
    return {
        "webhook_set": await _evo_call("POST", f"/webhook/set/{inst}", body),
        "settings_set": await _evo_call("POST", f"/settings/set/{inst}", INSTANCE_SETTINGS_OFF),
        "webhook_now": await _evo_call("GET", f"/webhook/find/{inst}"),
        "settings_now": await _evo_call("GET", f"/settings/find/{inst}"),
    }


@app.post("/debug/evolution/send-test", tags=["WhatsApp & Webhook"])
async def debug_send_test(jid: str, text: str = "🤖 Teste de envio do Bot de Auditoria"):
    """Envia texto direto pela Evolution e devolve a resposta crua (para diagnosticar falhas de envio)."""
    inst = settings.EVOLUTION_INSTANCE_NAME
    return await _evo_call(
        "POST",
        f"/message/sendText/{inst}",
        {"number": jid, "text": text, "delay": 500, "linkPreview": False},
    )


# ==========================================================
# WEBHOOK EVOLUTION API (COM TRAVAS ANTI-FRAUDE)
# ==========================================================

@app.post("/webhook/evolution", tags=["WhatsApp & Webhook"])
async def evolution_webhook(
    request: Request,
    payload: Dict[str, Any] = Body(...),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Webhook da Evolution API para recepção de eventos de GRUPOS do WhatsApp com travas anti-fraude:
    1. Filtra eventos operacionais (connection.update, etc.) retornando 200 imediatamente.
    2. Ignora mensagens enviadas pelo próprio bot (fromMe=True) para evitar loops.
    3. Verifica se a mensagem provém de um GRUPO cadastrado no sistema (@g.us).
    4. Trava Anti-Fraude: Bloqueia fotos encaminhadas da galeria ou de outros chats (isForwarded=True).
    5. Validação de Objeto: O Gemini 2.0 Flash valida se a imagem retrata o ambiente comercial da conveniência.
       - Se INVALID_OBJECT: avisa no grupo e não contabiliza a auditoria como concluída no dia.
    6. Se foto válida: avalia ruptura, faceamento e organização, salva no SQLite e responde no grupo.
    7. Se falha crítica (score < 60 ou ruptura), notifica o Diretor no privado.
    """
    event_name = (payload.get("event") or "").strip().lower()

    # 1. Trata eventos operacionais de conexão/sistema sem disparar warnings
    if event_name and event_name != "messages.upsert":
        logger.info(f"[Webhook] Evento operacional '{event_name}' recebido e tratado com sucesso.")
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "received", "event": event_name},
        )

    # 2. Ignora mensagens enviadas pelo próprio bot (fromMe=True)
    data_block = payload.get("data", payload)
    if isinstance(data_block, dict):
        key_block = data_block.get("key", {})
        if key_block.get("fromMe", False):
            logger.info("[Webhook] Mensagem enviada pelo próprio bot (fromMe=True) ignorada.")
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"status": "ignored", "reason": "bot_own_message"},
            )

    logger.info(f"[Webhook] Mensagem recebida da Evolution API: {json.dumps(payload)[:200]}...")

    # 3. Identificar se é mensagem de grupo e qual o remoteJid
    remote_jid, is_group, participant = evolution_service.extract_remote_jid(payload)
    if not remote_jid:
        logger.warning("[Webhook] Não foi possível extrair o identificador (remoteJid) do payload de mensagem.")
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ignored", "reason": "remote_jid_not_found"},
        )

    # Regra: Aceita apenas mensagens vindas de grupos (@g.us)
    if not is_group:
        logger.info(f"[Webhook] Mensagem de {remote_jid} ignorada: não provém de um grupo (@g.us).")
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ignored", "reason": "not_a_group_message"},
        )

    # Localiza a loja através do JID do grupo
    store = await find_store_by_group_jid(session, remote_jid)
    if not store:
        logger.info(f"[Webhook] Grupo {remote_jid} ignorado: grupo não cadastrado em nenhuma loja.")
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ignored", "reason": "unregistered_group", "group_jid": remote_jid},
        )

    logger.info(
        f"[Webhook] Mensagem aceita do grupo '{store.name}' ({store.whatsapp_group_jid}) "
        f"- Participante: {participant or 'Colaborador da loja'}"
    )

    # 4. Trava Anti-Fraude: Bloqueio de fotos encaminhadas
    if evolution_service.is_forwarded_message(payload):
        logger.warning(
            f"[Anti-Fraude] Foto encaminhada detectada no grupo {store.name} ({store.whatsapp_group_jid}). "
            "Rejeitando processamento."
        )
        warning_msg = (
            "⚠️ *Foto Recusada:* Não são permitidas fotos encaminhadas da galeria. "
            "Por favor, tire a foto diretamente pela câmera da loja."
        )
        await evolution_service.send_text_message(store.whatsapp_group_jid, warning_msg)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "rejected", "reason": "forwarded_rejected"},
        )

    # 5. Obter bytes da imagem postada no grupo
    image_bytes, mime_type, image_ref = evolution_service.extract_image_bytes(payload)

    if not image_bytes and image_ref and image_ref.startswith(("http://", "https://")):
        image_bytes = await evolution_service.download_media(image_ref)

    if not image_bytes:
        # Se não há foto, verifica se é uma mensagem de texto (comando de ranking ou resposta rápida de operador)
        text_content = evolution_service.extract_text_message(payload)
        if text_content:
            clean_cmd = text_content.strip().lower()

            # Caso A: Comando de consulta de Ranking (!ranking ou ranking)
            if clean_cmd in ("!ranking", "ranking"):
                logger.info(f"[Webhook] Comando de ranking solicitado no grupo de {store.name}.")
                ranking_data = await get_ranking_data(session)
                card_text = ranking_data["whatsapp_card"]
                await evolution_service.send_text_message(store.whatsapp_group_jid, card_text)
                return JSONResponse(
                    status_code=status.HTTP_200_OK,
                    content={"status": "ranking_sent", "store": store.name},
                )

            # Caso B: Comando de Justificativa de Ticket (!justificar <ID> <MOTIVO>)
            if clean_cmd.startswith("!justificar") or clean_cmd.startswith("justificar "):
                parts = text_content.strip().split(maxsplit=2)
                if len(parts) >= 2:
                    ticket_id_target = parts[1]
                    motivo = parts[2] if len(parts) > 2 else "Falta de estoque / Operacional"
                    ticket = await buscar_ticket_por_id_ou_prefixo(ticket_id_target, store_id=store.id, session=session)
                    if ticket and ticket.status == "ABERTO":
                        autor_name = participant or "Operador da Loja"
                        await justificar_ticket(ticket.id, motivo=motivo, autor=autor_name, session=session)
                        short_id = ticket.id[:8] if len(ticket.id) >= 8 else ticket.id
                        msg_just = (
                            "📝 *JUSTIFICATIVA REGISTRADA COM SUCESSO!*\n"
                            f"🎫 *Ticket:* {short_id} | *Setor:* {ticket.setor}\n"
                            f"📌 *Motivo:* {motivo}\n"
                            "Operação liberada sem penalidade para a loja."
                        )
                        await evolution_service.send_text_message(store.whatsapp_group_jid, msg_just)
                        return JSONResponse(
                            status_code=status.HTTP_200_OK,
                            content={
                                "status": "ticket_justified",
                                "ticket_id": ticket.id,
                                "setor": ticket.setor,
                                "motivo": motivo,
                                "store": store.name,
                            },
                        )
                    else:
                        msg_not = (
                            "⚠️ *Ticket não encontrado ou já finalizado:*\n"
                            f"Não encontramos pendência aberta com ID '{ticket_id_target}'. "
                            "Envie *!pendencias* para consultar os tickets ativos da sua loja."
                        )
                        await evolution_service.send_text_message(store.whatsapp_group_jid, msg_not)
                        return JSONResponse(
                            status_code=status.HTTP_200_OK,
                            content={"status": "ticket_not_found", "ticket_id": ticket_id_target, "store": store.name},
                        )
                else:
                    msg_help = (
                        "ℹ️ *Formato do comando:* `!justificar [ID] [Motivo]`\n"
                        "Exemplo: `!justificar a1b2c3 Falta de estoque no CD central`"
                    )
                    await evolution_service.send_text_message(store.whatsapp_group_jid, msg_help)
                    return JSONResponse(
                        status_code=status.HTTP_200_OK,
                        content={"status": "invalid_command_format"},
                    )

            # Caso C: Consulta de Pendências em Aberto (!pendencias)
            if clean_cmd in ("!pendencias", "!pendências", "pendencias", "pendências"):
                tickets_abertos = await buscar_tickets_abertos_loja(store.id, session=session)
                if not tickets_abertos:
                    msg_p = "✅ *Nenhuma pendência em aberto para esta loja no momento! Parabéns pela conformidade.*"
                else:
                    now_pend = datetime.utcnow()
                    lines = ["⏳ *PENDÊNCIAS EM ABERTO:*"]
                    for idx, t in enumerate(tickets_abertos, 1):
                        short_id = t.id[:8] if len(t.id) >= 8 else t.id
                        minutos_restantes = max(0, int((t.expires_at - now_pend).total_seconds() // 60))
                        lines.append(f"{idx}. [{short_id}] {t.setor}: {t.descricao_falha} (Expira em {minutos_restantes} min)")
                    lines.append("\nEnvie a foto corrigida ou use `!justificar [ID] [motivo]`.")
                    msg_p = "\n".join(lines)
                await evolution_service.send_text_message(store.whatsapp_group_jid, msg_p)
                return JSONResponse(
                    status_code=status.HTTP_200_OK,
                    content={
                        "status": "pendencias_listed",
                        "store": store.name,
                        "total": len(tickets_abertos),
                        "tickets": [t.to_dict() for t in tickets_abertos],
                    },
                )

            # Caso D: Resposta rápida informando o nome do operador para a última auditoria da loja (últimos 15 min)
            operator_candidate = evolution_service.extract_operator_name_from_text(text_content)
            if operator_candidate:
                recent_audit = await get_recent_unassigned_audit(session, store.id, max_minutes=15)
                if recent_audit:
                    logger.info(
                        f"[Webhook] Associando operador '{operator_candidate}' à auditoria recente #{recent_audit.id} "
                        f"em {store.name}..."
                    )
                    await assign_operator_to_audit(session, recent_audit.id, operator_candidate)
                    confirm_msg = (
                        f"✅ Pontos computados para *{operator_candidate}* "
                        f"no ranking individual e somados para a *{store.name}*!"
                    )
                    await evolution_service.send_text_message(store.whatsapp_group_jid, confirm_msg)
                    return JSONResponse(
                        status_code=status.HTTP_200_OK,
                        content={
                            "status": "operator_assigned",
                            "store": store.name,
                            "audit_id": recent_audit.id,
                            "operator": operator_candidate,
                            "points": recent_audit.points,
                        },
                    )

        tipo_msg = (payload.get("data") or {}).get("messageType") if isinstance(payload.get("data"), dict) else None
        if tipo_msg == "imageMessage":
            reply = (
                "🤖 Recebi sua foto, mas não consegui ler a imagem. "
                "Tente enviar novamente, tirada na hora pela câmera."
            )
        else:
            reply = (
                "🤖 *Bot de Auditoria ativo!*\n"
                "📸 Envie uma *foto tirada na hora* do setor para análise.\n"
                "Comandos: *!ranking* · *!pendencias* · *!justificar [ID] [motivo]*"
            )
        logger.info(f"[Webhook] Postagem no grupo de {store.name} sem foto válida/comando. Respondendo ajuda.")
        sent = await evolution_service.send_text_message(store.whatsapp_group_jid, reply)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "received", "reason": "no_actionable_message", "message_type": tipo_msg, "reply_sent": bool(sent)},
        )

    # 6. Identificação de Operador via Legenda da Imagem (se houver)
    caption_text = evolution_service.extract_caption(payload)
    operator_name = evolution_service.extract_operator_name_from_text(caption_text)
    if operator_name:
        logger.info(f"[Webhook] Operador identificado via legenda da foto: '{operator_name}'.")

    # Determina o turno dinamicamente via resolve_shift
    now = datetime.now()
    shift = resolve_shift(now)
    today = date.today()

    # 6.5. Ciclo de Correção Antes/Depois: Interceptação de Tickets de Pendência Abertos
    ticket_aberto = await buscar_ticket_aberto(store_id=store.id, session=session)
    if ticket_aberto:
        logger.info(
            f"[Webhook Ticket] Encontrado ticket de pendência #{ticket_aberto.id} aberto para {store.name} "
            f"(Setor: {ticket_aberto.setor} | Falha: '{ticket_aberto.descricao_falha}'). "
            "Iniciando comparação visual Antes vs Depois..."
        )

        foto_antes_bytes = None
        if ticket_aberto.foto_origem_path and os.path.exists(ticket_aberto.foto_origem_path):
            try:
                with open(ticket_aberto.foto_origem_path, "rb") as f:
                    foto_antes_bytes = f.read()
            except Exception as e:
                logger.warning(f"[Webhook Ticket] Não foi possível ler foto anterior em {ticket_aberto.foto_origem_path}: {e}")

        if not foto_antes_bytes:
            foto_antes_bytes = b"foto_origem_antes_bytes"

        comparacao = await gemini_service.comparar_correcao(
            img_antes_bytes=foto_antes_bytes,
            img_depois_bytes=image_bytes,
            criterio_pendente=ticket_aberto.criterio,
            descricao_falha=ticket_aberto.descricao_falha,
        )

        if comparacao.mesmo_setor and comparacao.problema_resolvido:
            op_bonus = operator_name or ticket_aberto.operator_id or "Equipe"
            ticket_db, bonus_pts, aviso_teto = await aplicar_correcao_ticket(
                session=session,
                ticket_id=ticket_aberto.id,
                store_id=store.id,
                operator_name=op_bonus,
                shift=shift,
            )

            logger.info(
                f"[Webhook Ticket] Correção VALIDADA para o ticket #{ticket_aberto.id}! "
                f"Bônus: +{bonus_pts} pts para {op_bonus} (Aviso: {aviso_teto or 'Nenhum'})."
            )

            if bonus_pts > 0:
                msg_correcao = (
                    "✅ *CORREÇÃO VALIDADA COM SUCESSO!*\n"
                    f"🏪 *Setor:* {ticket_aberto.setor}\n"
                    f"🎯 *Problema resolvido:* {ticket_aberto.descricao_falha}\n"
                    f"🏆 *Bônus:* +{bonus_pts} pontos creditados para *{op_bonus}*!"
                )
            else:
                msg_correcao = (
                    "✅ *CORREÇÃO VALIDADA COM SUCESSO!*\n"
                    f"🏪 *Setor:* {ticket_aberto.setor}\n"
                    f"🎯 *Problema resolvido:* {ticket_aberto.descricao_falha}\n"
                    f"⚠️ *Aviso:* {aviso_teto or 'Limite de bônus de correção atingido para este turno'}."
                )
            await evolution_service.send_text_message(store.whatsapp_group_jid, msg_correcao)

            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "status": "ticket_corrected",
                    "store": store.name,
                    "ticket_id": ticket_aberto.id,
                    "bonus": bonus_pts,
                    "operator": op_bonus,
                    "aviso": aviso_teto,
                    "detalhes": comparacao.detalhes_observados,
                },
            )
        else:
            logger.info(
                f"[Webhook Ticket] Foto não validou o ticket #{ticket_aberto.id} "
                f"(mesmo_setor={comparacao.mesmo_setor}, resolvido={comparacao.problema_resolvido}). "
                "Seguindo com o fluxo padrão de auditoria independente."
            )

    logger.info(
        f"[Webhook] Foto recebida do grupo de {store.name} ({len(image_bytes)} bytes) | Turno: {shift}. "
        f"Acionando Gemini 2.0 Flash..."
    )

    # 7. Análise visual multimodal com Gemini 2.0 Flash (incluindo validação de ambiente)
    analysis = await gemini_service.analyze_store_image(image_bytes, mime_type=mime_type)
    analysis_status = analysis.get("status", "OK")
    score = analysis.get("score", 0.0)
    aprovado = analysis.get("aprovado", False)
    problemas = analysis.get("problemas", [])
    orientacao = analysis.get("orientacao", "")
    motivo = analysis.get("motivo", "")

    # Log detalhado do serviço avaliador determinístico (Perito Visual -> Juiz)
    avaliacao = analysis.get("avaliacao")
    if avaliacao:
        logger.info(
            f"[Evaluator] Avaliação determinística: Setor={avaliacao.get('setor')} | "
            f"Nota={avaliacao.get('nota_calculada')} | "
            f"Conformes={avaliacao.get('criterios_conformes')}/{avaliacao.get('total_criterios_avaliados')} | "
            f"Ação Imediata={avaliacao.get('requer_acao_imediata')} | "
            f"Itens Corrigíveis={len(avaliacao.get('itens_corrigiveis', []))}"
        )

    # Validação Estrita de Objeto e Ambiente
    if analysis_status == "INVALID_OBJECT":
        logger.warning(
            f"[Webhook] Foto rejeitada em {store.name}: imagem fora do padrão da conveniência. "
            f"Motivo: {motivo}"
        )
        await save_audit(
            session=session,
            store_id=store.id,
            audit_date=today,
            shift=shift,
            status="INVALID_OBJECT",
            score=0.0,
            ai_analysis=json.dumps(analysis, ensure_ascii=False),
            image_url=image_ref or "whatsapp_group_media",
            operator_name=operator_name,
            points=0.0,
        )
        invalid_reply = (
            "❌ *Foto Inválida:* O sistema identificou que a imagem não pertence a uma área da conveniência "
            "(geladeiras, gôndolas, estufa, banheiro ou caixa). Por favor, envie uma foto válida do setor auditado."
        )
        await evolution_service.send_text_message(store.whatsapp_group_jid, invalid_reply)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "invalid_object",
                "store": store.name,
                "motivo": motivo,
                "reason": "image_not_store_environment",
            },
        )

    # 8. Cálculo de pontos da rodada e gamificação
    audit_points = calculate_audit_points(score, now)

    # Se operador foi identificado na legenda, credita pontos imediatamente
    if operator_name and audit_points > 0:
        await add_points_to_operator(session, store.id, operator_name, audit_points)

    # 9. Salva auditoria válida (OK ou ALERT)
    audit_status = "OK" if aprovado else "ALERT"
    audit_record = await save_audit(
        session=session,
        store_id=store.id,
        audit_date=today,
        shift=shift,
        status=audit_status,
        score=score,
        ai_analysis=json.dumps(analysis, ensure_ascii=False),
        image_url=image_ref or "whatsapp_group_media",
        operator_name=operator_name,
        points=audit_points,
    )
    logger.info(
        f"[Webhook] Auditoria salva: ID={audit_record.id} | Loja={store.name} | "
        f"Turno={shift} | Status={audit_status} | Score={score} | Pontos={audit_points} | Operador={operator_name}"
    )

    # 9.5. Se houver itens corrigíveis, cria tickets de pendência com TTL de 20 minutos
    itens_corrigiveis = analysis.get("itens_corrigiveis", [])
    if itens_corrigiveis:
        try:
            os.makedirs("media_tickets", exist_ok=True)
            foto_origem_path = f"media_tickets/audit_{audit_record.id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.jpg"
            with open(foto_origem_path, "wb") as f:
                f.write(image_bytes)

            await criar_tickets_pendencia(
                audit_id=audit_record.id,
                store_id=store.id,
                operator_id=operator_name,
                setor=analysis.get("setor", "GERAL"),
                itens_corrigiveis=itens_corrigiveis,
                foto_path=foto_origem_path,
                session=session,
            )
            logger.info(f"[Webhook Ticket] Ticket(s) de pendência criado(s) para {store.name} com expiração em 20 min.")
        except Exception as e:
            logger.error(f"[Webhook Ticket] Erro ao persistir tickets de pendência: {e}")

    # 10. Responde o feedback diretamente dentro do grupo da loja com tom educativo e gamificação
    msg_whatsapp = analysis.get("mensagem_whatsapp", "")
    if not msg_whatsapp:
        msg_whatsapp = (
            "Audit aprovado! Loja 100% pronta pro pico."
            if aprovado
            else f"Atenção: identificado {', '.join(problemas)}. Favor puxar a frente e repor agora."
        )

    # Complementa a mensagem com o crédito de pontos ou a chamada rápida de identificação
    if operator_name:
        operator_line = f"\n\n👤 *Operador(a):* {operator_name} (+{int(audit_points)} pts no ranking!)"
    else:
        operator_line = (
            "\n\n👤 *Quem realizou esta auditoria?* "
            "Responda a esta mensagem apenas com seu primeiro nome para pontuar no ranking!"
        )

    # Alerta de bônus por correção em até 20 minutos
    if itens_corrigiveis:
        correction_alert = (
            "\n\n⏱️ *Ciclo de Correção Ativo (+25 pts):*\n"
            "Corrija os apontamentos acima e envie nova foto deste setor nos próximos *20 minutos* para ganhar *+25 pontos de bônus* no ranking! 🚀"
        )
    else:
        correction_alert = ""

    group_reply = msg_whatsapp + operator_line + correction_alert
    await evolution_service.send_text_message(store.whatsapp_group_jid, group_reply)

    # 11. Alerta Executivo (Diretoria):
    has_rupture = any("ruptura" in p.lower() or "vazio" in p.lower() for p in problemas)
    is_critical_failure = (score < 6.0) or (not aprovado and has_rupture)

    if is_critical_failure:
        setor_nome = analysis.get("setor", "SETOR OPERACIONAL")
        logger.warning(
            f"[Webhook] Falha grave detectada no grupo de {store.name} (Setor: {setor_nome} | Nota: {score}/10). "
            f"Notificando Diretoria..."
        )
        director_msg = (
            f"🚨 Alerta Operacional Diretoria - {store.name} ({setor_nome}):\n"
            f"Turno: {shift}\n"
            f"Nota IA: {score}/10 (REPROVADO)\n"
            f"Problemas: {', '.join(problemas) if problemas else 'Ruptura ou desorganização grave'}\n"
            f"Operador: {operator_name or 'Não informado'}\n"
            f"Grupo: {store.whatsapp_group_jid}\n"
            f"Gerente Responsável: {store.manager_name} ({store.manager_phone or 'N/A'})\n"
            f"Status: Equipe orientada no grupo para correção imediata."
        )

        await evolution_service.send_text_message(settings.DIRECTOR_PHONE, director_msg)

        await save_alert(
            session=session,
            store_id=store.id,
            message=director_msg,
            sent_to_director=True,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "success",
            "store": store.name,
            "group_jid": store.whatsapp_group_jid,
            "audit_id": audit_record.id,
            "shift": shift,
            "score": score,
            "points": audit_points,
            "operator": operator_name,
            "approved": aprovado,
            "problemas": problemas,
            "orientacao": orientacao,
            "group_feedback_sent": True,
            "director_alerted": is_critical_failure,
        },
    )


# ==========================================================
# ROTA PWA: CAPTURA AO VIVO COM TRAVA DE SENSOR DE CÂMERA
# ==========================================================

@app.get("/capturar/{store_id}", response_class=HTMLResponse, tags=["PWA Captura ao Vivo"])
async def pwa_capture_page(store_id: int, session: AsyncSession = Depends(get_db_session)):
    """
    Renderiza página web mobile (PWA leve) com trava de sensor de câmera (capture="environment").
    Garante que a foto seja tirada ao vivo pela câmera traseira do celular sem seleção de arquivos da galeria.
    """
    store = await get_store_by_id(session, store_id)
    if not store:
        raise HTTPException(status_code=404, detail=f"Loja com ID {store_id} não encontrada.")

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Auditoria ao Vivo - {store.name}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: #090d16;
      color: #f1f5f9;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 20px 16px;
    }}
    .container {{
      width: 100%;
      max-width: 460px;
      display: flex;
      flex-direction: column;
      gap: 18px;
    }}
    .header {{
      background: #111827;
      border: 1px solid #1f2937;
      border-radius: 16px;
      padding: 20px;
      text-align: center;
    }}
    .badge {{
      display: inline-block;
      padding: 4px 12px;
      border-radius: 9999px;
      background: rgba(14, 165, 233, 0.15);
      color: #38bdf8;
      font-size: 12px;
      font-weight: 600;
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    h1 {{ font-size: 20px; color: #ffffff; margin-bottom: 4px; }}
    p.sub {{ font-size: 13px; color: #94a3b8; line-height: 1.4; }}
    
    .card-camera {{
      background: #111827;
      border: 2px dashed #374151;
      border-radius: 16px;
      padding: 24px 16px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 220px;
      position: relative;
      overflow: hidden;
    }}
    #previewImg {{
      width: 100%;
      max-height: 280px;
      object-fit: cover;
      border-radius: 12px;
      display: none;
      margin-bottom: 12px;
    }}
    .btn-capture {{
      width: 100%;
      background: linear-gradient(135deg, #2563eb, #1d4ed8);
      color: #ffffff;
      border: none;
      border-radius: 14px;
      padding: 18px 24px;
      font-size: 17px;
      font-weight: 700;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      box-shadow: 0 8px 24px rgba(37, 99, 235, 0.4);
      transition: transform 0.1s, opacity 0.2s;
    }}
    .btn-capture:active {{
      transform: scale(0.98);
      opacity: 0.9;
    }}
    .btn-capture:disabled {{
      background: #475569;
      box-shadow: none;
      cursor: not-allowed;
    }}
    
    .status-box {{
      display: none;
      background: #1e293b;
      border-radius: 14px;
      padding: 16px;
      border: 1px solid #334155;
    }}
    .spinner {{
      display: inline-block;
      width: 22px;
      height: 22px;
      border: 3px solid rgba(255,255,255,0.2);
      border-radius: 50%;
      border-top-color: #38bdf8;
      animation: spin 1s ease-in-out infinite;
    }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}

    .result-card {{
      display: none;
      background: #111827;
      border-radius: 16px;
      padding: 20px;
      border: 1px solid #1f2937;
    }}
    .result-card.success {{ border-color: #10b981; }}
    .result-card.alert {{ border-color: #f59e0b; }}
    .result-card.invalid {{ border-color: #ef4444; }}

    .score-badge {{
      font-size: 32px;
      font-weight: 800;
      margin-bottom: 4px;
    }}
    .score-badge.ok {{ color: #10b981; }}
    .score-badge.alert {{ color: #f59e0b; }}
    .score-badge.invalid {{ color: #ef4444; }}

    .res-title {{ font-size: 18px; font-weight: 700; margin-bottom: 8px; }}
    .res-text {{ font-size: 14px; color: #cbd5e1; margin-bottom: 12px; line-height: 1.5; }}
    .problems-list {{ font-size: 13px; color: #f87171; padding-left: 18px; margin-bottom: 12px; }}
    .orientacao-box {{
      background: #0f172a;
      padding: 12px 14px;
      border-radius: 8px;
      font-size: 13px;
      color: #93c5fd;
      border-left: 3px solid #3b82f6;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="badge">Trava Anti-Fraude Ativa</div>
      <h1>{store.name}</h1>
      <p class="sub">Auditoria Operacional ao Vivo. A foto deve ser tirada diretamente pelo sensor da câmera da loja.</p>
    </div>

    <!-- Campo de identificação do operador para ranking -->
    <div style="background: #111827; border: 1px solid #1f2937; border-radius: 14px; padding: 14px 16px;">
      <label for="operatorInput" style="display: block; font-size: 13px; font-weight: 600; color: #94a3b8; margin-bottom: 6px;">
        👤 Seu Nome / Operador (opcional para ranking):
      </label>
      <input type="text" id="operatorInput" placeholder="Ex: Carlos, Ana..." style="width: 100%; box-sizing: border-box; padding: 12px 14px; background: #0f172a; border: 1px solid #334155; border-radius: 10px; color: #f1f5f9; font-size: 14px; outline: none;">
    </div>

    <div class="card-camera">
      <img id="previewImg" alt="Preview da Câmera">
      
      <!-- Input com trava no sensor de ambiente (câmera traseira) -->
      <input type="file" accept="image/*" capture="environment" id="cameraInput" style="display: none;">

      <button class="btn-capture" id="btnTirarFoto" onclick="document.getElementById('cameraInput').click()">
        📸 Tirar Foto da Loja Agora
      </button>
    </div>

    <div class="status-box" id="statusBox">
      <div style="display: flex; align-items: center; gap: 12px;">
        <div class="spinner"></div>
        <div>
          <div style="font-weight: 600; font-size: 14px;">Analisando foto com Gemini 2.0 Flash...</div>
          <div style="font-size: 12px; color: #94a3b8;">Verificando ambiente comercial, ruptura e faceamento.</div>
        </div>
      </div>
    </div>

    <div class="result-card" id="resultCard">
      <div class="score-badge" id="scoreVal">--</div>
      <div class="res-title" id="resTitle">--</div>
      <div class="res-text" id="resDesc">--</div>
      <ul class="problems-list" id="problemsList"></ul>
      <div class="orientacao-box" id="orientacaoBox">--</div>
    </div>
  </div>

  <script>
    const cameraInput = document.getElementById('cameraInput');
    const previewImg = document.getElementById('previewImg');
    const btnTirarFoto = document.getElementById('btnTirarFoto');
    const statusBox = document.getElementById('statusBox');
    const resultCard = document.getElementById('resultCard');

    cameraInput.addEventListener('change', async (e) => {{
      const file = e.target.files[0];
      if (!file) return;

      // Exibe preview
      const reader = new FileReader();
      reader.onload = (event) => {{
        previewImg.src = event.target.result;
        previewImg.style.display = 'block';
      }};
      reader.readAsDataURL(file);

      // Inicia envio e análise
      btnTirarFoto.disabled = true;
      statusBox.style.display = 'block';
      resultCard.style.display = 'none';

      const formData = new FormData();
      formData.append('file', file);
      const opInput = document.getElementById('operatorInput');
      if (opInput && opInput.value.trim()) {{
        formData.append('operator_name', opInput.value.trim());
      }}

      try {{
        const response = await fetch('/api/upload-auditoria/{store.id}', {{
          method: 'POST',
          body: formData
        }});

        const data = await response.json();
        exibirResultado(data);
      }} catch (err) {{
        alert('Erro ao enviar foto para auditoria: ' + err.message);
      }} finally {{
        btnTirarFoto.disabled = false;
        btnTirarFoto.innerText = '📸 Tirar Outra Foto';
        statusBox.style.display = 'none';
      }}
    }});

    function exibirResultado(data) {{
      resultCard.style.display = 'block';
      resultCard.className = 'result-card';
      const problemsList = document.getElementById('problemsList');
      problemsList.innerHTML = '';

      if (data.status === 'INVALID_OBJECT') {{
        resultCard.classList.add('invalid');
        document.getElementById('scoreVal').className = 'score-badge invalid';
        document.getElementById('scoreVal').innerText = '0/10';
        document.getElementById('resTitle').innerText = '❌ Imagem Inválida';
        document.getElementById('resDesc').innerText = data.motivo || 'A foto não pertence ao ambiente comercial da conveniência.';
        document.getElementById('orientacaoBox').innerText = data.orientacao || 'Por favor, tire a foto focando nas prateleiras ou refrigeradores.';
      }} else if (data.status === 'OK' && data.approved) {{
        resultCard.classList.add('success');
        document.getElementById('scoreVal').className = 'score-badge ok';
        document.getElementById('scoreVal').innerText = (data.score <= 10 ? data.score + '/10' : data.score + '/100');
        document.getElementById('resTitle').innerText = '✅ Auditoria Aprovada!';
        document.getElementById('resDesc').innerText = 'Loja 100% pronta para o pico de vendas.';
        document.getElementById('orientacaoBox').innerText = data.orientacao || 'Excelente padrão operacional.';
      }} else {{
        resultCard.classList.add('alert');
        document.getElementById('scoreVal').className = 'score-badge alert';
        document.getElementById('scoreVal').innerText = (data.score <= 10 ? data.score + '/10' : data.score + '/100');
        document.getElementById('resTitle').innerText = '💡 Oportunidades Identificadas';
        document.getElementById('resDesc').innerText = 'Foram identificados pequenos pontos para ajuste.';
        
        if (data.problemas && data.problemas.length) {{
          data.problemas.forEach(p => {{
            const li = document.createElement('li');
            li.innerText = p;
            problemsList.appendChild(li);
          }});
        }}
        document.getElementById('orientacaoBox').innerText = data.orientacao || 'Favor puxar a frente e repor os produtos.';
      }}
    }}
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html)


@app.post("/api/upload-auditoria/{store_id}", tags=["PWA Captura ao Vivo"])
async def upload_auditoria_live(
    store_id: int,
    file: UploadFile = File(...),
    operator_name: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Recebe a foto tirada ao vivo através do PWA (com trava de sensor de câmera),
    submete à inspeção visual do Gemini 2.0 Flash, credita pontos ao operador e sincroniza com o grupo da loja.
    """
    store = await get_store_by_id(session, store_id)
    if not store:
        raise HTTPException(status_code=404, detail=f"Loja {store_id} não encontrada.")

    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Arquivo de imagem vazio.")

    # Normaliza nome do operador se fornecido
    cleaned_operator = evolution_service.extract_operator_name_from_text(operator_name)

    logger.info(
        f"[PWA Live] Foto ao vivo recebida de {store.name} ({len(image_bytes)} bytes) "
        f"| Operador: {cleaned_operator or 'Não informado'}."
    )

    # Ciclo de Correção Antes/Depois via PWA
    ticket_aberto = await buscar_ticket_aberto(store_id=store.id, session=session)
    if ticket_aberto:
        foto_antes_bytes = None
        if ticket_aberto.foto_origem_path and os.path.exists(ticket_aberto.foto_origem_path):
            try:
                with open(ticket_aberto.foto_origem_path, "rb") as f:
                    foto_antes_bytes = f.read()
            except Exception:
                pass
        if not foto_antes_bytes:
            foto_antes_bytes = b"foto_origem_antes_bytes"

        comparacao = await gemini_service.comparar_correcao(
            img_antes_bytes=foto_antes_bytes,
            img_depois_bytes=image_bytes,
            criterio_pendente=ticket_aberto.criterio,
            descricao_falha=ticket_aberto.descricao_falha,
        )

        if comparacao.mesmo_setor and comparacao.problema_resolvido:
            op_bonus = cleaned_operator or ticket_aberto.operator_id or "Equipe"
            ticket_db, bonus_pts, aviso_teto = await aplicar_correcao_ticket(
                session=session,
                ticket_id=ticket_aberto.id,
                store_id=store.id,
                operator_name=op_bonus,
                shift=resolve_shift(),
            )

            if bonus_pts > 0:
                msg_correcao = (
                    "✅ *CORREÇÃO VALIDADA COM SUCESSO!*\n"
                    f"🏪 *Setor:* {ticket_aberto.setor}\n"
                    f"🎯 *Problema resolvido:* {ticket_aberto.descricao_falha}\n"
                    f"🏆 *Bônus:* +{bonus_pts} pontos creditados para *{op_bonus}*!"
                )
            else:
                msg_correcao = (
                    "✅ *CORREÇÃO VALIDADA COM SUCESSO!*\n"
                    f"🏪 *Setor:* {ticket_aberto.setor}\n"
                    f"🎯 *Problema resolvido:* {ticket_aberto.descricao_falha}\n"
                    f"⚠️ *Aviso:* {aviso_teto or 'Limite de bônus de correção atingido para este turno'}."
                )
            await evolution_service.send_text_message(store.whatsapp_group_jid, msg_correcao)
            return {
                "status": "CORRIGIDO",
                "approved": True,
                "score": 10.0,
                "points": float(bonus_pts),
                "operator": op_bonus,
                "shift": resolve_shift(),
                "ticket_id": ticket_aberto.id,
                "bonus": bonus_pts,
                "aviso": aviso_teto,
                "message": msg_correcao,
                "detalhes": comparacao.detalhes_observados,
            }

    # Análise pelo Gemini 2.0 Flash
    mime_type = file.content_type or "image/jpeg"
    analysis = await gemini_service.analyze_store_image(image_bytes, mime_type=mime_type)

    analysis_status = analysis.get("status", "OK")
    score = analysis.get("score", 0.0)
    aprovado = analysis.get("aprovado", False)
    problemas = analysis.get("problemas", [])
    orientacao = analysis.get("orientacao", "")
    motivo = analysis.get("motivo", "")

    # Log detalhado do serviço avaliador determinístico
    avaliacao = analysis.get("avaliacao")
    if avaliacao:
        logger.info(
            f"[PWA Evaluator] Avaliação determinística: Setor={avaliacao.get('setor')} | "
            f"Nota={avaliacao.get('nota_calculada')} | "
            f"Conformes={avaliacao.get('criterios_conformes')}/{avaliacao.get('total_criterios_avaliados')} | "
            f"Ação Imediata={avaliacao.get('requer_acao_imediata')} | "
            f"Itens Corrigíveis={len(avaliacao.get('itens_corrigiveis', []))}"
        )

    # Define o turno dinamicamente via resolve_shift
    now = datetime.now()
    current_shift = resolve_shift(now)
    today = date.today()

    # Validação estrita de objeto
    if analysis_status == "INVALID_OBJECT":
        logger.warning(f"[PWA Live] Foto rejeitada em {store.name}: {motivo}")
        await save_audit(
            session=session,
            store_id=store.id,
            audit_date=today,
            shift=current_shift,
            status="INVALID_OBJECT",
            score=0.0,
            ai_analysis=json.dumps(analysis, ensure_ascii=False),
            image_url="pwa_live_camera",
            operator_name=cleaned_operator,
            points=0.0,
        )
        return {
            "status": "INVALID_OBJECT",
            "approved": False,
            "score": 0.0,
            "points": 0.0,
            "motivo": motivo,
            "problemas": problemas,
            "orientacao": orientacao,
            "avaliacao": avaliacao,
            "resumo_operacional": analysis.get("resumo_operacional"),
            "itens_corrigiveis": analysis.get("itens_corrigiveis", []),
            "requer_acao_imediata": analysis.get("requer_acao_imediata", True),
        }

    # Cálculo de pontos e gamificação
    audit_points = calculate_audit_points(score, now)
    if cleaned_operator and audit_points > 0:
        await add_points_to_operator(session, store.id, cleaned_operator, audit_points)

    # Salva auditoria válida
    audit_status = "OK" if aprovado else "ALERT"
    audit_record = await save_audit(
        session=session,
        store_id=store.id,
        audit_date=today,
        shift=current_shift,
        status=audit_status,
        score=score,
        ai_analysis=json.dumps(analysis, ensure_ascii=False),
        image_url="pwa_live_camera",
        operator_name=cleaned_operator,
        points=audit_points,
    )

    # Criação de tickets de pendência no PWA se houver itens corrigíveis
    itens_corrigiveis = analysis.get("itens_corrigiveis", [])
    if itens_corrigiveis:
        try:
            os.makedirs("media_tickets", exist_ok=True)
            foto_origem_path = f"media_tickets/pwa_audit_{audit_record.id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.jpg"
            with open(foto_origem_path, "wb") as f:
                f.write(image_bytes)

            await criar_tickets_pendencia(
                audit_id=audit_record.id,
                store_id=store.id,
                operator_id=cleaned_operator,
                setor=analysis.get("setor", "GERAL"),
                itens_corrigiveis=itens_corrigiveis,
                foto_path=foto_origem_path,
                session=session,
            )
            logger.info(f"[PWA Tickets] Ticket(s) de pendência criado(s) para {store.name} com expiração em 20 min.")
        except Exception as e:
            logger.error(f"[PWA Tickets] Erro ao persistir tickets de pendência: {e}")

    # Notifica o grupo da loja via Evolution API com tom educativo e pontos
    msg_whatsapp = analysis.get("mensagem_whatsapp")
    if msg_whatsapp:
        whatsapp_notification = msg_whatsapp
    else:
        status_emoji = "✅" if aprovado else "⚠️"
        status_label = "Aprovado" if aprovado else "Reposição Necessária"
        whatsapp_notification = (
            f"📸 *Auditoria via Câmera ao Vivo - {store.name}*\n"
            f"Setor: {analysis.get('setor', 'LOJA')}\n"
            f"Turno: {current_shift}\n"
            f"Resultado: {status_emoji} {status_label} (Nota: {score}/10)\n"
        )
        if aprovado:
            whatsapp_notification += "Audit aprovado! Loja 100% pronta pro pico."
        else:
            whatsapp_notification += f"Atenção: {', '.join(problemas)}. {orientacao}"

    if cleaned_operator:
        whatsapp_notification += f"\n\n👤 *Operador(a):* {cleaned_operator} (+{int(audit_points)} pts no ranking!)"
    else:
        whatsapp_notification += (
            "\n\n👤 *Quem realizou esta auditoria?* "
            "Responda a esta mensagem apenas com seu primeiro nome para pontuar no ranking!"
        )

    await evolution_service.send_text_message(store.whatsapp_group_jid, whatsapp_notification)

    # Notifica o Diretor se nota < 6.0 ou não-conformidade com ruptura
    has_rupture = any("ruptura" in p.lower() or "vazio" in p.lower() for p in problemas)
    is_critical_failure = (score < 6.0) or (not aprovado and has_rupture)
    if is_critical_failure:
        director_msg = (
            f"🚨 Alerta Operacional Diretoria - {store.name} (Via Câmera ao Vivo):\n"
            f"Setor: {analysis.get('setor', 'LOJA')}\n"
            f"Turno: {current_shift}\n"
            f"Nota IA: {score}/10 (REPROVADO)\n"
            f"Problemas: {', '.join(problemas) if problemas else 'Ruptura ou desorganização'}\n"
            f"Operador: {cleaned_operator or 'Não informado'}\n"
            f"Grupo: {store.whatsapp_group_jid}\n"
            f"Status: Equipe orientada para correção imediata."
        )
        await evolution_service.send_text_message(settings.DIRECTOR_PHONE, director_msg)
        await save_alert(session=session, store_id=store.id, message=director_msg, sent_to_director=True)

    return {
        "status": audit_status,
        "approved": aprovado,
        "score": score,
        "points": audit_points,
        "operator": cleaned_operator,
        "shift": current_shift,
        "problemas": problemas,
        "orientacao": orientacao,
        "audit_id": audit_record.id,
        "avaliacao": avaliacao,
        "resumo_operacional": analysis.get("resumo_operacional"),
        "itens_corrigiveis": analysis.get("itens_corrigiveis", []),
        "requer_acao_imediata": analysis.get("requer_acao_imediata", False),
    }


# ==========================================================
# ROTAS DE GESTÃO DE LOJAS E CONSULTAS
# ==========================================================

@app.get("/api/stores", tags=["Lojas"])
async def list_stores(session: AsyncSession = Depends(get_db_session)):
    """Retorna todas as lojas cadastradas com seus respectivos grupos de WhatsApp."""
    stores = await get_all_stores(session)
    return [
        {
            "id": s.id,
            "name": s.name,
            "whatsapp_group_jid": s.whatsapp_group_jid,
            "manager_name": s.manager_name,
            "manager_phone": s.manager_phone,
            "capture_url": f"/capturar/{s.id}",
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in stores
    ]


@app.put("/api/stores/{store_id}/group", tags=["Lojas"])
async def update_store_group(
    store_id: int,
    payload: UpdateGroupPayload,
    session: AsyncSession = Depends(get_db_session),
):
    """Atualiza o JID do grupo de WhatsApp de uma loja específica."""
    store = await update_store_group_jid(session, store_id, payload.whatsapp_group_jid)
    if not store:
        raise HTTPException(status_code=404, detail=f"Loja com ID {store_id} não encontrada.")

    return {
        "status": "updated",
        "store_id": store.id,
        "name": store.name,
        "whatsapp_group_jid": store.whatsapp_group_jid,
    }


@app.get("/api/audits", tags=["Auditorias"])
async def list_audits(
    target_date: Optional[date] = None,
    shift: Optional[str] = None,
    session: AsyncSession = Depends(get_db_session),
):
    """Lista as auditorias registradas com filtros opcionais por data e turno."""
    stmt = select(Audit).order_by(Audit.created_at.desc())
    if target_date:
        stmt = stmt.where(Audit.date == target_date)
    if shift:
        stmt = stmt.where(Audit.shift == shift.upper())

    result = await session.execute(stmt)
    audits = result.scalars().all()

    return [
        {
            "id": a.id,
            "store_id": a.store_id,
            "date": a.date.isoformat(),
            "shift": a.shift,
            "status": a.status,
            "score": a.score,
            "operator_name": a.operator_name,
            "points": a.points,
            "ai_analysis": a.parsed_analysis,
            "image_url": a.image_url,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in audits
    ]


@app.get("/api/ranking", tags=["Gamificação & Ranking"])
async def get_system_ranking(session: AsyncSession = Depends(get_db_session)):
    """
    Retorna o placar geral de auditoria e gamificação:
    - Ranking por Loja (Soma de pontos e média de score)
    - Ranking por Turno (T1 Manhã vs T2 Tarde vs T3 Noite)
    - Ranking dos Top 5 Operadores individuais
    - Card formatado para envio no WhatsApp
    """
    return await get_ranking_data(session)


@app.get("/api/alerts", tags=["Alertas"])
async def list_alerts(session: AsyncSession = Depends(get_db_session)):
    """Lista os alertas operacionais gerados pelo sistema."""
    stmt = select(Alert).order_by(Alert.created_at.desc())
    result = await session.execute(stmt)
    alerts = result.scalars().all()

    return [
        {
            "id": al.id,
            "store_id": al.store_id,
            "message": al.message,
            "sent_to_director": al.sent_to_director,
            "created_at": al.created_at.isoformat() if al.created_at else None,
        }
        for al in alerts
    ]


# ==========================================================
# DISPARADORES MANUAIS PARA TESTES DOS JOBS AGENDADOS
# ==========================================================

@app.post("/api/scheduler/trigger/{job_name}", tags=["Agendador (Testes)"])
async def trigger_scheduler_job(job_name: str):
    """
    Dispara manualmente os jobs de agendamento:
    - tolerance_1645: Lembrete nos grupos das lojas sem auditoria válida hoje
    - critical_1700: Alerta exclusivo à diretoria para lojas inadimplentes
    - summary_1715: Envio do resumo telegráfico consolidado para a diretoria
    """
    job_key = job_name.lower().strip()
    if job_key in ("tolerance_1645", "1645"):
        result = await check_afternoon_audits_tolerance_1645()
        return {"job": "tolerance_1645", "executed": True, "result": result}
    elif job_key in ("critical_1700", "1700"):
        result = await check_critical_unresponsive_stores_1700()
        return {"job": "critical_1700", "executed": True, "result": result}
    elif job_key in ("summary_1715", "1715"):
        result = await send_executive_summary_1715()
        return {"job": "summary_1715", "executed": True, "summary": result}
    else:
        raise HTTPException(
            status_code=400,
            detail="Job inválido. Use 'tolerance_1645', 'critical_1700' ou 'summary_1715'.",
        )
