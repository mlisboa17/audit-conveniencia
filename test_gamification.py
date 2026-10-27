"""
Script de testes automatizados para Gamificação, Turnos e Operadores (Fase 2).
Valida:
1. resolve_shift (T1_MANHA, T2_TARDE, T3_NOITE)
2. calculate_audit_points (bônus de excelência e pontualidade antes das 16h45)
3. Extração e normalização de nomes de operadores via legenda e texto
4. Criação e acumulação de pontos em operators
5. Atribuição de operador em auditorias recentes sem nome (janela de 15 min)
6. Cálculo completo do ranking (Lojas, Turnos, Top 5 e Card WhatsApp)
"""

import asyncio
from datetime import date, datetime, timedelta
import logging

from database import (
    Audit,
    Operator,
    Store,
    add_points_to_operator,
    assign_operator_to_audit,
    async_session_factory,
    calculate_audit_points,
    get_all_stores,
    get_or_create_operator,
    get_ranking_data,
    get_recent_unassigned_audit,
    init_db,
    resolve_shift,
    save_audit,
)
from services.evolution_service import evolution_service

logging.basicConfig(level=logging.INFO, format="%(message)s")


def test_resolve_shift():
    print("\n--- [Teste 1] Resolução Automática de Turnos ---")
    t1_start = datetime(2026, 10, 27, 6, 0)
    t1_mid = datetime(2026, 10, 27, 10, 30)
    t1_end = datetime(2026, 10, 27, 13, 59)
    assert resolve_shift(t1_start) == "T1_MANHA", "06:00 deve ser T1_MANHA"
    assert resolve_shift(t1_mid) == "T1_MANHA", "10:30 deve ser T1_MANHA"
    assert resolve_shift(t1_end) == "T1_MANHA", "13:59 deve ser T1_MANHA"

    t2_start = datetime(2026, 10, 27, 14, 0)
    t2_mid = datetime(2026, 10, 27, 18, 0)
    t2_end = datetime(2026, 10, 27, 21, 59)
    assert resolve_shift(t2_start) == "T2_TARDE", "14:00 deve ser T2_TARDE"
    assert resolve_shift(t2_mid) == "T2_TARDE", "18:00 deve ser T2_TARDE"
    assert resolve_shift(t2_end) == "T2_TARDE", "21:59 deve ser T2_TARDE"

    t3_start = datetime(2026, 10, 27, 22, 0)
    t3_midnight = datetime(2026, 10, 27, 0, 15)
    t3_end = datetime(2026, 10, 27, 5, 59)
    assert resolve_shift(t3_start) == "T3_NOITE", "22:00 deve ser T3_NOITE"
    assert resolve_shift(t3_midnight) == "T3_NOITE", "00:15 deve ser T3_NOITE"
    assert resolve_shift(t3_end) == "T3_NOITE", "05:59 deve ser T3_NOITE"
    print("  [OK] Todos os intervalos de turnos validados com sucesso.")


def test_calculate_audit_points():
    print("\n--- [Teste 2] Motor de Gamificação e Pontos ---")

    # Score >= 9.0 com bônus de pontualidade (< 16h45)
    early_time = datetime(2026, 10, 27, 11, 0)
    pts1 = calculate_audit_points(9.5, early_time)
    assert pts1 == 130.0, f"Esperado 130.0 (120+10), obtido {pts1}"

    # Score >= 9.0 após as 16h45 (sem bônus de pontualidade)
    late_time = datetime(2026, 10, 27, 17, 30)
    pts2 = calculate_audit_points(9.0, late_time)
    assert pts2 == 120.0, f"Esperado 120.0, obtido {pts2}"

    # Score entre 7.5 e 8.9 antes das 16h45
    pts3 = calculate_audit_points(8.0, early_time)
    assert pts3 == 100.0, f"Esperado 100.0 (90+10), obtido {pts3}"

    # Score entre 6.0 e 7.4 após 16h45
    pts4 = calculate_audit_points(6.5, late_time)
    assert pts4 == 60.0, f"Esperado 60.0, obtido {pts4}"

    # Score < 6.0 antes das 16h45
    pts5 = calculate_audit_points(5.0, early_time)
    assert pts5 == 30.0, f"Esperado 30.0 (20+10), obtido {pts5}"

    # Score 0.0 (foto inválida)
    pts0 = calculate_audit_points(0.0, early_time)
    assert pts0 == 0.0, f"Esperado 0.0, obtido {pts0}"

    print("  [OK] Regras de pontuação, faixas e bônus de pontualidade validadas.")


