"""
Suíte de testes unitários para services/evaluator_service.py.
Valida o desacoplamento determinístico (Perito Visual vs Juiz):
1. Caso NAO_VERIFICAVEL -> nota 0.0, requer revisão e ação imediata.
2. Cálculo ponderado de conformidade por checklist setorial (FREEZER_GELADEIRA, ESTUFA, etc.).
3. Aplicação de penalidades por severidade (BAIXA: -0.5, MEDIA: -1.0, ALTA: -2.0).
4. Flag requer_acao_imediata acionada por severidade ALTA ou nota < 7.0.
5. Suporte a sinônimos de setores legados (ex: GONDOLA_PRATELEIRA -> GONDOLA_GERAL).
"""

import pytest
from services.gemini_service import (
    EvidenciaVisual,
    GeminiAuditoriaOutput,
    Severidade,
    StatusAnalise,
)
from services.evaluator_service import (
    CHECKLIST_SETORES,
    ItemCorrigivel,
    ResultadoAvaliacao,
    calcular_nota,
    evaluator_service,
)


def test_caso_nao_verificavel():
    print("\n--- [Teste 1] Caso Não Verificável (Imagem Ilegível/Borrada) ---")
    analise = GeminiAuditoriaOutput(
        setor_identificado="INVALID_OBJECT",
        status_analise=StatusAnalise.NAO_VERIFICAVEL,
        nivel_confianca=0.2,
        requer_revisao=True,
        elementos_visuais_detectados=[],
        evidencias=[],
        acoes_recomendadas=["Reenviar foto nítida"],
    )

    resultado = calcular_nota(analise)
    assert resultado.nota_calculada == 0.0
    assert resultado.status_analise == StatusAnalise.NAO_VERIFICAVEL
    assert resultado.requer_revisao is True
    assert resultado.requer_acao_imediata is True
    assert len(resultado.itens_corrigiveis) == 0
    print("  [OK] Imagem não verificável tratada com nota 0.0 e ação imediata.")


def test_freezer_geladeira_100_porcento_conforme():
    print("\n--- [Teste 2] Freezer/Geladeira 100% Conforme ---")
    # Pesos: organizacao=3, espaco_vazio=3, precificacao=2, limpeza=2 (total = 10)
    analise = GeminiAuditoriaOutput(
        setor_identificado="FREEZER_GELADEIRA",
        status_analise=StatusAnalise.CONCLUIDA,
        nivel_confianca=0.95,
        requer_revisao=False,
        elementos_visuais_detectados=["cervejeira itaipava", "cervejeira skol", "latas de cerveja"],
        evidencias=[
            EvidenciaVisual(criterio="organizacao", descricao="Latas frenteadas", localizacao="todas as grades", conforme=True),
            EvidenciaVisual(criterio="espaco_vazio", descricao="Sem rupturas", localizacao="prateleiras", conforme=True),
            EvidenciaVisual(criterio="precificacao", descricao="Preços legíveis", localizacao="canaletas frontais", conforme=True),
            EvidenciaVisual(criterio="limpeza", descricao="Vidros limpos e secos", localizacao="portas de vidro", conforme=True),
        ],
        acoes_recomendadas=[],
    )

    resultado = calcular_nota(analise)
    assert resultado.nota_calculada == 10.0
    assert resultado.criterios_conformes == 4
    assert resultado.total_criterios_avaliados == 4
    assert resultado.requer_acao_imediata is False
    assert len(resultado.itens_corrigiveis) == 0
    print("  [OK] 100% conforme no checklist atingiu nota máxima 10.0 sem ação imediata.")


