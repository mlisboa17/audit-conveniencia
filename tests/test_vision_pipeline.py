"""
Suíte de Testes Automatizados para o Pipeline de Visão Blindada em 2 Etapas:
Gatekeeper de Sanidade + Perito Especialista do Setor (com Evaluator Service Determinístico).

Cobre:
1. test_gatekeeper_rejeita_imagem_fora_de_contexto:
   Foto inválida (meme, print, comprovante) gera StatusAnalise.NAO_VERIFICAVEL e requer_revisao = True sem chamar o perito.
2. test_gatekeeper_rejeita_qualidade_escura_ou_borrada:
   Fotos com iluminação inadequada ou borradas são descartadas com qualidade_imagem_ok = False.
3. test_perito_setorial_freezer_aplica_regras_estritas:
   Foto de freezer não gera critérios de estufa ou banheiro e elimina alucinações de fios/bobinas.
4. test_perito_setorial_outros_setores_regras_estritas:
   Valida que banheiro nunca pontua precificação/estoque e estufa não possui critérios de insumos de banheiro.
5. test_pipeline_integrado_foto_valida:
   Foto de conveniência passa pelas duas etapas desacopladas e entrega resultado consistente ao avaliador.
6. test_gatekeeper_prioridade_inegociavel_freezer:
   Regra inegociável garante classificação como FREEZER_GELADEIRA na presença de cervejeiras/bebidas.
"""

import pytest
from services.gemini_service import (
    GatekeeperOutput,
    GeminiAuditoriaOutput,
    Severidade,
    StatusAnalise,
    analisar_foto,
    gemini_service,
    validar_foto_conveniencia,
)
from services.evaluator_service import calcular_nota


def test_gatekeeper_rejeita_imagem_fora_de_contexto():
    """
    1. test_gatekeeper_rejeita_imagem_fora_de_contexto:
    Foto inválida gera StatusAnalise.NAO_VERIFICAVEL e requer_revisao = True sem chamar o perito.
    """
    print("\n--- [Teste 1] Gatekeeper Rejeita Imagem Fora de Contexto (Meme/Comprovante) ---")
    
    # 1.1 Triagem direta pelo Gatekeeper
    foto_invalida_bytes = b"meme_engracado_de_gato_fora_de_contexto"
    gatekeeper: GatekeeperOutput = gemini_service.validar_foto_conveniencia(foto_invalida_bytes)
    
    assert gatekeeper.is_conveniencia_valida is False
    assert gatekeeper.motivo_rejeicao is not None
    assert "conveniência" in gatekeeper.motivo_rejeicao.lower() or "fora de contexto" in gatekeeper.motivo_rejeicao.lower()
    print(f"  [OK] Gatekeeper identificou imagem fora de contexto: motivo='{gatekeeper.motivo_rejeicao}'")

    # 1.2 Pipeline integrado analisar_foto
    output: GeminiAuditoriaOutput = gemini_service.analisar_foto(foto_invalida_bytes)
    
    assert output.status_analise == StatusAnalise.NAO_VERIFICAVEL
    assert output.requer_revisao is True
    assert len(output.elementos_visuais_detectados) == 0
    assert len(output.evidencias) == 0, "Perito setorial NÃO deve ser chamado nem produzir evidências para foto rejeitada"
    assert len(output.acoes_recomendadas) > 0
    assert gatekeeper.motivo_rejeicao in output.acoes_recomendadas[0]
    
    # 1.3 Avaliação determinística encaminhada
    assert output.resultado_avaliacao is not None
    assert output.resultado_avaliacao.status_analise == StatusAnalise.NAO_VERIFICAVEL
    assert output.resultado_avaliacao.nota_calculada == 0.0
    assert output.resultado_avaliacao.requer_revisao is True
    print("  [OK] Pipeline interrompido na Etapa 1 com StatusAnalise.NAO_VERIFICAVEL e nota 0.0.")


