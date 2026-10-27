"""
Serviço avaliador determinístico para cálculo matemático de conformidade e notas
por checklist setorial em lojas de conveniência (Desacoplamento: Perito Visual vs Juiz Determinístico).
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from services.gemini_service import GeminiAuditoriaOutput, Severidade, StatusAnalise


class ItemCorrigivel(BaseModel):
    criterio: str
    descricao: str
    localizacao: str
    severidade: Severidade
    acao_sugerida: Optional[str] = None


class ResultadoAvaliacao(BaseModel):
    setor: str
    nota_calculada: float = Field(..., ge=0.0, le=10.0)
    status_analise: StatusAnalise
    total_criterios_avaliados: int
    criterios_conformes: int
    requer_revisao: bool
    requer_acao_imediata: bool
    itens_corrigiveis: List[ItemCorrigivel]
    resumo_operacional: str


# Matriz de pesos por setor (total = 10 por setor)
CHECKLIST_SETORES: Dict[str, Dict[str, int]] = {
    "FREEZER_GELADEIRA": {"organizacao": 3, "espaco_vazio": 3, "precificacao": 2, "limpeza": 2},
    "GONDOLA_GERAL": {"organizacao": 3, "espaco_vazio": 3, "precificacao": 2, "limpeza": 2},
    "ESTUFA": {"limpeza": 4, "apresentacao": 3, "organizacao": 3},
    "BANHEIRO": {"limpeza": 5, "conservacao": 3, "reposicao_insumos": 2},
    "VIDROS": {"limpeza": 7, "conservacao": 3},
    "CAIXA": {"organizacao": 4, "limpeza": 3, "impulso_visivel": 3},
}

# Sinônimos de setores legados para garantir compatibilidade
SETOR_ALIASES: Dict[str, str] = {
    "GONDOLA_PRATELEIRA": "GONDOLA_GERAL",
    "ESTUFA_SALGADOS": "ESTUFA",
    "BALCAO_CAIXA": "CAIXA",
}

PENALIDADE_SEVERIDADE = {
    Severidade.BAIXA: 0.5,
    Severidade.MEDIA: 1.0,
    Severidade.ALTA: 2.0,
}


def calcular_nota(analise: GeminiAuditoriaOutput) -> ResultadoAvaliacao:
    """
    Calcula matematicamente e deterministicamente a nota e conformidade do setor
    a partir das evidências visuais observadas pelo perito visual (Gemini).
    """
    # 1. Caso não verificável ou borrado
    if analise.status_analise == StatusAnalise.NAO_VERIFICAVEL:
        return ResultadoAvaliacao(
            setor=analise.setor_identificado,
            nota_calculada=0.0,
            status_analise=analise.status_analise,
            total_criterios_avaliados=0,
            criterios_conformes=0,
            requer_revisao=True,
            requer_acao_imediata=True,
            itens_corrigiveis=[],
            resumo_operacional="Foto ilegível ou fora de enquadramento. Reenvie nova foto do setor."
        )

    # Identificação da matriz de pesos com suporte a sinônimos
    setor_chave = SETOR_ALIASES.get(analise.setor_identificado, analise.setor_identificado)
    pesos = CHECKLIST_SETORES.get(setor_chave, CHECKLIST_SETORES.get(analise.setor_identificado, {"geral": 10}))
    total_peso_possivel = sum(pesos.values())
    pontos_obtidos = 0.0
    itens_corrigiveis: List[ItemCorrigivel] = []
    criterios_conformes = 0
    penalidade_extra = 0.0
    severidade_alta_presente = False

    # Mapear evidências observadas
    for ev in analise.evidencias:
        peso = pesos.get(ev.criterio, 2)  # default 2 se critério extra
        if ev.conforme:
            pontos_obtidos += peso
            criterios_conformes += 1
        else:
            sev = ev.severidade or Severidade.BAIXA
            if sev == Severidade.ALTA:
                severidade_alta_presente = True
            penalidade_extra += PENALIDADE_SEVERIDADE.get(sev, 0.5)
            itens_corrigiveis.append(ItemCorrigivel(
                criterio=ev.criterio,
                descricao=ev.descricao,
                localizacao=ev.localizacao,
                severidade=sev,
                acao_sugerida=analise.acoes_recomendadas[0] if analise.acoes_recomendadas else None
            ))

    total_avaliados = len(analise.evidencias)
    if total_avaliados > 0:
        base_score = (pontos_obtidos / max(total_peso_possivel, 1)) * 10.0
        nota_final = max(0.0, min(10.0, base_score - penalidade_extra))
    else:
        nota_final = 5.0

    nota_final = round(nota_final, 1)
    requer_acao = severidade_alta_presente or (nota_final < 7.0)

    resumo = (
        f"Setor {analise.setor_identificado}: {criterios_conformes}/{total_avaliados} conformes. "
        f"{len(itens_corrigiveis)} apontamento(s)."
    )

    return ResultadoAvaliacao(
        setor=analise.setor_identificado,
        nota_calculada=nota_final,
        status_analise=analise.status_analise,
        total_criterios_avaliados=total_avaliados,
        criterios_conformes=criterios_conformes,
        requer_revisao=analise.requer_revisao or (analise.nivel_confianca < 0.6),
        requer_acao_imediata=requer_acao,
        itens_corrigiveis=itens_corrigiveis,
        resumo_operacional=resumo
    )


class EvaluatorService:
    """Classe de serviço para avaliações determinísticas e formatação de respostas."""

    @staticmethod
    def avaliar(analise: GeminiAuditoriaOutput) -> ResultadoAvaliacao:
        return calcular_nota(analise)

    @staticmethod
    def gerar_mensagem_whatsapp(
        resultado: ResultadoAvaliacao,
        analise: GeminiAuditoriaOutput,
        pontos_positivos: Optional[List[str]] = None
    ) -> str:
        """Gera mensagem para WhatsApp baseada na avaliação determinística."""
        display_name = resultado.setor.replace("_", " ").title()
        if resultado.status_analise == StatusAnalise.NAO_VERIFICAVEL:
            return (
                f"❌ *Foto Não Identificada - Setor: {display_name}*\n"
                f"{resultado.resumo_operacional}"
            )

        lines = [f"📸 *Setor: {display_name}*"]
        if pontos_positivos and len(pontos_positivos) > 0:
            lines.append(f"👏 {pontos_positivos[0]}")
        else:
            lines.append(f"👏 Equipe empenhada no padrão operacional do setor!")

        lines.append("")
        if resultado.itens_corrigiveis:
            lines.append("💡 *Oportunidades de Melhoria (Checklist):*")
            for item in resultado.itens_corrigiveis[:3]:
                sev_icon = "⚠️" if item.severidade == Severidade.ALTA else "•"
                lines.append(f"{sev_icon} [{item.criterio.capitalize()}] {item.descricao} ({item.localizacao})")
        else:
            lines.append("💡 *Oportunidades de Melhoria:*")
            lines.append("• Manter o alinhamento e abastecimento durante o pico!")

        lines.append("")
        if resultado.nota_calculada >= 9.0:
            frase = "Excelente trabalho! Padrão impecável!"
        elif resultado.nota_calculada >= 7.5:
            frase = "Muito bom! Pequenos ajustes e o setor fica nota 10!"
        elif resultado.nota_calculada >= 6.0:
            frase = "Atenção: necessário ajustar os itens apontados."
        else:
            frase = "Alerta Operacional: Requer intervenção imediata da equipe!"

        lines.append(f"⭐ *Nota Calculada:* {resultado.nota_calculada}/10 - {frase}")
        return "\n".join(lines)


evaluator_service = EvaluatorService()
