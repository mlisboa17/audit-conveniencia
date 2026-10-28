"""
Suíte de Testes Automatizados para a Fase P1:
Ciclo de Correção Antes/Depois e Tickets de Pendência (WhatsApp & PWA).

Cobre:
1. Abertura correta de ticket com expiração de 20 minutos (TTL).
2. Comparação com mock do Gemini retornando problema_resolvido = True -> fecha ticket e adiciona +25 bônus.
3. Ticket expirado não deve creditar bônus retroativo.
4. Foto de setor divergente não valida a pendência (mantém ticket ABERTO).
"""

import base64
import os
import uuid
from datetime import datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from database import (
    Audit,
    AuditTicket,
    Operator,
    Store,
    async_session_factory,
    buscar_ticket_aberto,
    criar_tickets_pendencia,
    fechar_ticket,
    get_or_create_operator,
    init_db,
)
from main import app
from services.gemini_service import gemini_service, GeminiCorrecaoOutput


@pytest.fixture(autouse=True)
async def setup_database():
    """Garante tabelas inicializadas e limpa tickets de pendência antes de cada teste."""
    await init_db()
    async with async_session_factory() as session:
        from sqlalchemy import text
        await session.execute(text("DELETE FROM audit_tickets"))
        await session.commit()


@pytest.mark.asyncio
async def test_abertura_ticket_expiracao_20_minutos():
    """1. Abertura correta de ticket com expiração de 20 minutos."""
    print("\n--- [Teste 1] Abertura de Ticket com TTL de 20 Minutos ---")
    
    # Cria ticket com item corrigível
    itens = [
        {
            "criterio": "espaco_vazio",
            "descricao": "Puxar 2 latas de refrigerante para a borda frontal",
            "localizacao": "grade intermediária",
        }
    ]
    foto_mock_path = "media_tickets/test_antes_01.jpg"
    os.makedirs("media_tickets", exist_ok=True)
    with open(foto_mock_path, "wb") as f:
        f.write(b"mock_bytes_antes_freezer")

    tickets = await criar_tickets_pendencia(
        audit_id=1,
        store_id=1,
        operator_id="Carlos Silva",
        setor="FREEZER_GELADEIRA",
        itens_corrigiveis=itens,
        foto_path=foto_mock_path,
    )

    assert len(tickets) == 1
    t = tickets[0]
    assert t.status == "ABERTO"
    assert t.setor == "FREEZER_GELADEIRA"
    assert t.criterio == "espaco_vazio"
    assert t.descricao_falha == "Puxar 2 latas de refrigerante para a borda frontal"
    assert t.foto_origem_path == foto_mock_path
    assert t.bonus_creditado == 0

    # Validação do TTL de 20 minutos (1200 segundos) com tolerância de 5 segundos
    delta_segundos = (t.expires_at - t.created_at).total_seconds()
    assert abs(delta_segundos - 1200) < 5, f"Esperado ~1200s (20 min), obtido {delta_segundos}s"

    # Busca o ticket aberto
    ticket_encontrado = await buscar_ticket_aberto(store_id=1, setor="FREEZER_GELADEIRA")
    assert ticket_encontrado is not None
    assert ticket_encontrado.id == t.id
    print("  [OK] Ticket aberto persistido com TTL de exatamente 20 minutos.")


