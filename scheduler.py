"""
Módulo de agendamento de tarefas em segundo plano com APScheduler.
Implementa rotina flexível (janela diária das 06:00 às 22:00):
- 16h45: Lembrete amigável DENTRO DO GRUPO caso nenhuma auditoria válida tenha sido enviada no dia.
- 17h00: Alerta exclusivo no privado do Diretor se ainda não houver auditoria válida.
- 17h15: Resumo executivo telegráfico consolidado no privado do Diretor.
"""

from datetime import date, datetime
import logging
from typing import Dict, List

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import settings
from database import (
    async_session_factory,
    get_all_stores,
    has_valid_daily_audit,
    save_alert,
)
from services.evolution_service import evolution_service

logger = logging.getLogger("audit.scheduler")

# Instância global do agendador assíncrono
scheduler = AsyncIOScheduler()


async def check_afternoon_audits_tolerance_1645() -> Dict[str, List[str]]:
    """
    Rotina executada às 16h45.
    Verifica se cada uma das lojas enviou ao menos UMA auditoria válida (OK ou ALERT) hoje.
    Caso a loja ainda não tenha enviado nenhuma auditoria válida no dia, dispara
    lembrete amigável DENTRO DO GRUPO daquela loja.
    """
    today = date.today()
    logger.info(f"[Scheduler 16:45] Verificando cumprimento de auditorias validas para {today}...")

    notified_groups = []
    already_sent = []

    async with async_session_factory() as session:
        stores = await get_all_stores(session)

        for store in stores:
            audit = await has_valid_daily_audit(session, store.id, today)

            if audit:
                already_sent.append(store.name)
                logger.info(
                    f"[Scheduler 16:45] {store.name}: Auditoria valida ja registrada "
                    f"(Status: {audit.status} | Score: {audit.score})."
                )
            else:
                logger.warning(
                    f"[Scheduler 16:45] {store.name}: Nenhuma auditoria valida hoje. "
                    f"Disparando lembrete no grupo ({store.whatsapp_group_jid})."
                )
                message = (
                    "⚠️ *Lembrete Operacional:* Nenhuma foto de auditoria válida foi registrada "
                    "para a loja hoje. Favor o atendente do turno tirar a foto das gôndolas "
                    "nos próximos 15 minutos."
                )

                # Dispara lembrete DENTRO DO GRUPO da loja
                sent = await evolution_service.send_text_message(store.whatsapp_group_jid, message)

                # Registra alerta no banco
                await save_alert(
                    session=session,
                    store_id=store.id,
                    message=f"Lembrete amigavel em grupo 16h45: {message} (Enviado={sent})",
                    sent_to_director=False,
                )
                notified_groups.append(store.name)

    logger.info(
        f"[Scheduler 16:45] Concluido. Lojas em dia: {len(already_sent)} | Grupos lembrados: {len(notified_groups)}"
    )
    return {"already_sent": already_sent, "notified_groups": notified_groups}


async def check_critical_unresponsive_stores_1700() -> List[str]:
    """
    Rotina executada às 17h00.
    Verifica se após o lembrete das 16h45 alguma loja AINDA permanece sem auditoria válida.
    Para lojas que continuam sem foto, dispara alerta exclusivo no WhatsApp privado do DIRECTOR_PHONE.
    """
    today = date.today()
    logger.info(f"[Scheduler 17:00] Verificando lojas ainda pendentes apos tolerancia...")

    critical_stores = []

    async with async_session_factory() as session:
        stores = await get_all_stores(session)

        for store in stores:
            audit = await has_valid_daily_audit(session, store.id, today)

            if not audit:
                critical_stores.append(store.name)
                logger.error(
                    f"[Scheduler 17:00] ALERTA CRITICO: {store.name} permanece sem auditoria valida as 17h00."
                )

                director_message = (
                    f"⚠️ Alerta Diretoria - Tolerância Excedida:\n"
                    f"A {store.name} (Grupo: {store.whatsapp_group_jid} | Gerente: {store.manager_name}) "
                    f"NÃO registrou nenhuma foto de auditoria válida hoje até às 17h00, "
                    f"mesmo após lembrete operacional no grupo."
                )

                # Dispara alerta exclusivo no WhatsApp privado do diretor
                await evolution_service.send_text_message(settings.DIRECTOR_PHONE, director_message)

                # Registra alerta no banco
                await save_alert(
                    session=session,
                    store_id=store.id,
                    message=director_message,
                    sent_to_director=True,
                )

    return critical_stores


async def send_executive_summary_1715() -> str:
    """
    Rotina executada às 17h15.
    Envia um resumo telegráfico consolidado no WhatsApp privado do Diretor.
    Exemplo:
        📊 Fechamento Auditoria 17h:
        - Loja 1: OK
        - Loja 2: OK
        - Loja 3: PENDENTE (Grupo cobrado)
    """
    today = date.today()
    logger.info(f"[Scheduler 17:15] Gerando resumo executivo consolidado para o Diretor...")

    lines = ["📊 Fechamento Auditoria 17h:"]

    async with async_session_factory() as session:
        stores = await get_all_stores(session)

        for store in stores:
            audit = await has_valid_daily_audit(session, store.id, today)

            if audit:
                if audit.status == "OK":
                    status_label = f"OK (Score: {audit.score or 0})"
                elif audit.status == "ALERT":
                    status_label = f"ALERT (Score: {audit.score or 0})"
                else:
                    status_label = audit.status
            else:
                status_label = "PENDENTE (Grupo cobrado)"

            lines.append(f"- {store.name}: {status_label}")

        summary_text = "\n".join(lines)
        logger.info(f"[Scheduler 17:15] Resumo gerado:\n{summary_text}")

        # Envia no WhatsApp privado do diretor
        await evolution_service.send_text_message(settings.DIRECTOR_PHONE, summary_text)

        # Salva o resumo no banco
        if stores:
            await save_alert(
                session=session,
                store_id=stores[0].id,
                message=f"Fechamento 17h15 enviado ao Diretor:\n{summary_text}",
                sent_to_director=True,
            )

    return summary_text


def setup_scheduler() -> AsyncIOScheduler:
    """
    Registra os 3 jobs diários no APScheduler:
    - 16h45: Lembrete nos GRUPOS das lojas pendentes
    - 17h00: Alerta de tolerância excedida no privado da Diretoria
    - 17h15: Fechamento executivo telegráfico no privado da Diretoria
    """
    scheduler.add_job(
        check_afternoon_audits_tolerance_1645,
        trigger=CronTrigger(hour=16, minute=45),
        id="job_tolerance_1645",
        name="Lembrete no Grupo 16h45",
        replace_existing=True,
    )

    scheduler.add_job(
        check_critical_unresponsive_stores_1700,
        trigger=CronTrigger(hour=17, minute=0),
        id="job_critical_1700",
        name="Alerta Inadimplência Diretoria 17h00",
        replace_existing=True,
    )

    scheduler.add_job(
        send_executive_summary_1715,
        trigger=CronTrigger(hour=17, minute=15),
        id="job_summary_1715",
        name="Resumo Executivo Diretoria 17h15",
        replace_existing=True,
    )

    logger.info("[Scheduler] Jobs das 16h45, 17h00 e 17h15 agendados com sucesso.")
    return scheduler