def test_gatekeeper_rejeita_qualidade_escura_ou_borrada():
    """
    2. test_gatekeeper_rejeita_qualidade_escura_ou_borrada:
    Fotos com qualidade degradada (escuras ou borradas) são rejeitadas com qualidade_imagem_ok = False.
    """
    print("\n--- [Teste 2] Gatekeeper Rejeita Fotos Escuras ou Borradas ---")
    
    # 2.1 Foto escura
    foto_escura = b"foto_preta_muito_escura_dark_sem_iluminacao"
    gk_dark = gemini_service.validar_foto_conveniencia(foto_escura)
    assert gk_dark.qualidade_imagem_ok is False
    assert "escura" in gk_dark.motivo_rejeicao.lower() or "iluminação" in gk_dark.motivo_rejeicao.lower()
    
    out_dark = gemini_service.analisar_foto(foto_escura)
    assert out_dark.status_analise == StatusAnalise.NAO_VERIFICAVEL
    assert out_dark.requer_revisao is True
    assert len(out_dark.evidencias) == 0
    print("  [OK] Foto escura rejeitada com qualidade_imagem_ok = False.")

    # 2.2 Foto borrada
    foto_borrada = b"foto_excessivamente_borrada_blur_fora_de_foco"
    gk_blur = gemini_service.validar_foto_conveniencia(foto_borrada)
    assert gk_blur.qualidade_imagem_ok is False
    assert "borrada" in gk_blur.motivo_rejeicao.lower() or "foco" in gk_blur.motivo_rejeicao.lower()
    
    out_blur = gemini_service.analisar_foto(foto_borrada)
    assert out_blur.status_analise == StatusAnalise.NAO_VERIFICAVEL
    assert out_blur.requer_revisao is True
    assert len(out_blur.evidencias) == 0
    print("  [OK] Foto borrada rejeitada com qualidade_imagem_ok = False.")


def test_perito_setorial_freezer_aplica_regras_estritas():
    """
    3. test_perito_setorial_freezer_aplica_regras_estritas:
    Valida que foto de freezer não gera critérios de estufa ou banheiro e elimina alucinações.
    """
    print("\n--- [Teste 3] Perito Setorial de Freezer Aplica Regras Estritas ---")
    
    foto_freezer = b"foto_legitima_cervejeira_geladeira_vertical_bebidas_skol"
    output = gemini_service.analisar_foto(foto_freezer)
    
    assert output.setor_identificado == "FREEZER_GELADEIRA"
    assert output.status_analise == StatusAnalise.CONCLUIDA
    assert output.requer_revisao is False
    assert len(output.elementos_visuais_detectados) > 0
    assert len(output.evidencias) > 0

    criterios_permitidos_freezer = {"organizacao", "espaco_vazio", "precificacao", "limpeza"}
    
    for ev in output.evidencias:
        # Validação 1: Critério deve ser do escopo de freezer
        assert ev.criterio in criterios_permitidos_freezer, f"Critério inesperado no freezer: {ev.criterio}"
        
        # Validação 2: NUNCA critérios de banheiro (reposicao_insumos, conservacao)
        assert ev.criterio != "reposicao_insumos", "Freezer nunca deve pontuar reposição de insumos de banheiro!"
        
        # Validação 3: NUNCA critérios de estufa (apresentacao)
        assert ev.criterio != "apresentacao", "Freezer nunca deve pontuar critérios térmicos de estufa!"

        # Validação 4: NUNCA impulso de checkout
        assert ev.criterio != "impulso_visivel", "Freezer nunca deve pontuar impulso de caixa!"

        # Validação 5: NUNCA alucinação de fios, bobinas ou maquininhas
        desc_lower = ev.descricao.lower()
        for termo in ["fio", "bobina", "maquininha", "pinpad"]:
            assert termo not in desc_lower, f"Alucinação detectada no freezer: '{termo}' na descrição '{ev.descricao}'"

    print(f"  [OK] Laudo do Freezer validado com {len(output.evidencias)} evidências estritas e sem alucinações.")


