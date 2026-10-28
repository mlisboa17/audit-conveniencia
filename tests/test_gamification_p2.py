"""
Suíte de Testes Automatizados para a Fase P2:
Gamificação Auditável, Limites Anti-Exploit e Trilha de Contestação.

Cobre:
1. test_limite_teto_bonus_correcao: 3 primeiras correções recebem +25 pts;
   a 4ª fecha o ticket com 0 pontos adicionais e aviso de limite atingido.
2. test_comando_justificar_ticket: Transforma ticket ABERTO em JUSTIFICADO via WhatsApp
   (!justificar <ID> <MOTIVO>) e impede que continue contando como falha.
3. test_comando_pendencias_loja: Comando !pendencias retorna tickets ativos dentro do TTL
   e aplica lazy sweep em tickets vencidos.
4. test_bonus_consistencia_loja_padrao_10: Fechamento de turno com 4 setores acima de 9.0
   e zero tickets não resolvidos credita +50 pontos divididos entre operadores do turno.
"""

import json
import uuid
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from database import (
    Audit,
    AuditTicket,
    Operator,
    aplicar_correcao_ticket,
    async_session_factory,
    buscar_ticket_por_id_ou_prefixo,
    buscar_tickets_abertos_loja,
    criar_tickets_pendencia,
    get_or_create_operator,
    init_db,
    justificar_ticket,
)
from main import app
from services.evaluator_service import avaliar_consistencia_turno


@pytest.fixture(autouse=True)
async def setup_database():
    """Inicializa banco e limpa tabelas operacionais antes de cada teste."""
    await init_db()
    async with async_session_factory() as session:
        await session.execute(text("DELETE FROM audit_tickets"))
        await session.execute(text("DELETE FROM audits"))
        await session.execute(text("DELETE FROM operators"))
        await session.commit()


@pytest.mark.asyncio
async def test_limite_teto_bonus_correcao():
    """
    1. test_limite_teto_bonus_correcao:
       As 3 primeiras correções de um operador no turno recebem +25 pts (+75 max).
       A 4ª correção fecha o ticket como CORRIGIDO, mas credita 0 pontos com aviso de limite.
    """
    print("\n--- [Teste 1] Limite Anti-Exploit de Bônus de Correção (Máx 3 por Turno) ---")
    
    today = date.today()
    shift = "T1_MANHA"

    async with async_session_factory() as session:
        op = await get_or_create_operator(session, store_id=1, name="Mariana Santos")
        assert op.total_points == 0.0

        # Cria 4 tickets de inconformidade abertos para o turno da manhã
        ticket_ids = []
        for i in range(1, 5):
            t = AuditTicket(
                id=str(uuid.uuid4()),
                audit_origem_id=100 + i,
                store_id=1,
                operator_id="Mariana Santos",
                setor="FREEZER_GELADEIRA",
                criterio="espaco_vazio",
                descricao_falha=f"Falha de teste #{i}",
                foto_origem_path=f"media_tickets/test_{i}.jpg",
                status="ABERTO",
                created_at=datetime.utcnow(),
                expires_at=datetime.utcnow() + timedelta(minutes=20),
                shift=shift,
            )
            session.add(t)
            ticket_ids.append(t.id)
        await session.commit()

        # Correção 1: deve creditar 25 pts
        t1, bonus1, aviso1 = await aplicar_correcao_ticket(
            session=session,
            ticket_id=ticket_ids[0],
            store_id=1,
            operator_name="Mariana Santos",
            shift=shift,
            data_ref=today,
        )
        assert t1.status == "CORRIGIDO"
        assert bonus1 == 25
        assert t1.bonus_creditado == 25
        assert aviso1 is None

        # Correção 2: deve creditar 25 pts
        t2, bonus2, aviso2 = await aplicar_correcao_ticket(
            session=session,
            ticket_id=ticket_ids[1],
            store_id=1,
            operator_name="Mariana Santos",
            shift=shift,
            data_ref=today,
        )
        assert t2.status == "CORRIGIDO"
        assert bonus2 == 25
        assert t2.bonus_creditado == 25
        assert aviso2 is None

        # Correção 3: deve creditar 25 pts (total acumulado = 75 pts)
        t3, bonus3, aviso3 = await aplicar_correcao_ticket(
            session=session,
            ticket_id=ticket_ids[2],
            store_id=1,
            operator_name="Mariana Santos",
            shift=shift,
            data_ref=today,
        )
        assert t3.status == "CORRIGIDO"
        assert bonus3 == 25
        assert t3.bonus_creditado == 25
        assert aviso3 is None

        # Verifica pontuação intermediária do operador
        await session.refresh(op)
        assert op.total_points == 75.0

        # Correção 4: Teto atingido! Ticket fecha como CORRIGIDO, mas bonus = 0
        t4, bonus4, aviso4 = await aplicar_correcao_ticket(
            session=session,
            ticket_id=ticket_ids[3],
            store_id=1,
            operator_name="Mariana Santos",
            shift=shift,
            data_ref=today,
        )
        assert t4.status == "CORRIGIDO"
        assert bonus4 == 0
        assert t4.bonus_creditado == 0
        assert aviso4 is not None
        assert "Limite de bônus de correção atingido" in aviso4

        # Pontuação total deve permanecer em 75.0
        await session.refresh(op)
        assert op.total_points == 75.0
        print("  [OK] 3 primeiras correções creditaram +25 pts; 4ª fechou com 0 pts e aviso de limite.")


