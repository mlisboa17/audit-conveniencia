"""
Script de validação automatizada para gemini_service.py (Calibração de Visão Computacional & Anti-Alucinação).
Testa:
1. Validação de formato e schema JSON exigido (com elementos_visuais_detectados preliminar).
2. Raciocínio visual obrigatório passo a passo (Chain of Thought no prompt).
3. Regra de classificação de setor inegociável (Cervejeiras/Geladeiras -> FREEZER_GELADEIRA, nunca Balcão/Caixa).
4. Salvaguarda do parser: auto-correção de setor caso elementos visuais indiquem refrigeradores/bebidas.
5. Eliminação de alucinações (proibição de fios, bobinas e maquininhas fantasmas).
6. Presença de elementos_visuais_detectados em todas as saídas do mock e análise real.
"""

import asyncio
import json
import logging
from services.gemini_service import (
    SYSTEM_AUDITOR_PROMPT,
    SectorAnalysisResult,
    gemini_service,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")


def test_system_prompt_requirements():
    print("\n--- [Teste 1] Verificação dos Requisitos no Prompt do Gemini ---")
    prompt = SYSTEM_AUDITOR_PROMPT

    # 1. Chain of Thought e Raciocínio Visual Obrigatório
    assert "elementos_visuais_detectados" in prompt, "Campo elementos_visuais_detectados deve estar no prompt"
    assert "PASSO 1" in prompt, "PASSO 1 deve estar explícito no prompt"
    assert "PASSO 2" in prompt, "PASSO 2 deve estar explícito no prompt"
    print("  [OK] Raciocínio visual Chain of Thought (Passo 1 e Passo 2) verificado no prompt.")

    # 2. Regra Inegociável de Setor
    assert "REGRA DE CLASSIFICAÇÃO DE SETOR INEGOCIÁVEL" in prompt or "REGRA DE CLASSIFICAÇÃO INEGOCIÁVEL" in prompt, (
        "Regra inegociável deve estar presente no prompt"
    )
    assert "FREEZER_GELADEIRA" in prompt
    assert "terminantemente proibido classificar como Balcão ou Caixa" in prompt.lower() or "proibido classificar como balcão" in prompt.lower(), (
        "Proibição de classificar geladeiras como balcão deve estar expressa"
    )
    print("  [OK] Regra inegociável de freezer/geladeiras vs balcão/caixa verificada.")

    # 3. Eliminação de Alucinações
    assert "ELIMINAR ALUCINAÇÕES" in prompt, "Diretriz anti-alucinação deve constar no prompt"
    assert "fios" in prompt.lower() or "bobinas" in prompt.lower() or "maquininhas" in prompt.lower(), (
        "Prompt deve proibir alucinações específicas de fios, bobinas ou maquininhas"
    )
    print("  [OK] Regras estritas anti-alucinação verificadas no prompt.")

    # 4. Reconhecimento de Setores
    sectors = [
        "FREEZER_GELADEIRA",
        "GONDOLA_PRATELEIRA",
        "ESTUFA_SALGADOS",
        "BANHEIRO",
        "BALCAO_CAIXA",
    ]
    for s in sectors:
        assert s in prompt, f"Setor {s} deve estar explicitamente presente no SYSTEM_AUDITOR_PROMPT"
    print("  [OK] Todos os setores obrigatórios definidos no prompt.")


def test_json_parsing_and_cot_safety_override():
    print("\n--- [Teste 2] Parse de JSON com Chain of Thought e Safety Override ---")

    # Amostra com elementos visuais de cervejeiras
    raw_sample = """```json
    {
      "elementos_visuais_detectados": [
        "cervejeira vertical vermelha Itaipava",
        "cervejeira vertical amarela Skol",
        "latas de cerveja",
        "garrafas pet de refrigerante"
      ],
      "setor": "FREEZER_GELADEIRA",
      "conformidade": true,
      "score": 8.4,
      "pontos_positivos": [
        "Rótulos alinhados para a frente",
        "Cervejeiras bem iluminadas"
      ],
      "pontos_melhoria": [
        "Puxar 2 latas da grade intermediária para a borda frontal",
        "Repor o espaço vazio na fileira de águas sem gás"
      ],
      "mensagem_whatsapp": "📸 *Setor: Freezer e Geladeiras de Bebidas*\\n👏 Parabéns equipe!\\n\\n⭐ *Nota do Turno:* 8.4/10"
    }
    ```"""

    parsed = gemini_service._parse_and_validate_response(raw_sample)

    assert "elementos_visuais_detectados" in parsed
    assert len(parsed["elementos_visuais_detectados"]) == 4
    assert parsed["setor"] == "FREEZER_GELADEIRA"
    assert parsed["conformidade"] is True
    assert parsed["score"] == 8.4
    print("  [OK] JSON com elementos_visuais_detectados preliminares parseado com perfeição.")

    # TESTE CRÍTICO DE SAFEGUARD: Simula a IA alucinando BALCAO_CAIXA quando há cervejeiras detectadas
    hallucinated_sample = """{
      "elementos_visuais_detectados": [
        "cervejeira vertical Itaipava cheia de latas",
        "cervejeira vertical Skol com garrafas"
      ],
      "setor": "BALCAO_CAIXA",
      "conformidade": true,
      "score": 7.5,
      "pontos_positivos": ["Geladeiras organizadas"],
      "pontos_melhoria": [
        "Organizar fios de maquininha",
        "Puxar latas para a frente"
      ],
      "mensagem_whatsapp": "📸 *Setor: Balcão e Caixa*..."
    }"""

    corrected = gemini_service._parse_and_validate_response(hallucinated_sample)
    assert corrected["setor"] == "FREEZER_GELADEIRA", (
        f"Salvaguarda do parser falhou! Esperado 'FREEZER_GELADEIRA', obtido '{corrected['setor']}'"
    )
    # Alucinação de "fios de maquininha" deve ter sido eliminada dos pontos de melhoria
    for m in corrected["pontos_melhoria"]:
        assert "fio" not in m.lower() and "maquininha" not in m.lower(), (
            f"Alucinação não foi removida: {m}"
        )
    assert "Freezer e Geladeiras" in corrected["mensagem_whatsapp"]
    print("  [OK] Salvaguarda do parser corrigiu automaticamente 'BALCAO_CAIXA' para 'FREEZER_GELADEIRA' e expurgou alucinações.")


def test_score_and_validation_edge_cases():
    print("\n--- [Teste 3] Casos Extremos: Escala 0-100 e INVALID_OBJECT ---")

    raw_sample_100 = """{
      "elementos_visuais_detectados": ["estufa térmica de salgados"],
      "setor": "ESTUFA_SALGADOS",
      "conformidade": true,
      "score": 85.0,
      "pontos_positivos": ["Vidros limpos e salgados apetitosos"],
      "pontos_melhoria": ["Reabastecer coxinhas"],
      "mensagem_whatsapp": ""
    }"""
    parsed = gemini_service._parse_and_validate_response(raw_sample_100)
    assert parsed["score"] == 8.5
    assert parsed["conformidade"] is True
    assert "📸 *Setor: Estufa de Salgados" in parsed["mensagem_whatsapp"]
    assert "elementos_visuais_detectados" in parsed
    print("  [OK] Escala corrigida de 85.0 para 8.5 e mensagem_whatsapp regenerada.")

    raw_invalid = """{
      "elementos_visuais_detectados": ["documento impresso ilegível"],
      "setor": "INVALID_OBJECT",
      "conformidade": false,
      "score": 0.0,
      "pontos_positivos": [],
      "pontos_melhoria": ["A foto não retrata um setor operacional da conveniência."]
    }"""
    parsed_invalid = gemini_service._parse_and_validate_response(raw_invalid)
    assert parsed_invalid["setor"] == "INVALID_OBJECT"
    assert parsed_invalid["score"] == 0.0
    assert parsed_invalid["conformidade"] is False
    assert "❌" in parsed_invalid["mensagem_whatsapp"]
    assert parsed_invalid["elementos_visuais_detectados"] == ["documento impresso ilegível"]
    print("  [OK] Tratamento de INVALID_OBJECT com elementos visuais preservados.")


async def test_mock_analysis_flow():
    print("\n--- [Teste 4] Execução Simulada dos Setores (Mock Determinístico) ---")
    
    dummy_bytes_1 = b"0" * 4000
    dummy_bytes_2 = b"0" * 4010
    dummy_bytes_3 = b"0" * 4020
    dummy_bytes_4 = b"0" * 4030
    dummy_bytes_5 = b"0" * 4040

    res1 = await gemini_service.analyze_store_image(dummy_bytes_1)
    res2 = await gemini_service.analyze_store_image(dummy_bytes_2)
    res3 = await gemini_service.analyze_store_image(dummy_bytes_3)
    res4 = await gemini_service.analyze_store_image(dummy_bytes_4)
    res5 = await gemini_service.analyze_store_image(dummy_bytes_5)

    results = [res1, res2, res3, res4, res5]
    for r in results:
        assert "elementos_visuais_detectados" in r, "Todo resultado deve conter elementos_visuais_detectados"
        assert isinstance(r["elementos_visuais_detectados"], list)
        assert len(r["elementos_visuais_detectados"]) >= 1
        assert "setor" in r
        assert "conformidade" in r
        assert "score" in r
        assert 0.0 <= r["score"] <= 10.0
        assert "pontos_positivos" in r
        assert "pontos_melhoria" in r
        assert "mensagem_whatsapp" in r
        # NUNCA deve conter alucinações de fios e bobinas
        for m in r["pontos_melhoria"]:
            assert "bobina" not in m.lower(), f"Alucinação de bobina encontrada: {m}"
            assert "fio" not in m.lower(), f"Alucinação de fio encontrada: {m}"

    # Verificação do mock específico de FREEZER_GELADEIRA (selector 0)
    assert res1["setor"] == "FREEZER_GELADEIRA"
    assert res1["score"] == 8.4
    assert "cervejeira itaipava" in [e.lower() for e in res1["elementos_visuais_detectados"]]
    assert "cervejeira skol" in [e.lower() for e in res1["elementos_visuais_detectados"]]
    print("  [OK] Mock de FREEZER_GELADEIRA contém cervejeira Itaipava, Skol e nota 8.4.")
    print("  [OK] Mock de BALCAO_CAIXA não contém alucinações de fios e bobinas.")


if __name__ == "__main__":
    test_system_prompt_requirements()
    test_json_parsing_and_cot_safety_override()
    test_score_and_validation_edge_cases()
    asyncio.run(test_mock_analysis_flow())
    print("\n" + "=" * 65)
    print("[SUCESSO] TODOS OS TESTES DE CALIBRACAO E ANTI-ALUCINACAO PASSARAM!")
    print("=" * 65 + "\n")