def test_perito_setorial_outros_setores_regras_estritas():
    """
    4. test_perito_setorial_outros_setores_regras_estritas:
    Valida isolamento setorial: Banheiro não pontua preço/mercadoria e Estufa não pontua insumos de higiene.
    """
    print("\n--- [Teste 4] Isolamento Setorial Rígido (Banheiro e Estufa) ---")
    
    # 4.1 Banheiro
    foto_banheiro = b"foto_sanitario_banheiro_com_pia_e_espelho"
    out_banheiro = gemini_service.analisar_foto(foto_banheiro)
    assert out_banheiro.setor_identificado == "BANHEIRO"
    for ev in out_banheiro.evidencias:
        assert ev.criterio not in {"precificacao", "espaco_vazio", "impulso_visivel"}, (
            f"Critério proibido no banheiro: {ev.criterio}"
        )
    print("  [OK] Banheiro analisado sem precificação, espaço vazio ou itens de impulso.")

    # 4.2 Estufa de Salgados
    foto_estufa = b"foto_estufa_termica_de_salgados_e_lanches"
    out_estufa = gemini_service.analisar_foto(foto_estufa)
    assert out_estufa.setor_identificado == "ESTUFA"
    for ev in out_estufa.evidencias:
        assert ev.criterio not in {"reposicao_insumos", "precificacao"}, (
            f"Critério proibido na estufa: {ev.criterio}"
        )
    print("  [OK] Estufa analisada com foco em vidro, apresentação e organização de bandejas.")


@pytest.mark.asyncio
async def test_pipeline_integrado_foto_valida():
    """
    5. test_pipeline_integrado_foto_valida:
    Foto de conveniência passa pelas duas etapas e entrega resultado consistente ao avaliador.
    """
    print("\n--- [Teste 5] Pipeline Integrado com Foto Válida ---")
    
    foto_valida = b"foto_conveniencia_cervejeira_vertical_completa_4000_bytes_de_qualidade"
    
    # Chamada via analisar_foto
    gemini_output: GeminiAuditoriaOutput = gemini_service.analisar_foto(foto_valida)
    assert gemini_output.status_analise == StatusAnalise.CONCLUIDA
    assert gemini_output.setor_identificado == "FREEZER_GELADEIRA"
    assert len(gemini_output.evidencias) > 0
    
    # Avaliador determinístico
    resultado_avaliacao = gemini_output.resultado_avaliacao
    assert resultado_avaliacao is not None
    assert resultado_avaliacao.nota_calculada >= 0.0
    assert resultado_avaliacao.nota_calculada <= 10.0
    assert resultado_avaliacao.total_criterios_avaliados == len(gemini_output.evidencias)
    assert resultado_avaliacao.status_analise == StatusAnalise.CONCLUIDA
    print(f"  [OK] Nota calculada deterministicamente: {resultado_avaliacao.nota_calculada}/10.0")

    # Chamada integrada via analyze_store_image (compatibilidade com webhook e PWA)
    resultado_dict = await gemini_service.analyze_store_image(foto_valida)
    assert resultado_dict["setor"] == "FREEZER_GELADEIRA"
    assert "score" in resultado_dict
    assert "mensagem_whatsapp" in resultado_dict
    assert "avaliacao" in resultado_dict
    assert "gemini_output" in resultado_dict
    assert resultado_dict["status"] in ("OK", "ALERT")
    print(f"  [OK] analyze_store_image integrado retornou score {resultado_dict['score']}.")


def test_gatekeeper_prioridade_inegociavel_freezer():
    """
    6. test_gatekeeper_prioridade_inegociavel_freezer:
    Regra inegociável garante que presença de cervejeira/refrigerador nunca seja classificada como Caixa.
    """
    print("\n--- [Teste 6] Regra Inegociável: Prioridade Absoluta para FREEZER_GELADEIRA ---")
    
    foto_mista = b"foto_com_cervejeira_itaipava_e_balcao_caixa_ao_lado"
    gk = gemini_service.validar_foto_conveniencia(foto_mista)
    assert gk.setor_detectado == "FREEZER_GELADEIRA"
    print("  [OK] Prioridade inegociável confirmou FREEZER_GELADEIRA.")