def test_freezer_geladeira_com_penalidade_media():
    print("\n--- [Teste 3] Freezer com Inconformidade Média (Espaço Vazio) ---")
    # organizacao=3 (ok), limpeza=2 (ok), precificacao=2 (ok) -> 7 pontos
    # espaco_vazio=3 (falha, MEDIA -> penalidade 1.0)
    # base_score = (7 / 10) * 10 = 7.0
    # final = 7.0 - 1.0 = 6.0
    analise = GeminiAuditoriaOutput(
        setor_identificado="FREEZER_GELADEIRA",
        status_analise=StatusAnalise.CONCLUIDA,
        nivel_confianca=0.90,
        requer_revisao=False,
        elementos_visuais_detectados=["cervejeira vertical", "refrigerantes pet"],
        evidencias=[
            EvidenciaVisual(criterio="organizacao", descricao="Garrafas em pé", localizacao="grade 1", conforme=True),
            EvidenciaVisual(criterio="limpeza", descricao="Vidro limpo", localizacao="porta", conforme=True),
            EvidenciaVisual(criterio="precificacao", descricao="Etiquetas no lugar", localizacao="grade 2", conforme=True),
            EvidenciaVisual(
                criterio="espaco_vazio",
                descricao="Buraco sem garrafas",
                localizacao="grade 3 lado direito",
                conforme=False,
                severidade=Severidade.MEDIA,
            ),
        ],
        acoes_recomendadas=["Repor refrigerantes na grade 3"],
    )

    resultado = calcular_nota(analise)
    assert resultado.nota_calculada == 6.0
    assert resultado.criterios_conformes == 3
    assert resultado.total_criterios_avaliados == 4
    assert resultado.requer_acao_imediata is True  # nota < 7.0
    assert len(resultado.itens_corrigiveis) == 1
    assert resultado.itens_corrigiveis[0].severidade == Severidade.MEDIA
    print("  [OK] Penalidade média calculada deterministicamente: 7.0 base - 1.0 = 6.0.")


def test_severidade_alta_aciona_acao_imediata_mesmo_com_nota_alta():
    print("\n--- [Teste 4] Severidade Alta aciona Requer Ação Imediata ---")
    # Suponha estufa: limpeza=4, apresentacao=3, organizacao=3 (total 10)
    # apresentacao=3 (ok), organizacao=3 (ok) -> 6 pontos
    # limpeza=4 (falha severidade ALTA -> penalidade 2.0)
    # base = (6 / 10) * 10 = 6.0; nota = 6.0 - 2.0 = 4.0
    analise = GeminiAuditoriaOutput(
        setor_identificado="ESTUFA",
        status_analise=StatusAnalise.CONCLUIDA,
        nivel_confianca=0.92,
        requer_revisao=False,
        elementos_visuais_detectados=["estufa de salgados", "coxinhas", "folhados"],
        evidencias=[
            EvidenciaVisual(criterio="apresentacao", descricao="Salgados frescos", localizacao="bandeja superior", conforme=True),
            EvidenciaVisual(criterio="organizacao", descricao="Bandejas alinhadas", localizacao="bandeja inferior", conforme=True),
            EvidenciaVisual(
                criterio="limpeza",
                descricao="Vidro com gordura excessiva e resíduos",
                localizacao="vidro frontal",
                conforme=False,
                severidade=Severidade.ALTA,
            ),
        ],
        acoes_recomendadas=["Limpar vidro frontal da estufa imediatamente"],
    )

    resultado = calcular_nota(analise)
    assert resultado.nota_calculada == 4.0
    assert resultado.requer_acao_imediata is True
    assert resultado.itens_corrigiveis[0].severidade == Severidade.ALTA
    print("  [OK] Inconformidade com severidade ALTA acionou requer_acao_imediata.")


def test_sinonimo_setor_legado():
    print("\n--- [Teste 5] Sinônimo de Setor Legado (GONDOLA_PRATELEIRA -> GONDOLA_GERAL) ---")
    analise = GeminiAuditoriaOutput(
        setor_identificado="GONDOLA_PRATELEIRA",
        status_analise=StatusAnalise.CONCLUIDA,
        nivel_confianca=0.88,
        requer_revisao=False,
        elementos_visuais_detectados=["gôndola", "snacks"],
        evidencias=[
            EvidenciaVisual(criterio="organizacao", descricao="Snacks alinhados", localizacao="prateleira 1", conforme=True),
            EvidenciaVisual(criterio="espaco_vazio", descricao="Sem rupturas", localizacao="prateleira 2", conforme=True),
            EvidenciaVisual(criterio="precificacao", descricao="Preços ok", localizacao="frente", conforme=True),
            EvidenciaVisual(criterio="limpeza", descricao="Prateleiras limpas", localizacao="base", conforme=True),
        ],
        acoes_recomendadas=[],
    )

    resultado = calcular_nota(analise)
    assert resultado.nota_calculada == 10.0
    print("  [OK] Setor legado GONDOLA_PRATELEIRA mapeado para matriz GONDOLA_GERAL.")


if __name__ == "__main__":
    test_caso_nao_verificavel()
    test_freezer_geladeira_100_porcento_conforme()
    test_freezer_geladeira_com_penalidade_media()
    test_severidade_alta_aciona_acao_imediata_mesmo_com_nota_alta()
    test_sinonimo_setor_legado()
    print("\n" + "=" * 65)
    print("[SUCESSO] TODOS OS TESTES DO EVALUATOR SERVICE PASSARAM!")
    print("=" * 65 + "\n")