@pytest.mark.asyncio
async def test_comparacao_correcao_resolvida_fecha_ticket_e_adiciona_bonus():
    """2. Comparação com mock do Gemini retornando problema_resolvido = True -> fecha ticket e adiciona bônus."""
    print("\n--- [Teste 2] Resolução de Ticket com Comparação e +25 Bônus ---")

    # 1. Garante que há um operador e anota seus pontos iniciais
    async with async_session_factory() as session:
        op = await get_or_create_operator(session, store_id=1, name="Carlos Silva")
        pontos_iniciais = op.total_points

    # 2. Cria ticket aberto na loja 1
    foto_antes_path = "media_tickets/test_antes_corrigir.jpg"
    with open(foto_antes_path, "wb") as f:
        f.write(b"imagem_antes_com_falha")

    itens = [{"criterio": "espaco_vazio", "descricao": "Repor fileira de águas"}]
    tickets = await criar_tickets_pendencia(
        audit_id=1,
        store_id=1,
        operator_id="Carlos Silva",
        setor="FREEZER_GELADEIRA",
        itens_corrigiveis=itens,
        foto_path=foto_antes_path,
    )
    ticket_id = tickets[0].id

    # 3. Envia foto de correção pelo Webhook (Evolution API)
    # Foto de correção simulada (> 3000 bytes)
    foto_depois_bytes = b"\xff\xd8\xff" + b"0" * 3500 + b"\xff\xd9"
    b64_str = base64.b64encode(foto_depois_bytes).decode("ascii")

    payload = {
        "event": "messages.upsert",
        "instance": "AuditConveniencia",
        "data": {
            "key": {
                "remoteJid": "120363430718119886@g.us",
                "fromMe": False,
                "id": "MSG_CORRECAO_01"
            },
            "message": {
                "imageMessage": {
                    "caption": "Correção realizada por Carlos Silva",
                    "mimetype": "image/jpeg",
                    "base64": b64_str,
                    "contextInfo": {
                        "isForwarded": False,
                        "forwardingScore": 0
                    }
                }
            }
        }
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhook/evolution", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ticket_corrected"
        assert data["ticket_id"] == ticket_id
        assert data["bonus"] == 25

    # 4. Verifica no banco se o ticket foi fechado como CORRIGIDO com 25 pontos
    async with async_session_factory() as session:
        stmt = select(AuditTicket).where(AuditTicket.id == ticket_id)
        res = await session.execute(stmt)
        ticket_db = res.scalars().first()
        assert ticket_db.status == "CORRIGIDO"
        assert ticket_db.bonus_creditado == 25
        assert ticket_db.resolved_at is not None

        # Verifica se o operador recebeu os 25 pontos de bônus
        op_atualizado = await get_or_create_operator(session, store_id=1, name="Carlos Silva")
        assert op_atualizado.total_points == round(pontos_iniciais + 25.0, 1)

    print("  [OK] Ticket fechado como CORRIGIDO e +25 pontos creditados ao operador.")


@pytest.mark.asyncio
async def test_ticket_expirado_nao_credita_bonus_retroativo():
    """3. Ticket expirado não deve creditar bônus retroativo."""
    print("\n--- [Teste 3] Ticket Expirado Bloqueia Bônus Retroativo ---")

    # 1. Cria manualmente um ticket já expirado (criado há 30 min, expirado há 10 min)
    now = datetime.utcnow()
    created_old = now - timedelta(minutes=30)
    expires_old = now - timedelta(minutes=10)
    ticket_exp_id = str(uuid.uuid4())

    async with async_session_factory() as session:
        op = await get_or_create_operator(session, store_id=1, name="Marcos")
        pontos_iniciais = op.total_points

        t_exp = AuditTicket(
            id=ticket_exp_id,
            audit_origem_id=1,
            store_id=1,
            operator_id="Marcos",
            setor="FREEZER_GELADEIRA",
            criterio="limpeza",
            descricao_falha="Vidro embaçado",
            foto_origem_path="media_tickets/dummy_exp.jpg",
            status="ABERTO",
            created_at=created_old,
            expires_at=expires_old,
            bonus_creditado=0,
        )
        session.add(t_exp)
        await session.commit()

    # 2. Busca ticket aberto e garante que ele é ignorado e marcado como EXPIRADO
    ticket_busca = await buscar_ticket_aberto(store_id=1, setor="FREEZER_GELADEIRA")
    assert ticket_busca is None or ticket_busca.id != ticket_exp_id

    async with async_session_factory() as session:
        stmt = select(AuditTicket).where(AuditTicket.id == ticket_exp_id)
        res = await session.execute(stmt)
        ticket_db = res.scalars().first()
        assert ticket_db.status == "EXPIRADO"
        assert ticket_db.bonus_creditado == 0

    # 3. Envia foto pelo webhook para a loja: como o ticket expirou, NÃO deve resolver o ticket nem dar bônus de 25
    foto_bytes = b"\xff\xd8\xff" + b"0" * 4010 + b"\xff\xd9"
    b64_str = base64.b64encode(foto_bytes).decode("ascii")
    payload = {
        "event": "messages.upsert",
        "instance": "AuditConveniencia",
        "data": {
            "key": {
                "remoteJid": "120363430718119886@g.us",
                "fromMe": False,
                "id": "MSG_EXPIRADO_TEST"
            },
            "message": {
                "imageMessage": {
                    "caption": "Marcos",
                    "mimetype": "image/jpeg",
                    "base64": b64_str,
                    "contextInfo": {"isForwarded": False, "forwardingScore": 0}
                }
            }
        }
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhook/evolution", json=payload)
        assert resp.status_code == 200
        # Não deve ser ticket_corrected
        assert resp.json().get("status") != "ticket_corrected"

    # Confirma que o ticket expirado não recebeu bônus
    async with async_session_factory() as session:
        stmt = select(AuditTicket).where(AuditTicket.id == ticket_exp_id)
        res = await session.execute(stmt)
        ticket_db = res.scalars().first()
        assert ticket_db.bonus_creditado == 0
        assert ticket_db.status == "EXPIRADO"

    print("  [OK] Ticket expirado permaneceu sem bônus e não interceptou a nova auditoria.")


@pytest.mark.asyncio
async def test_foto_setor_divergente_nao_valida_pendencia():
    """4. Foto de setor divergente não valida a pendência (mantém ticket ABERTO)."""
    print("\n--- [Teste 4] Foto de Setor Divergente Rejeita Validação ---")

    # 1. Cria ticket para o setor FREEZER_GELADEIRA
    itens = [{"criterio": "organizacao", "descricao": "Alinhar cervejas"}]
    foto_antes_path = "media_tickets/test_antes_div.jpg"
    with open(foto_antes_path, "wb") as f:
        f.write(b"foto_antes_cervejas")

    tickets = await criar_tickets_pendencia(
        audit_id=1,
        store_id=1,
        operator_id="Roberto",
        setor="FREEZER_GELADEIRA",
        itens_corrigiveis=itens,
        foto_path=foto_antes_path,
    )
    ticket_id = tickets[0].id

    # 2. Envia foto contendo o marcador b'setor_divergente'
    # O mock do Gemini retornará mesmo_setor = False
    foto_divergente_bytes = b"setor_divergente_" + b"0" * 4000
    b64_str = base64.b64encode(foto_divergente_bytes).decode("ascii")
    payload = {
        "event": "messages.upsert",
        "instance": "AuditConveniencia",
        "data": {
            "key": {
                "remoteJid": "120363430718119886@g.us",
                "fromMe": False,
                "id": "MSG_DIVERGENTE_TEST"
            },
            "message": {
                "imageMessage": {
                    "caption": "Roberto",
                    "mimetype": "image/jpeg",
                    "base64": b64_str,
                    "contextInfo": {"isForwarded": False, "forwardingScore": 0}
                }
            }
        }
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhook/evolution", json=payload)
        assert resp.status_code == 200
        # Não deve ter resolvido o ticket
        assert resp.json().get("status") != "ticket_corrected"

    # 3. Verifica que o ticket continua ABERTO e sem bônus
    async with async_session_factory() as session:
        stmt = select(AuditTicket).where(AuditTicket.id == ticket_id)
        res = await session.execute(stmt)
        ticket_db = res.scalars().first()
        assert ticket_db.status == "ABERTO"
        assert ticket_db.bonus_creditado == 0
        assert ticket_db.resolved_at is None

    print("  [OK] Foto de setor divergente rejeitou a validação e manteve o ticket ABERTO.")