@pytest.mark.asyncio
async def test_comando_justificar_ticket():
    """
    2. test_comando_justificar_ticket:
       Transforma ticket ABERTO em JUSTIFICADO via WhatsApp (!justificar <ID> <MOTIVO>).
       Registra justificativa, autor e data de resolução.
    """
    print("\n--- [Teste 2] Comando WhatsApp !justificar <ID> <MOTIVO> ---")
    
    # 1. Cria um ticket em aberto
    async with async_session_factory() as session:
        t = AuditTicket(
            id=str(uuid.uuid4()),
            audit_origem_id=201,
            store_id=1,
            operator_id="Lucas",
            setor="ESTUFA_SALGADOS",
            criterio="abastecimento",
            descricao_falha="Falta de salgados assados na estufa",
            foto_origem_path="media_tickets/estufa_vazia.jpg",
            status="ABERTO",
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(minutes=20),
            shift="T1_MANHA",
        )
        session.add(t)
        await session.commit()
        ticket_id = t.id
        short_id = ticket_id[:8]

    # 2. Envia comando operacional via Webhook
    payload = {
        "event": "messages.upsert",
        "instance": "AuditConveniencia",
        "data": {
            "key": {
                "remoteJid": "120363430718119886@g.us",
                "fromMe": False,
                "id": "MSG_JUSTIFICAR_01",
                "participant": "5511999998888",
            },
            "message": {
                "conversation": f"!justificar {short_id} Falta de entrega pelo fornecedor matinal"
            },
        },
    }

    with patch("main.evolution_service.send_text_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = True
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/webhook/evolution", json=payload)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ticket_justified"
            assert data["ticket_id"] == ticket_id
            assert "Falta de entrega" in data["motivo"]

        # Verifica mensagem enviada no WhatsApp
        mock_send.assert_called_once()
        msg_enviada = mock_send.call_args[0][1]
        assert "JUSTIFICATIVA REGISTRADA COM SUCESSO" in msg_enviada
        assert short_id in msg_enviada

    # 3. Verifica no banco se o status foi alterado para JUSTIFICADO
    async with async_session_factory() as session:
        t_db = await buscar_ticket_por_id_ou_prefixo(ticket_id, session=session)
        assert t_db is not None
        assert t_db.status == "JUSTIFICADO"
        assert t_db.justificativa_texto == "Falta de entrega pelo fornecedor matinal"
        assert t_db.justificado_por is not None
        assert t_db.resolved_at is not None

        # Tentativa de justificar novamente deve retornar False
        repetido = await justificar_ticket(ticket_id, "Outro motivo", "Admin", session=session)
        assert repetido is False
        print("  [OK] Ticket justificado com sucesso no banco e replicado no grupo da loja.")


@pytest.mark.asyncio
async def test_comando_pendencias_loja():
    """
    3. test_comando_pendencias_loja:
       Comando !pendencias lista tickets ativos dentro do TTL com tempo restante
       e realiza lazy sweep de tickets expirados.
    """
    print("\n--- [Teste 3] Comando WhatsApp !pendencias (Listagem com TTL) ---")
    
    now = datetime.utcnow()
    # 1. Cria 2 tickets ativos e 1 expirado
    async with async_session_factory() as session:
        t_ativo1 = AuditTicket(
            id=str(uuid.uuid4()),
            audit_origem_id=301,
            store_id=1,
            operator_id="Beatriz",
            setor="FREEZER_GELADEIRA",
            criterio="espaco_vazio",
            descricao_falha="Repor refrigerante lata 350ml",
            foto_origem_path="media_tickets/p1.jpg",
            status="ABERTO",
            created_at=now,
            expires_at=now + timedelta(minutes=15),
            shift="T2_TARDE",
        )
        t_ativo2 = AuditTicket(
            id=str(uuid.uuid4()),
            audit_origem_id=302,
            store_id=1,
            operator_id="Beatriz",
            setor="GONDOLA_GERAL",
            criterio="organizacao",
            descricao_falha="Alinhar salgadinhos Torcida",
            foto_origem_path="media_tickets/p2.jpg",
            status="ABERTO",
            created_at=now,
            expires_at=now + timedelta(minutes=18),
            shift="T2_TARDE",
        )
        t_expirado = AuditTicket(
            id=str(uuid.uuid4()),
            audit_origem_id=303,
            store_id=1,
            operator_id="Beatriz",
            setor="BANHEIRO",
            criterio="reposicao_insumos",
            descricao_falha="Repor papel toalha",
            foto_origem_path="media_tickets/p3.jpg",
            status="ABERTO",
            created_at=now - timedelta(minutes=40),
            expires_at=now - timedelta(minutes=20),  # Venceu há 20 min
            shift="T2_TARDE",
        )
        session.add_all([t_ativo1, t_ativo2, t_expirado])
        await session.commit()

    # 2. Executa comando !pendencias via Webhook
    payload = {
        "event": "messages.upsert",
        "instance": "AuditConveniencia",
        "data": {
            "key": {
                "remoteJid": "120363430718119886@g.us",
                "fromMe": False,
                "id": "MSG_PENDENCIAS_01",
            },
            "message": {
                "conversation": "!pendencias"
            },
        },
    }

    with patch("main.evolution_service.send_text_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = True
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/webhook/evolution", json=payload)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "pendencias_listed"
            assert data["total"] == 2  # Apenas os 2 ativos

        mock_send.assert_called_once()
        msg_enviada = mock_send.call_args[0][1]
        assert "PENDÊNCIAS EM ABERTO" in msg_enviada
        assert "Repor refrigerante lata 350ml" in msg_enviada
        assert "Alinhar salgadinhos Torcida" in msg_enviada
        assert "Repor papel toalha" not in msg_enviada

    # 3. Verifica no banco se o ticket vencido virou EXPIRADO
    async with async_session_factory() as session:
        t_exp_db = await buscar_ticket_por_id_ou_prefixo(t_expirado.id, session=session)
        assert t_exp_db.status == "EXPIRADO"
        print("  [OK] Comando !pendencias listou os 2 tickets válidos e expirou o ticket atrasado.")


@pytest.mark.asyncio
async def test_bonus_consistencia_loja_padrao_10():
    """
    4. test_bonus_consistencia_loja_padrao_10:
       Fechamento de turno com no mínimo 4 setores auditados com nota >= 9.0
       e zero tickets pendentes credita +50 pontos de consistência divididos entre os operadores.
    """
    print("\n--- [Teste 4] Bônus de Consistência (Loja Padrão 10 - +50 Pontos) ---")
    
    today = date.today()
    shift = "T1_MANHA"

    # 1. Cria 4 auditorias conformes em 4 setores distintos com nota >= 9.0
    setores_dados = [
        ("FREEZER_GELADEIRA", 9.5, "Carlos Silva"),
        ("GONDOLA_GERAL", 9.2, "Carlos Silva"),
        ("ESTUFA", 9.0, "Ana Paula"),
        ("CAIXA", 10.0, "Ana Paula"),
    ]

    async with async_session_factory() as session:
        for idx, (setor, nota, op_name) in enumerate(setores_dados, 1):
            ai_data = {
                "setor": setor,
                "score": nota,
                "avaliacao": {
                    "setor": setor,
                    "nota_calculada": nota,
                    "criterios_conformes": 4,
                    "total_criterios_avaliados": 4,
                },
            }
            a = Audit(
                store_id=1,
                date=today,
                shift=shift,
                status="OK",
                score=nota,
                ai_analysis=json.dumps(ai_data),
                image_url=f"foto_setor_{idx}.jpg",
                operator_name=op_name,
                points=100.0,
            )
            session.add(a)

        # Garante operadores cadastrados com 0 pontos adicionais
        op_carlos = await get_or_create_operator(session, store_id=1, name="Carlos Silva")
        op_carlos.total_points = 0.0
        op_ana = await get_or_create_operator(session, store_id=1, name="Ana Paula")
        op_ana.total_points = 0.0
        await session.commit()

    # 2. Avalia consistência do turno: Deve conceder +50 pontos (25 para cada)
    res = await avaliar_consistencia_turno(store_id=1, turno=shift, data=today)
    assert res["elegivel"] is True
    assert res["loja_padrao_10"] is True
    assert res["total_setores"] == 4
    assert res["pontos_totais"] == 50.0
    assert res["pontos_por_operador"] == 25.0
    assert set(res["operadores"]) == {"Ana Paula", "Carlos Silva"}

    # 3. Verifica no banco se cada operador recebeu 25 pontos
    async with async_session_factory() as session:
        stmt_c = select(Operator).where(Operator.store_id == 1, Operator.name == "Carlos Silva")
        res_c = await session.execute(stmt_c)
        op_c = res_c.scalar_one()
        assert op_c.total_points == 25.0

        stmt_a = select(Operator).where(Operator.store_id == 1, Operator.name == "Ana Paula")
        res_a = await session.execute(stmt_a)
        op_a = res_a.scalar_one()
        assert op_a.total_points == 25.0

    # 4. Caso Negativo: Se houver um ticket ABERTO não resolvido, o bônus não é concedido
    async with async_session_factory() as session:
        t_pend = AuditTicket(
            id=str(uuid.uuid4()),
            audit_origem_id=999,
            store_id=1,
            operator_id="Carlos Silva",
            setor="BANHEIRO",
            criterio="limpeza",
            descricao_falha="Lixeira transbordando",
            foto_origem_path="media_tickets/lixeira.jpg",
            status="ABERTO",
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(minutes=20),
            shift=shift,
        )
        session.add(t_pend)
        await session.commit()

    res_negativo = await avaliar_consistencia_turno(store_id=1, turno=shift, data=today)
    assert res_negativo["elegivel"] is False
    assert res_negativo["loja_padrao_10"] is False
    assert res_negativo["tickets_nao_resolvidos"] > 0
    print("  [OK] Bônus de consistência (+50 pts) concedido com 4 setores >= 9.0 e bloqueado com ticket pendente.")
