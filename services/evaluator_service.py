"""
Serviço avaliador determinístico para cálculo matemático de conformidade e notas
por checklist setorial em lojas de conveniência (Desacoplamento: Perito Visual vs Juiz Determinístico).
"""

import json
from datetime import date
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.gemini_service import GeminiAuditoriaOutput, Severidade, StatusAnalise
from database import Audit, AuditTicket, add_points_to_operator, async_session_factory


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


async def avaliar_consistencia_turno(
    store_id: Any,
    turno: str,
    data: date,
    session: Optional[AsyncSession] = None,
) -> Dict[str, Any]:
    """
    Avalia o fechamento de turno para a concessão do Bônus de Consistência (Loja Padrão 10).
    Critérios:
    - Mínimo de 4 setores distintos auditados no turno.
    - Todos os setores auditados devem ter obtido nota_calculada >= 9.0.
    - Zero tickets de pendência não resolvidos (sem tickets ABERTO ou EXPIRADO).
    Premiação:
    - Credita +50 pontos de Loja Padrão 10 divididos igualmente entre os operadores ativos do turno.
    """
    int_store_id = int(store_id)
    sh_norm = str(turno).strip().upper()
    if sh_norm in ("MANHA", "T1"):
        sh_norm = "T1_MANHA"
    elif sh_norm in ("TARDE", "T2"):
        sh_norm = "T2_TARDE"
    elif sh_norm in ("NOITE", "T3"):
        sh_norm = "T3_NOITE"

    async def _evaluate(s: AsyncSession) -> Dict[str, Any]:
        # 1. Busca auditorias do turno e data
        stmt_audits = (
            select(Audit)
            .where(
                Audit.store_id == int_store_id,
                Audit.date == data,
                Audit.shift == sh_norm,
            )
        )
        res_audits = await s.execute(stmt_audits)
        audits = res_audits.scalars().all()

        if not audits:
            return {
                "elegivel": False,
                "loja_padrao_10": False,
                "motivo": "Nenhuma auditoria registrada neste turno",
                "total_setores": 0,
                "setores": [],
                "tickets_nao_resolvidos": 0,
                "operadores": [],
                "pontos_totais": 0.0,
            }

        # Mapeia notas por setor e operadores ativos
        setores_scores: Dict[str, List[float]] = {}
        active_operators: Set[str] = set()

        for a in audits:
            if a.operator_name:
                active_operators.add(a.operator_name.strip())

            # Extrai o setor e a nota calculada
            setor_audit = None
            nota = a.score if a.score is not None else 0.0

            if a.ai_analysis:
                try:
                    js = json.loads(a.ai_analysis) if isinstance(a.ai_analysis, str) else a.ai_analysis
                    setor_audit = js.get("setor") or (js.get("avaliacao") or {}).get("setor")
                    if js.get("avaliacao") and "nota_calculada" in js["avaliacao"]:
                        nota = js["avaliacao"]["nota_calculada"]
                except Exception:
                    pass

            if not setor_audit:
                setor_audit = f"SETOR_AUDIT_{a.id}"

            if setor_audit not in setores_scores:
                setores_scores[setor_audit] = []
            setores_scores[setor_audit].append(nota)

        # 2. Verifica se atingiu mínimo de 4 setores
        total_setores = len(setores_scores)
        if total_setores < 4:
            return {
                "elegivel": False,
                "loja_padrao_10": False,
                "motivo": f"Mínimo de 4 setores exigidos, mas apenas {total_setores} foram auditados",
                "total_setores": total_setores,
                "setores": list(setores_scores.keys()),
                "tickets_nao_resolvidos": 0,
                "operadores": list(active_operators),
                "pontos_totais": 0.0,
            }

        # 3. Verifica se todos os setores auditados obtiveram nota >= 9.0
        setores_reprovados = []
        for st_name, notas in setores_scores.items():
            if max(notas) < 9.0:
                setores_reprovados.append((st_name, max(notas)))

        if setores_reprovados:
            reprovados_str = ", ".join([f"{st}: {n}" for st, n in setores_reprovados])
            return {
                "elegivel": False,
                "loja_padrao_10": False,
                "motivo": f"Nem todos os setores obtiveram nota >= 9.0 ({reprovados_str})",
                "total_setores": total_setores,
                "setores": list(setores_scores.keys()),
                "setores_reprovados": setores_reprovados,
                "tickets_nao_resolvidos": 0,
                "operadores": list(active_operators),
                "pontos_totais": 0.0,
            }

        # 4. Verifica tickets pendentes não resolvidos (ABERTO ou EXPIRADO)
        audit_ids = [a.id for a in audits]
        stmt_tickets = (
            select(AuditTicket)
            .where(
                AuditTicket.store_id == int_store_id,
                AuditTicket.status.in_(["ABERTO", "EXPIRADO"]),
            )
        )
        res_tickets = await s.execute(stmt_tickets)
        tickets_pendentes = res_tickets.scalars().all()
        # Filtra tickets que pertencem a este turno/data ou às auditorias do turno
        tickets_turno = [
            t for t in tickets_pendentes
            if t.audit_origem_id in audit_ids
            or (t.shift and t.shift.upper() == sh_norm and t.created_at.date() == data)
        ]

        if tickets_turno:
            return {
                "elegivel": False,
                "loja_padrao_10": False,
                "motivo": f"Existem {len(tickets_turno)} ticket(s) de pendência não resolvidos no turno",
                "total_setores": total_setores,
                "setores": list(setores_scores.keys()),
                "tickets_nao_resolvidos": len(tickets_turno),
                "operadores": list(active_operators),
                "pontos_totais": 0.0,
            }

        # 5. Elegível para o Bônus de Loja Padrão 10 (+50 pts)
        ops_list = sorted(list(active_operators))
        pontos_por_op = round(50.0 / len(ops_list), 1) if ops_list else 50.0

        for op_name in ops_list:
            await add_points_to_operator(s, int_store_id, op_name, pontos_por_op)

        return {
            "elegivel": True,
            "loja_padrao_10": True,
            "store_id": int_store_id,
            "turno": sh_norm,
            "data": str(data),
            "total_setores": total_setores,
            "setores": list(setores_scores.keys()),
            "tickets_nao_resolvidos": 0,
            "pontos_totais": 50.0,
            "pontos_por_operador": pontos_por_op,
            "operadores": ops_list,
            "mensagem": (
                f"🏆 Bônus de Consistência (Loja Padrão 10) concedido! "
                f"+50 pontos distribuídos entre: {', '.join(ops_list)}."
            ),
        }

    if session:
        return await _evaluate(session)
    else:
        async with async_session_factory() as sess:
            return await _evaluate(sess)


EvaluatorService.avaliar_consistencia_turno = staticmethod(avaliar_consistencia_turno)
evaluator_service = EvaluatorService()