def test_operator_name_extraction():
    print("\n--- [Teste 3] Extração e Normalização de Nome de Operador ---")

    test_cases = [
        ("Carlos", "Carlos"),
        ("Marcos T1", "Marcos"),
        ("Operador João", "João"),
        ("Operadora Maria Silva", "Maria Silva"),
        ("Auditoria por Lucas", "Lucas"),
        ("Turno 2 - Fernanda", "Fernanda"),
        ("Atendente Beatriz T2", "Beatriz"),
        ("pedro henrique", "Pedro Henrique"),
        ("!ranking", None),
        ("ranking", None),
        ("ok", None),
        ("valeu", None),
        ("", None),
        (None, None),
    ]

    for raw, expected in test_cases:
        res = evolution_service.extract_operator_name_from_text(raw)
        assert res == expected, f"Falha para '{raw}': esperado '{expected}', obtido '{res}'"

    print("  [OK] Todos os casos de extração de operadores validados com sucesso.")


async def test_database_gamification_flow():
    print("\n--- [Teste 4] Fluxo de Banco de Dados: Operadores, Auditorias e Ranking ---")
    await init_db()

    async with async_session_factory() as session:
        stores = await get_all_stores(session)
        assert len(stores) >= 3, "Lojas padrão devem estar cadastradas"
        store1 = stores[0]
        store2 = stores[1]

        # 1. Cria ou busca operador
        op1 = await get_or_create_operator(session, store1.id, "Carlos Silva")
        assert op1.name == "Carlos Silva"

        # 2. Acumula pontos
        await add_points_to_operator(session, store1.id, "Carlos Silva", 120.0)
        assert op1.total_points >= 120.0

        op2 = await add_points_to_operator(session, store2.id, "Fernanda Souza", 90.0)
        assert op2.name == "Fernanda Souza"
        assert op2.total_points >= 90.0

        # 3. Testa auditoria sem operador e resposta rápida
        audit_unassigned = await save_audit(
            session=session,
            store_id=store1.id,
            audit_date=date.today(),
            shift="T1_MANHA",
            status="OK",
            score=8.5,
            ai_analysis='{"score": 8.5}',
            operator_name=None,
            points=100.0,
        )
        assert audit_unassigned.operator_name is None

        # Busca auditoria recente sem operador (últimos 15 min)
        recent = await get_recent_unassigned_audit(session, store1.id, max_minutes=15)
        assert recent is not None
        assert recent.id == audit_unassigned.id

        # Atribui operador "Marcos" à auditoria
        updated_audit = await assign_operator_to_audit(session, recent.id, "Marcos")
        assert updated_audit.operator_name == "Marcos"

        # Verifica se Marcos recebeu os 100 pontos
        op_marcos = await get_or_create_operator(session, store1.id, "Marcos")
        assert op_marcos.total_points >= 100.0
        print("  [OK] Atribuição de operador por resposta rápida validada.")

        # 4. Testa geração de ranking
        ranking = await get_ranking_data(session)
        assert "stores_ranking" in ranking
        assert "shifts_ranking" in ranking
        assert "top_operators" in ranking
        assert "whatsapp_card" in ranking

        card = ranking["whatsapp_card"]
        try:
            print("\nCard Gerado para WhatsApp:\n" + card)
        except UnicodeEncodeError:
            print("\nCard Gerado para WhatsApp (sanitizado):\n" + card.encode("ascii", errors="replace").decode("ascii"))
        assert "🏆 *Placar Geral de Auditoria*" in card
        assert "🏬 *Lojas:*" in card
        assert "☀️ *Turno Líder:*" in card
        assert "⭐ *Top Operadores:*" in card
        print("\n  [OK] Ranking e Card de WhatsApp gerados perfeitamente.")


if __name__ == "__main__":
    test_resolve_shift()
    test_calculate_audit_points()
    test_operator_name_extraction()
    asyncio.run(test_database_gamification_flow())
    print("\n" + "=" * 65)
    print("[SUCESSO] TODOS OS TESTES DE GAMIFICACAO PASSARAM COM 100% DE SUCESSO!")
    print("=" * 65 + "\n")
