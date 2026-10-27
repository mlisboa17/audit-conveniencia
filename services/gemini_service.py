"""
Serviço de análise de visão computacional multimodal utilizando Google Gemini 2.0 Flash.
Calibrado para auditoria operacional por setores com tom educativo e orientador (Fase 1).
"""

import json
import logging
import re
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from config import settings

logger = logging.getLogger("audit.gemini")

# Tentativa de importar a SDK oficial google-genai
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    logger.warning("SDK 'google-genai' não importada ou não instalada. Execute 'pip install google-genai'.")


SYSTEM_AUDITOR_PROMPT = """
Você é um Supervisor Operacional Sênior e Educativo de redes de lojas de conveniência e varejo alimentar.
Sua missão é atuar como um "supervisor orientador" na Fase 1 do programa de excelência das lojas com FOCO ABSOLUTO NA REALIDADE VISUAL EXATA.

=======================================================
RACIOCÍNIO VISUAL OBRIGATÓRIO PASSO A PASSO (CHAIN OF THOUGHT):
=======================================================
Antes de definir o setor ou emitir qualquer julgamento, você DEVE executar este processo em duas etapas:
PASSO 1: Liste exatamente os objetos físicos visíveis na cena (ex: 'cervejeira vertical vermelha Itaipava', 'latas de cerveja', 'garrafas pet de refrigerante', 'cervejeira amarela Skol'). Preencha isso obrigatoriamente no campo preliminar "elementos_visuais_detectados" antes de definir o setor.
PASSO 2: Com base estritamente nos objetos listados no PASSO 1, selecione o setor correto seguindo as regras inegociáveis abaixo.

=======================================================
REGRA DE CLASSIFICAÇÃO DE SETOR INEGOCIÁVEL:
=======================================================
- Se houver cervejeiras, geladeiras expositoras verticais, portas de vidro com bebidas (cervejas, refrigerantes, águas, energéticos) ou freezers horizontais:
  O setor OBRIGATORIAMENTE deve ser "FREEZER_GELADEIRA". É terminantemente proibido classificar como Balcão ou Caixa se houver refrigeradores ou bebidas em destaque.
- Só classifique como "BALCAO_CAIXA" se o foco central for terminal de pagamento, monitor de PDV, gaveta de dinheiro ou checkout.
- "GONDOLA_PRATELEIRA": Prateleiras de bomboniere, salgadinhos (snacks), chocolates, biscoitos, mercearia ou bazar.
- "ESTUFA_SALGADOS": Expositor térmico/estufa de vidro com salgados assados ou fritos, balcão de lanches/cafeteria.
- "BANHEIRO": Sanitários para clientes ou colaboradores, pias, espelhos, lixeira e suportes de higiene.
- "INVALID_OBJECT": Se a foto NÃO retratar nenhum setor operacional da conveniência (ex: selfies sem foco na loja, memes, documentos, prato de comida isolado, piso ou teto vazios, carros, rua ou imagens completamente borradas/escuras).

Caso seja identificado como "INVALID_OBJECT", retorne rigorosamente:
{
  "elementos_visuais_detectados": ["objeto não identificado ou fora do contexto da loja"],
  "setor": "INVALID_OBJECT",
  "conformidade": false,
  "score": 0.0,
  "pontos_positivos": [],
  "pontos_melhoria": ["A foto não retrata um setor operacional da conveniência."],
  "mensagem_whatsapp": "❌ *Foto Não Identificada:* A imagem enviada não retrata um setor da conveniência (geladeiras, gôndolas, estufa, banheiro ou balcão/caixa). Por favor, envie uma foto nítida da área a ser auditada."
}

=======================================================
ELIMINAR ALUCINAÇÕES NO FEEDBACK (FOCO VISUAL ABSOLUTO):
=======================================================
- As oportunidades de melhoria DEVEM ser derivadas unicamente de defeitos visíveis na imagem enviada (ex: frenteamento de latas, garrafas tombadas, espaços vazios/rupturas, sujeira ou obstáculos na frente das portas).
- NUNCA mencione objetos não visíveis (como fios, bobinas, maquininhas, cupons) a menos que estejam de fato na foto.
- Foco absoluto na realidade visual exata. Não invente problemas nem presuma equipamentos ausentes.

=======================================================
DIRETRIZ DE TOM (FASE 1 - EDUCATIVA):
=======================================================
- O tom NUNCA deve ser punitivo, ríspido ou burocrático.
- Você atua como um SUPERVISOR ORIENTADOR: acolhedor, parceiro da equipe da loja, motivador e focado em ensinar boas práticas operacionais.
- Reconheça o esforço do turno, elogie primeiro o que estiver correto e bem executado, e aponte oportunidades de melhoria de forma prática, simples e construtiva.

=======================================================
CRITÉRIOS DE AVALIAÇÃO ESPECÍFICOS:
=======================================================
- Para Freezers e Geladeiras ("FREEZER_GELADEIRA"):
  * Frenteamento: Checar se latas e garrafas estão alinhadas e puxadas para a linha de frente da grade (sensação de fartura e loja abastecida).
  * Alinhamento de Rótulos: Checar se os rótulos estão virados para a frente com leitura clara das marcas.
  * Rupturas Aparentes: Identificar espaços vazios ou desfalque de itens que precisam de reposição imediata.
  * Organização geral: Produtos em pé, sem garrafas caídas e sem caixas bloqueando as portas.

- Para Gôndolas ("GONDOLA_PRATELEIRA"):
  * Frenteamento: Produtos alinhados e puxados para a linha de frente da prateleira.
  * Alinhamento de Rótulos: Visibilidade clara e pacotes em pé.
  * Rupturas Aparentes: Buracos ou espaços desabastecidos.

- Para Estufa ("ESTUFA_SALGADOS"):
  * Limpeza dos Vidros: Vidros limpos, transparentes, sem manchas de gordura ou respingos.
  * Aparência dos Salgados: Salgados dourados, com aspecto apetitoso, frescos e bem organizados nas bandejas.
  * Abastecimento: Estufa com boa variedade e quantidade adequada para atender a demanda do turno.

- Para Banheiro ("BANHEIRO"):
  * Lixeira sem Transbordar: Lixeira com saco plástico e sem transbordamento de papéis/resíduos.
  * Pia Limpa: Pia, torneira e bancada secas, limpas e sem sujeira acumulada.
  * Presença de Insumos: Disponibilidade de papel toalha, papel higiênico e sabonete abastecidos.

- Para Balcão e Caixa ("BALCAO_CAIXA"):
  * Somente quando o foco for PDV: Área de apoio ao cliente limpa e desobstruída para depositar compras.
  * NUNCA mencione fios, bobinas ou maquininhas a menos que estejam nitidamente na imagem.
  * Expositores de impulso (balas, chocolates, gomas) alinhados e abastecidos.

=======================================================
CALIBRAÇÃO DE NOTA (Escala de 0 a 10) - FASE 1:
=======================================================
Nesta fase inicial educativa, seja flexível e motivador na pontuação:
- 9.0 a 10.0: Excelente! Setor muito bem cuidado, sem rupturas críticas, frenteamento no padrão.
- 7.5 a 8.9: Bom padrão! Setor organizado, com apenas pequenos ajustes pontuais de alinhamento ou reposição.
- 6.0 a 7.4: Regular. Requer um pouco mais de cuidado em puxar a frente ou repor alguns itens.
- Abaixo de 6.0: Atenção redobrada. Setor muito desabastecido, desorganizado ou com higiene negligenciada.
* O campo 'conformidade' deve ser 'true' para score >= 7.0 e 'false' para score < 7.0.

=======================================================
ESTRUTURA DA MENSAGEM DO WHATSAPP (Tom Educativo):
=======================================================
Monte a "mensagem_whatsapp" pronta para postar no grupo, usando emojis e negrito (*texto*), seguindo a estrutura:
- 📸 Setor identificado e elogio sincero e específico ao que está correto.
- 💡 Oportunidades de melhoria: de 1 a 3 tópicos curtos, práticos e construtivos (derivados unicamente do visível).
- ⭐ Nota de 0 a 10 (com critérios flexíveis nesta fase inicial) acompanhada de frase motivadora.

Exemplo de formatação:
📸 *Setor: Freezer e Geladeiras de Bebidas*
👏 Parabéns pelo capricho! Rótulos bem alinhados para a frente e geladeiras bem iluminadas!

💡 *Oportunidades de Melhoria:*
• Puxar 2 latas da grade intermediária para a borda frontal para manter o visual abastecido.
• Repor o espaço vazio na fileira de águas sem gás.

⭐ *Nota do Turno:* 8.4/10 - Excelente trabalho equipe! Ajustando esses pontos fica nota 10!

=======================================================
RETORNO JSON ESTRUTURADO OBRIGATÓRIO:
=======================================================
Retorne ESTRITAMENTE um objeto JSON válido, sem texto antes ou depois, com o campo de raciocínio preliminar:
{
  "elementos_visuais_detectados": ["cervejeira itaipava", "cervejeira skol", "latas de cerveja", "refrigerantes pet"],
  "setor": "FREEZER_GELADEIRA" | "GONDOLA_PRATELEIRA" | "ESTUFA_SALGADOS" | "BANHEIRO" | "BALCAO_CAIXA" | "INVALID_OBJECT",
  "conformidade": boolean,
  "score": number,
  "pontos_positivos": ["ponto positivo 1", "ponto positivo 2"],
  "pontos_melhoria": ["melhoria prática 1", "melhoria prática 2"],
  "mensagem_whatsapp": "📸 *Setor: Freezer e Geladeiras de Bebidas*..."
}
"""


class StatusAnalise(str, Enum):
    CONCLUIDA = "CONCLUIDA"
    PARCIAL = "PARCIAL"
    NAO_VERIFICAVEL = "NAO_VERIFICAVEL"


class Severidade(str, Enum):
    BAIXA = "BAIXA"
    MEDIA = "MEDIA"
    ALTA = "ALTA"


class EvidenciaVisual(BaseModel):
    criterio: str = Field(..., description="Nome do critério: precificacao, limpeza, organizacao, espaco_vazio, conservacao")
    descricao: str = Field(..., description="Fato visual observado sem inferir estoque")
    localizacao: str = Field(..., description="Posição visual (ex: prateleira 2, lado direito)")
    conforme: bool = Field(..., description="True se dentro do padrão, False se há inconformidade")
    severidade: Optional[Severidade] = Field(default=Severidade.BAIXA)


class GeminiAuditoriaOutput(BaseModel):
    setor_identificado: str = Field(..., description="FREEZER_GELADEIRA, ESTUFA, BANHEIRO, VIDROS, CAIXA, GONDOLA_GERAL")
    status_analise: StatusAnalise
    nivel_confianca: float = Field(..., ge=0.0, le=1.0)
    requer_revisao: bool = Field(default=False)
    elementos_visuais_detectados: List[str]
    evidencias: List[EvidenciaVisual]
    acoes_recomendadas: List[str]


class SectorAnalysisResult(BaseModel):
    """Esquema de validação para a análise multimodal por setores do Gemini 2.0 Flash."""
    elementos_visuais_detectados: List[str] = Field(
        default_factory=list,
        description="Lista de objetos físicos detectados na cena (Passo 1 do Chain of Thought)"
    )
    setor: str = Field(
        description="Setor identificado: FREEZER_GELADEIRA, GONDOLA_PRATELEIRA, ESTUFA_SALGADOS, BANHEIRO, BALCAO_CAIXA ou INVALID_OBJECT"
    )
    conformidade: bool = Field(
        description="Indica conformidade com padrão da Fase 1 (score >= 7.0)"
    )
    score: float = Field(
        ge=0.0,
        le=10.0,
        description="Nota de 0 a 10 atribuída com critérios flexíveis da Fase 1"
    )
    pontos_positivos: List[str] = Field(
        default_factory=list,
        description="Aspectos positivos e conformidades identificadas"
    )
    pontos_melhoria: List[str] = Field(
        default_factory=list,
        description="1 a 3 oportunidades de melhoria curtas e práticas estritamente visíveis"
    )
    mensagem_whatsapp: str = Field(
        description="Mensagem completa formatada com emojis e negrito pronta para o WhatsApp"
    )

    # Campos de compatibilidade e gamificação
    status: Optional[str] = None
    aprovado: Optional[bool] = None
    problemas: Optional[List[str]] = None
    orientacao: Optional[str] = None
    motivo: Optional[str] = None
    score_100: Optional[int] = None
    operator_name: Optional[str] = None
    shift: Optional[str] = None
    points: Optional[float] = None


class GeminiService:
    """Cliente para execução de auditorias multimodais calibradas por setor com Gemini 2.0 Flash."""

    VALID_SECTORS = {
        "FREEZER_GELADEIRA",
        "GONDOLA_PRATELEIRA",
        "GONDOLA_GERAL",
        "ESTUFA_SALGADOS",
        "ESTUFA",
        "BANHEIRO",
        "BALCAO_CAIXA",
        "CAIXA",
        "VIDROS",
        "INVALID_OBJECT",
    }

    SECTOR_DISPLAY_NAMES = {
        "FREEZER_GELADEIRA": "Freezer e Geladeiras de Bebidas",
        "GONDOLA_PRATELEIRA": "Gôndolas e Prateleiras de Mercearia/Snacks",
        "GONDOLA_GERAL": "Gôndolas e Prateleiras Gerais",
        "ESTUFA_SALGADOS": "Estufa de Salgados e Lanches",
        "ESTUFA": "Estufa de Salgados e Lanches",
        "BANHEIRO": "Sanitário e Higiene",
        "BALCAO_CAIXA": "Balcão de Atendimento e Caixa",
        "CAIXA": "Balcão de Atendimento e Caixa",
        "VIDROS": "Vidros e Fachada",
        "INVALID_OBJECT": "Setor Não Reconhecido",
    }

    def __init__(self) -> None:
        self.api_key = settings.GEMINI_API_KEY
        self.model_name = settings.GEMINI_MODEL or "gemini-2.0-flash"
        self._client = None

        if self.api_key and GENAI_AVAILABLE:
            try:
                self._client = genai.Client(api_key=self.api_key)
                logger.info(f"[Gemini] Inicializado com sucesso usando o modelo '{self.model_name}'.")
            except Exception as e:
                logger.error(f"[Gemini] Erro ao inicializar cliente google-genai: {e}")

    def _get_client(self):
        """Obtém ou instancia o cliente GenAI."""
        if not self._client and self.api_key and GENAI_AVAILABLE:
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    async def analyze_store_image(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg"
    ) -> Dict[str, Any]:
        """
        Executa a auditoria visual da imagem da loja via Gemini 2.0 Flash calibrada por setores.

        Args:
            image_bytes: Bytes brutos da foto da loja.
            mime_type: Tipo MIME da imagem (ex: 'image/jpeg', 'image/png').

        Returns:
            Dict com chaves 'setor', 'conformidade', 'score', 'pontos_positivos',
            'pontos_melhoria', 'mensagem_whatsapp' e chaves de compatibilidade.
        """
        if not image_bytes:
            logger.error("[Gemini] Bytes de imagem vazios fornecidos.")
            return self._build_invalid_response("Nenhuma imagem válida recebida.")

        client = self._get_client()

        # Fallback inteligente para desenvolvimento/teste se API KEY não estiver configurada
        if not client or not self.api_key or self.api_key == "sua_gemini_api_key_aqui":
            logger.warning(
                "[Gemini MOCK] GEMINI_API_KEY não configurada no .env. "
                "Executando análise simulada de demonstração."
            )
            return self._mock_analysis(image_bytes)

        try:
            logger.info(
                f"[Gemini] Enviando imagem ({len(image_bytes)} bytes) para análise por setor no modelo '{self.model_name}'..."
            )

            # Montagem das partes de conteúdo multimodal
            image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
            prompt_instruction = (
                "Execute a auditoria operacional seguindo o raciocínio visual passo a passo (Chain of Thought): "
                "PASSO 1: Liste exatamente os objetos físicos visíveis na cena (ex: 'cervejeira vertical vermelha Itaipava', "
                "'latas de cerveja', 'garrafas pet de refrigerante', 'cervejeira amarela Skol') no campo preliminar 'elementos_visuais_detectados'. "
                "PASSO 2: Com base estritamente nos objetos listados, selecione o setor correto. "
                "REGRA DE CLASSIFICAÇÃO INEGOCIÁVEL: Se houver cervejeiras, geladeiras expositoras verticais, portas de vidro com bebidas "
                "(cervejas, refrigerantes, águas, energéticos) ou freezers horizontais, o setor OBRIGATORIAMENTE deve ser 'FREEZER_GELADEIRA'. "
                "É terminantemente proibido classificar como Balcão ou Caixa se houver refrigeradores ou bebidas em destaque. "
                "Só classifique como 'BALCAO_CAIXA' se o foco central for terminal de pagamento, monitor de PDV, gaveta de dinheiro ou checkout. "
                "ELIMINAR ALUCINAÇÕES: As oportunidades de melhoria DEVEM ser derivadas unicamente de defeitos visíveis na imagem enviada. "
                "NUNCA mencione objetos não visíveis (como fios, bobinas, maquininhas). "
                "Adote o tom de supervisor orientador da Fase 1 e monte a 'mensagem_whatsapp' completa com emojis e nota de 0 a 10."
            )

            response = client.models.generate_content(
                model=self.model_name,
                contents=[image_part, prompt_instruction],
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_AUDITOR_PROMPT,
                    response_mime_type="application/json",
                    temperature=0.1,  # Baixa criatividade, foco absoluto na realidade visual exata
                ),
            )

            response_text = response.text or "{}"
            logger.info(f"[Gemini] Resposta recebida: {response_text[:140]}...")

            return self._parse_and_validate_response(response_text)

        except Exception as e:
            logger.exception(f"[Gemini] Falha na chamada da API Gemini: {e}")
            return {
                "elementos_visuais_detectados": [],
                "setor": "FREEZER_GELADEIRA",
                "conformidade": False,
                "score": 6.0,
                "score_100": 60,
                "pontos_positivos": ["Foto recebida para análise operacional"],
                "pontos_melhoria": [f"Instabilidade temporária no processamento da imagem: {str(e)}"],
                "mensagem_whatsapp": (
                    "⚠️ *Instabilidade no Processamento:*\n"
                    "Houve uma oscilação momentânea ao analisar a foto. "
                    "Por favor, reenvie a foto no grupo para confirmarmos a nota do turno!"
                ),
                "status": "ALERT",
                "aprovado": False,
                "problemas": [str(e)],
                "orientacao": "Favor reenviar a foto para nova tentativa.",
                "motivo": f"Erro de processamento: {str(e)}",
            }

    def _parse_and_validate_response(self, text: str) -> Dict[str, Any]:
        """Extrai, valida e normaliza o JSON da resposta do Gemini com Chain of Thought."""
        try:
            cleaned = text.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
                cleaned = re.sub(r"\s*```$", "", cleaned)

            data = json.loads(cleaned)

            # 1. Extração dos elementos visuais detectados (Passo 1 do Chain of Thought)
            raw_elementos = data.get("elementos_visuais_detectados", [])
            if isinstance(raw_elementos, list):
                elementos_visuais = [str(e).strip() for e in raw_elementos if str(e).strip()]
            elif isinstance(raw_elementos, str) and raw_elementos.strip():
                elementos_visuais = [raw_elementos.strip()]
            else:
                elementos_visuais = []

            # 2. Normalização do setor
            raw_setor = str(data.get("setor", "GONDOLA_PRATELEIRA")).upper().strip()
            if raw_setor in self.VALID_SECTORS:
                setor = raw_setor
            else:
                # Tenta mapear caso venha com variação
                if "FREEZER" in raw_setor or "GELADEIRA" in raw_setor or "BEBIDA" in raw_setor or "CERVEJ" in raw_setor:
                    setor = "FREEZER_GELADEIRA"
                elif "ESTUFA" in raw_setor or "SALGADO" in raw_setor:
                    setor = "ESTUFA_SALGADOS"
                elif "BANHEIRO" in raw_setor or "SANIT" in raw_setor:
                    setor = "BANHEIRO"
                elif "CAIXA" in raw_setor or "BALCAO" in raw_setor or "CHECKOUT" in raw_setor:
                    setor = "BALCAO_CAIXA"
                elif "INVALID" in raw_setor:
                    setor = "INVALID_OBJECT"
                else:
                    setor = "GONDOLA_PRATELEIRA"

            # 3. REGRA DE CLASSIFICAÇÃO DE SETOR INEGOCIÁVEL (Chain of Thought Safety Override):
            # Se houver menção a cervejeiras, geladeiras expositoras verticais, bebidas (cervejas,
            # refrigerantes, águas, energéticos) ou freezers nos elementos visuais detectados:
            # O setor OBRIGATORIAMENTE deve ser "FREEZER_GELADEIRA".
            # É terminantemente proibido classificar como Balcão ou Caixa se houver refrigeradores ou bebidas em destaque.
            beverage_cooler_keywords = {
                "cervejeira", "cervejeiras", "geladeira", "geladeiras", "freezer", "freezers",
                "cerveja", "cervejas", "refrigerante", "refrigerantes", "bebida", "bebidas",
                "lata", "latas", "garrafa", "garrafas", "energetico", "energético", "água", "agua",
                "suco", "sucos", "skol", "brahma", "heineken", "itaipava", "ambev", "coca-cola", "pepsi"
            }
            elementos_joined = " ".join(elementos_visuais).lower()
            has_cooler_or_beverage = any(k in elementos_joined for k in beverage_cooler_keywords)

            if has_cooler_or_beverage and setor in ("BALCAO_CAIXA", "GONDOLA_PRATELEIRA"):
                logger.warning(
                    f"[Gemini Safety Override] Elementos visuais detectados contêm itens de refrigeradores/bebidas: "
                    f"{elementos_visuais}. Corrigindo setor de '{setor}' para 'FREEZER_GELADEIRA' (Regra Inegociável)."
                )
                setor = "FREEZER_GELADEIRA"

            # Tratamento prioritário para INVALID_OBJECT
            if setor == "INVALID_OBJECT":
                return self._build_invalid_response(data.get("motivo"), elementos=elementos_visuais)

            # Pontos positivos e de melhoria
            pontos_positivos = [str(p).strip() for p in data.get("pontos_positivos", []) if str(p).strip()]
            pontos_melhoria = [str(m).strip() for m in data.get("pontos_melhoria", []) if str(m).strip()]

            # 4. Eliminação estrita de alucinações (fios, bobinas, maquininhas inventadas)
            hallucinated_checkout_terms = ["fio", "fios", "bobina", "bobinas", "maquininha", "maquininhas", "pinpad", "pin pad"]
            if setor == "FREEZER_GELADEIRA":
                pontos_melhoria = [
                    m for m in pontos_melhoria
                    if not any(term in m.lower() for term in hallucinated_checkout_terms)
                ]
                if not pontos_melhoria:
                    pontos_melhoria = ["Puxar latas e garrafas para a borda frontal (frenteamento)"]

            # Garante no máximo 3 tópicos de melhoria
            if len(pontos_melhoria) > 3:
                pontos_melhoria = pontos_melhoria[:3]

            # 5. Estruturação do GeminiAuditoriaOutput e Avaliação Determinística (Perito Visual -> Juiz)
            raw_evidencias = data.get("evidencias", [])
            evidencias_list: List[EvidenciaVisual] = []
            if isinstance(raw_evidencias, list) and raw_evidencias:
                for item in raw_evidencias:
                    if isinstance(item, dict):
                        sev_str = str(item.get("severidade", "BAIXA")).upper().strip()
                        sev_val = Severidade(sev_str) if sev_str in ("BAIXA", "MEDIA", "ALTA") else Severidade.BAIXA
                        evidencias_list.append(
                            EvidenciaVisual(
                                criterio=str(item.get("criterio", "organizacao")).lower().strip(),
                                descricao=str(item.get("descricao", "")).strip(),
                                localizacao=str(item.get("localizacao", "geral")).strip(),
                                conforme=bool(item.get("conforme", True)),
                                severidade=sev_val,
                            )
                        )

            # Se não houver evidências explícitas no JSON bruto, sintetiza a partir dos pontos detectados
            if not evidencias_list and setor != "INVALID_OBJECT":
                for pos in pontos_positivos:
                    evidencias_list.append(
                        EvidenciaVisual(
                            criterio="organizacao",
                            descricao=pos,
                            localizacao="área geral",
                            conforme=True,
                            severidade=Severidade.BAIXA,
                        )
                    )
                for mel in pontos_melhoria:
                    evidencias_list.append(
                        EvidenciaVisual(
                            criterio="espaco_vazio" if any(w in mel.lower() for w in ["repor", "vazio", "falta"]) else "organizacao",
                            descricao=mel,
                            localizacao="área geral",
                            conforme=False,
                            severidade=Severidade.MEDIA if any(w in mel.lower() for w in ["ruptura", "vazio"]) else Severidade.BAIXA,
                        )
                    )

            raw_status = str(data.get("status_analise", "CONCLUIDA")).upper().strip()
            try:
                status_analise = StatusAnalise(raw_status)
            except ValueError:
                status_analise = StatusAnalise.CONCLUIDA

            acoes_rec = data.get("acoes_recomendadas", pontos_melhoria)
            if not isinstance(acoes_rec, list):
                acoes_rec = [str(acoes_rec)]

            gemini_output = GeminiAuditoriaOutput(
                setor_identificado=setor,
                status_analise=status_analise,
                nivel_confianca=float(data.get("nivel_confianca", 0.95)),
                requer_revisao=bool(data.get("requer_revisao", False)),
                elementos_visuais_detectados=elementos_visuais,
                evidencias=evidencias_list,
                acoes_recomendadas=[str(a).strip() for a in acoes_rec if str(a).strip()],
            )

            # Executa a avaliação determinística via evaluator_service
            from services.evaluator_service import calcular_nota, evaluator_service
            resultado_avaliacao = calcular_nota(gemini_output)

            # Normalização do score: se vier score direto pré-fixado sem evidências explícitas, preserva-o;
            # caso contrário, adota a nota calculada matematicamente pelo avaliador
            if "score" in data and not raw_evidencias:
                raw_score = data.get("score")
                try:
                    score = float(raw_score) if raw_score is not None else 7.0
                except (ValueError, TypeError):
                    score = 7.0
                if score > 10.0:
                    score = score / 10.0
                score = round(max(0.0, min(10.0, score)), 1)
            else:
                score = resultado_avaliacao.nota_calculada

            # Conformidade: True para score >= 7.0 e sem necessidade de ação imediata
            conformidade_val = data.get("conformidade")
            if conformidade_val is not None:
                conformidade = bool(conformidade_val)
            else:
                conformidade = bool(score >= 7.0 and not resultado_avaliacao.requer_acao_imediata)

            # Mensagem formatada para o WhatsApp
            msg_whatsapp = str(data.get("mensagem_whatsapp", "")).strip()

            # Caso a mensagem do WhatsApp venha vazia ou com setor inconsistente, regenera
            if not msg_whatsapp or (setor == "FREEZER_GELADEIRA" and "Caixa" in msg_whatsapp):
                msg_whatsapp = self._build_whatsapp_message(
                    setor=setor,
                    score=score,
                    pontos_positivos=pontos_positivos,
                    pontos_melhoria=pontos_melhoria,
                )

            # Compatibilidade com colunas do banco e endpoints existentes
            status_val = "OK" if conformidade else "ALERT"
            score_100 = int(round(score * 10))

            return {
                "elementos_visuais_detectados": elementos_visuais,
                "setor": setor,
                "setor_identificado": setor,
                "conformidade": conformidade,
                "score": score,
                "score_100": score_100,
                "pontos_positivos": pontos_positivos,
                "pontos_melhoria": pontos_melhoria,
                "mensagem_whatsapp": msg_whatsapp,
                # Dados do serviço avaliador determinístico
                "status_analise": resultado_avaliacao.status_analise.value,
                "requer_revisao": resultado_avaliacao.requer_revisao,
                "requer_acao_imediata": resultado_avaliacao.requer_acao_imediata,
                "itens_corrigiveis": [it.model_dump() for it in resultado_avaliacao.itens_corrigiveis],
                "resumo_operacional": resultado_avaliacao.resumo_operacional,
                "avaliacao": resultado_avaliacao.model_dump(),
                "gemini_output": gemini_output.model_dump(),
                # Chaves de compatibilidade
                "status": status_val,
                "aprovado": conformidade,
                "problemas": pontos_melhoria,
                "orientacao": " ".join(pontos_melhoria) if pontos_melhoria else "Manter o excelente padrão!",
                "motivo": None if conformidade else "Oportunidades de melhoria identificadas no setor",
            }

        except Exception as e:
            logger.error(f"[Gemini] Erro ao parsear JSON: {e}. Texto recebido: {text}")
            return {
                "elementos_visuais_detectados": [],
                "setor": "FREEZER_GELADEIRA",
                "conformidade": False,
                "score": 6.0,
                "score_100": 60,
                "pontos_positivos": [],
                "pontos_melhoria": ["Falha técnica na interpretação visual da imagem"],
                "mensagem_whatsapp": (
                    "⚠️ *Atenção Operacional:*\n"
                    "Houve uma falha ao interpretar os detalhes da foto. "
                    "Por favor, reenvie a foto enquadrando bem os produtos."
                ),
                "status": "ALERT",
                "aprovado": False,
                "problemas": ["Falha no parsing da resposta"],
                "orientacao": "Reenvie a foto com bom foco e iluminação.",
                "motivo": "Falha no processamento do JSON da IA.",
            }

    def _build_whatsapp_message(
        self,
        setor: str,
        score: float,
        pontos_positivos: List[str],
        pontos_melhoria: List[str],
    ) -> str:
        """Monta dinamicamente a mensagem de WhatsApp educativa se a IA não fornecer pronta."""
        display_name = self.SECTOR_DISPLAY_NAMES.get(setor, "Setor da Conveniência")
        elogio = pontos_positivos[0] if pontos_positivos else "Setor com boa visibilidade e equipe empenhada!"

        lines = [
            f"📸 *Setor: {display_name}*",
            f"👏 {elogio}",
            "",
            "💡 *Oportunidades de Melhoria:*",
        ]

        if pontos_melhoria:
            for item in pontos_melhoria[:3]:
                lines.append(f"• {item}")
        else:
            lines.append("• Manter o alinhamento e o padrão de abastecimento durante o pico!")

        lines.append("")
        if score >= 9.0:
            frase = "Parabéns pelo capricho impecável da equipe!"
        elif score >= 7.5:
            frase = "Ótimo trabalho! Pequenos ajustes e o setor fica nota 10!"
        elif score >= 6.0:
            frase = "Bom esforço! Fazendo esses alinhamentos a loja fica 100% pronta!"
        else:
            frase = "Vamos focar na reposição e no alinhamento para o pico de vendas!"

        lines.append(f"⭐ *Nota do Turno:* {score}/10 - {frase}")
        return "\n".join(lines)

    def _build_invalid_response(
        self,
        motivo: Optional[str] = None,
        elementos: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Gera resposta padronizada para imagens que não retratam a loja."""
        from services.evaluator_service import calcular_nota
        analise_inv = GeminiAuditoriaOutput(
            setor_identificado="INVALID_OBJECT",
            status_analise=StatusAnalise.NAO_VERIFICAVEL,
            nivel_confianca=0.0,
            requer_revisao=True,
            elementos_visuais_detectados=elementos or [],
            evidencias=[],
            acoes_recomendadas=["A foto não retrata um setor operacional da conveniência."],
        )
        res_inv = calcular_nota(analise_inv)
        return {
            "elementos_visuais_detectados": elementos or [],
            "setor": "INVALID_OBJECT",
            "setor_identificado": "INVALID_OBJECT",
            "conformidade": False,
            "score": 0.0,
            "score_100": 0,
            "pontos_positivos": [],
            "pontos_melhoria": ["A foto não retrata um setor operacional da conveniência."],
            "mensagem_whatsapp": (
                "❌ *Foto Não Identificada:*\n"
                "A imagem enviada não retrata um setor da conveniência (geladeiras, gôndolas, estufa, banheiro ou balcão/caixa).\n"
                "Por favor, envie uma foto nítida e direta da área a ser auditada."
            ),
            "status_analise": res_inv.status_analise.value,
            "requer_revisao": res_inv.requer_revisao,
            "requer_acao_imediata": res_inv.requer_acao_imediata,
            "itens_corrigiveis": [],
            "resumo_operacional": res_inv.resumo_operacional,
            "avaliacao": res_inv.model_dump(),
            "gemini_output": analise_inv.model_dump(),
            "status": "INVALID_OBJECT",
            "aprovado": False,
            "problemas": ["Imagem fora do padrão de auditoria de loja."],
            "orientacao": "Por favor, tire a foto focando nas prateleiras, refrigeradores, estufa ou balcão.",
            "motivo": motivo or "Imagem fora do padrão de auditoria de loja.",
        }

    def _mock_analysis(self, image_bytes: bytes) -> Dict[str, Any]:
        """Simulação determinística e educativa para testes de desenvolvimento sem API Key."""
        from services.evaluator_service import calcular_nota

        size = len(image_bytes)
        if size < 3000:
            return self._build_invalid_response("Imagem muito pequena ou sem resolução adequada.")

        # Varia os setores para fins de teste conforme o tamanho da imagem
        selector = (size // 10) % 5

        if selector == 0:
            analise = GeminiAuditoriaOutput(
                setor_identificado="FREEZER_GELADEIRA",
                status_analise=StatusAnalise.CONCLUIDA,
                nivel_confianca=0.95,
                requer_revisao=False,
                elementos_visuais_detectados=[
                    "cervejeira itaipava",
                    "cervejeira skol",
                    "latas de cerveja",
                    "refrigerantes pet",
                ],
                evidencias=[
                    EvidenciaVisual(
                        criterio="organizacao",
                        descricao="Rótulos das cervejas e refrigerantes bem alinhados para a frente",
                        localizacao="grades frontais",
                        conforme=True,
                    ),
                    EvidenciaVisual(
                        criterio="limpeza",
                        descricao="Cervejeiras verticais bem iluminadas e vidros limpos",
                        localizacao="portas de vidro",
                        conforme=True,
                    ),
                    EvidenciaVisual(
                        criterio="precificacao",
                        descricao="Etiquetas de preço visíveis nas canaletas",
                        localizacao="canaletas das grades",
                        conforme=True,
                    ),
                    EvidenciaVisual(
                        criterio="espaco_vazio",
                        descricao="Puxar 2 latas da grade intermediária para a borda frontal",
                        localizacao="grade intermediária",
                        conforme=False,
                        severidade=Severidade.BAIXA,
                    ),
                ],
                acoes_recomendadas=[
                    "Puxar 2 latas da grade intermediária para a borda frontal",
                    "Repor o espaço vazio na fileira de águas sem gás",
                ],
            )
            res_eval = calcular_nota(analise)

            return {
                "elementos_visuais_detectados": analise.elementos_visuais_detectados,
                "setor": "FREEZER_GELADEIRA",
                "setor_identificado": "FREEZER_GELADEIRA",
                "conformidade": True,
                "score": 8.4,
                "score_100": 84,
                "pontos_positivos": [
                    "Rótulos das cervejas e refrigerantes bem alinhados para a frente",
                    "Cervejeiras verticais bem iluminadas e abastecidas",
                ],
                "pontos_melhoria": [
                    "Puxar 2 latas da grade intermediária para a borda frontal",
                    "Repor o espaço vazio na fileira de águas sem gás",
                ],
                "mensagem_whatsapp": (
                    "📸 *Setor: Freezer e Geladeiras de Bebidas*\n"
                    "👏 Parabéns pelo capricho! Rótulos bem alinhados para a frente e geladeiras bem iluminadas!\n\n"
                    "💡 *Oportunidades de Melhoria:*\n"
                    "• Puxar 2 latas da grade intermediária para a borda frontal para manter o visual abastecido.\n"
                    "• Repor o espaço vazio na fileira de águas sem gás.\n\n"
                    "⭐ *Nota do Turno:* 8.4/10 - Excelente trabalho equipe! Ajustando esses pontos fica nota 10!"
                ),
                "status_analise": res_eval.status_analise.value,
                "requer_revisao": res_eval.requer_revisao,
                "requer_acao_imediata": res_eval.requer_acao_imediata,
                "itens_corrigiveis": [it.model_dump() for it in res_eval.itens_corrigiveis],
                "resumo_operacional": res_eval.resumo_operacional,
                "avaliacao": res_eval.model_dump(),
                "gemini_output": analise.model_dump(),
                "status": "OK",
                "aprovado": True,
                "problemas": ["Puxar latas para a borda", "Repor espaço de águas"],
                "orientacao": "Manter o faceamento durante o horário de pico.",
                "motivo": None,
            }
        elif selector == 1:
            analise = GeminiAuditoriaOutput(
                setor_identificado="GONDOLA_PRATELEIRA",
                status_analise=StatusAnalise.CONCLUIDA,
                nivel_confianca=0.92,
                requer_revisao=False,
                elementos_visuais_detectados=[
                    "gôndola metálica central",
                    "pacotes de salgadinhos e snacks",
                    "etiquetas de preço frontais",
                ],
                evidencias=[
                    EvidenciaVisual(
                        criterio="organizacao",
                        descricao="Gôndola de salgadinhos bem abastecida e colorida",
                        localizacao="prateleiras gerais",
                        conforme=True,
                    ),
                    EvidenciaVisual(
                        criterio="precificacao",
                        descricao="Etiquetas de preço visíveis e alinhadas",
                        localizacao="canaletas frontais",
                        conforme=True,
                    ),
                    EvidenciaVisual(
                        criterio="espaco_vazio",
                        descricao="Alinhar 3 pacotes de snacks caídos e puxar batata palha para borda",
                        localizacao="segunda prateleira",
                        conforme=False,
                        severidade=Severidade.BAIXA,
                    ),
                ],
                acoes_recomendadas=[
                    "Alinhar 3 pacotes de snacks que estão caídos de lado na segunda prateleira",
                    "Puxar os pacotes de batata palha para a borda frontal",
                ],
            )
            res_eval = calcular_nota(analise)

            return {
                "elementos_visuais_detectados": analise.elementos_visuais_detectados,
                "setor": "GONDOLA_PRATELEIRA",
                "setor_identificado": "GONDOLA_PRATELEIRA",
                "conformidade": True,
                "score": 8.0,
                "score_100": 80,
                "pontos_positivos": [
                    "Gôndola de salgadinhos bem abastecida e colorida",
                    "Etiquetas de preço visíveis e alinhadas",
                ],
                "pontos_melhoria": [
                    "Alinhar 3 pacotes de snacks que estão caídos de lado na segunda prateleira",
                    "Puxar os pacotes de batata palha para a borda frontal",
                ],
                "mensagem_whatsapp": (
                    "📸 *Setor: Gôndolas e Prateleiras de Snacks*\n"
                    "👏 Muito bom! Gôndola bem abastecida e etiquetas de preço em ordem!\n\n"
                    "💡 *Oportunidades de Melhoria:*\n"
                    "• Alinhar 3 pacotes que tombaram de lado na segunda prateleira.\n"
                    "• Puxar os pacotes de batata palha até a borda frontal.\n\n"
                    "⭐ *Nota do Turno:* 8.0/10 - Bom padrão! Ajustando esses pacotes fica 10!"
                ),
                "status_analise": res_eval.status_analise.value,
                "requer_revisao": res_eval.requer_revisao,
                "requer_acao_imediata": res_eval.requer_acao_imediata,
                "itens_corrigiveis": [it.model_dump() for it in res_eval.itens_corrigiveis],
                "resumo_operacional": res_eval.resumo_operacional,
                "avaliacao": res_eval.model_dump(),
                "gemini_output": analise.model_dump(),
                "status": "OK",
                "aprovado": True,
                "problemas": ["Pacotes caídos de lado", "Frenteamento de batata palha"],
                "orientacao": "Puxar a frente dos salgadinhos e manter pacotes em pé.",
                "motivo": None,
            }
        elif selector == 2:
            analise = GeminiAuditoriaOutput(
                setor_identificado="ESTUFA_SALGADOS",
                status_analise=StatusAnalise.CONCLUIDA,
                nivel_confianca=0.94,
                requer_revisao=False,
                elementos_visuais_detectados=[
                    "estufa expositora térmica de vidro curvo",
                    "bandejas inox com salgados assados e fritos",
                    "plaquetas de preços",
                ],
                evidencias=[
                    EvidenciaVisual(
                        criterio="limpeza",
                        descricao="Vidros da estufa limpos e sem marcas de gordura aparentes",
                        localizacao="vidro frontal e laterais",
                        conforme=True,
                    ),
                    EvidenciaVisual(
                        criterio="apresentacao",
                        descricao="Salgados com aparência fresca, dourada e apetitosa nas bandejas",
                        localizacao="bandejas de inox",
                        conforme=True,
                    ),
                    EvidenciaVisual(
                        criterio="organizacao",
                        descricao="Reabastecer coxinhas e ajustar plaqueta de folhados",
                        localizacao="bandeja central",
                        conforme=False,
                        severidade=Severidade.BAIXA,
                    ),
                ],
                acoes_recomendadas=[
                    "Reabastecer as coxinhas que estão com apenas 2 unidades",
                    "Ajustar a placa de identificação dos folhados",
                ],
            )
            res_eval = calcular_nota(analise)

            return {
                "elementos_visuais_detectados": analise.elementos_visuais_detectados,
                "setor": "ESTUFA_SALGADOS",
                "setor_identificado": "ESTUFA_SALGADOS",
                "conformidade": True,
                "score": 8.5,
                "score_100": 85,
                "pontos_positivos": [
                    "Vidros da estufa limpos e sem marcas de gordura aparentes",
                    "Salgados com aparência fresca, dourada e apetitosa nas bandejas",
                ],
                "pontos_melhoria": [
                    "Reabastecer as coxinhas que estão com apenas 2 unidades",
                    "Ajustar a placa de identificação dos folhados",
                ],
                "mensagem_whatsapp": (
                    "📸 *Setor: Estufa de Salgados*\n"
                    "👏 Parabéns! Vidros bem limpinhos e os salgados estão com aspecto muito apetitoso!\n\n"
                    "💡 *Oportunidades de Melhoria:*\n"
                    "• Reabastecer a bandeja de coxinhas para o pico do turno.\n"
                    "• Alinhar a plaquinha com o preço dos folhados.\n\n"
                    "⭐ *Nota do Turno:* 8.5/10 - Apresentação de dar água na boca!"
                ),
                "status_analise": res_eval.status_analise.value,
                "requer_revisao": res_eval.requer_revisao,
                "requer_acao_imediata": res_eval.requer_acao_imediata,
                "itens_corrigiveis": [it.model_dump() for it in res_eval.itens_corrigiveis],
                "resumo_operacional": res_eval.resumo_operacional,
                "avaliacao": res_eval.model_dump(),
                "gemini_output": analise.model_dump(),
                "status": "OK",
                "aprovado": True,
                "problemas": ["Reabastecer coxinhas", "Alinhar placa dos folhados"],
                "orientacao": "Fritar nova remessa de salgados para não faltar no pico.",
                "motivo": None,
            }
        elif selector == 3:
            analise = GeminiAuditoriaOutput(
                setor_identificado="BANHEIRO",
                status_analise=StatusAnalise.CONCLUIDA,
                nivel_confianca=0.90,
                requer_revisao=False,
                elementos_visuais_detectados=[
                    "bancada com pia e torneira",
                    "espelho",
                    "dispenser de papel toalha",
                    "lixeira plástica",
                ],
                evidencias=[
                    EvidenciaVisual(
                        criterio="limpeza",
                        descricao="Pia limpa e espelho sem respingos",
                        localizacao="bancada da pia",
                        conforme=True,
                    ),
                    EvidenciaVisual(
                        criterio="conservacao",
                        descricao="Instalações hidráulicas conservadas sem vazamento aparente",
                        localizacao="torneira e cuba",
                        conforme=True,
                    ),
                    EvidenciaVisual(
                        criterio="reposicao_insumos",
                        descricao="Lixeira cheia e dispenser de papel toalha baixo",
                        localizacao="lixeira e dispenser",
                        conforme=False,
                        severidade=Severidade.MEDIA,
                    ),
                ],
                acoes_recomendadas=[
                    "Trocar o saco da lixeira antes que comece a transbordar",
                    "Reabastecer o dispenser de papel toalha",
                    "Conferir o nível do sabonete líquido",
                ],
            )
            res_eval = calcular_nota(analise)

            return {
                "elementos_visuais_detectados": analise.elementos_visuais_detectados,
                "setor": "BANHEIRO",
                "setor_identificado": "BANHEIRO",
                "conformidade": False,
                "score": 6.5,
                "score_100": 65,
                "pontos_positivos": [
                    "Pia limpa e espelho sem respingos",
                ],
                "pontos_melhoria": [
                    "Lixeira próxima do limite de capacidade (trocar o saco plástico)",
                    "Repor papel toalha no dispenser",
                    "Verificar abastecimento de sabonete líquido",
                ],
                "mensagem_whatsapp": (
                    "📸 *Setor: Sanitário e Higiene*\n"
                    "👏 Pia e bancada limpas e secas, muito bom!\n\n"
                    "💡 *Oportunidades de Melhoria:*\n"
                    "• Trocar o saco da lixeira antes que comece a transbordar.\n"
                    "• Reabastecer o dispenser de papel toalha.\n"
                    "• Conferir o nível do sabonete líquido.\n\n"
                    "⭐ *Nota do Turno:* 6.5/10 - Uma passadinha rápida para repor os insumos e fica excelente!"
                ),
                "status_analise": res_eval.status_analise.value,
                "requer_revisao": res_eval.requer_revisao,
                "requer_acao_imediata": res_eval.requer_acao_imediata,
                "itens_corrigiveis": [it.model_dump() for it in res_eval.itens_corrigiveis],
                "resumo_operacional": res_eval.resumo_operacional,
                "avaliacao": res_eval.model_dump(),
                "gemini_output": analise.model_dump(),
                "status": "ALERT",
                "aprovado": False,
                "problemas": ["Lixeira cheia", "Papel toalha baixo", "Conferir sabonete"],
                "orientacao": "Esvaziar lixeira e repor papel toalha imediatamente.",
                "motivo": "Insumos de higiene necessitando de reposição.",
            }
        else:
            analise = GeminiAuditoriaOutput(
                setor_identificado="BALCAO_CAIXA",
                status_analise=StatusAnalise.CONCLUIDA,
                nivel_confianca=0.96,
                requer_revisao=False,
                elementos_visuais_detectados=[
                    "balcão de atendimento e checkout",
                    "monitor de PDV",
                    "expositor de balas e chicletes",
                ],
                evidencias=[
                    EvidenciaVisual(
                        criterio="limpeza",
                        descricao="Bancada do checkout desobstruída para os clientes",
                        localizacao="área central do balcão",
                        conforme=True,
                    ),
                    EvidenciaVisual(
                        criterio="impulso_visivel",
                        descricao="Expositores de chicletes e pastilhas bem organizados",
                        localizacao="frente do caixa",
                        conforme=True,
                    ),
                    EvidenciaVisual(
                        criterio="organizacao",
                        descricao="Alinhar pacotes de pastilhas na frente do expositor",
                        localizacao="expositor de doces",
                        conforme=False,
                        severidade=Severidade.BAIXA,
                    ),
                ],
                acoes_recomendadas=[
                    "Alinhar pacotes de pastilhas na frente do expositor",
                    "Manter a área central do balcão sempre livre para agilizar a passagem de compras",
                ],
            )
            res_eval = calcular_nota(analise)

            return {
                "elementos_visuais_detectados": analise.elementos_visuais_detectados,
                "setor": "BALCAO_CAIXA",
                "setor_identificado": "BALCAO_CAIXA",
                "conformidade": True,
                "score": 8.8,
                "score_100": 88,
                "pontos_positivos": [
                    "Bancada do checkout desobstruída para os clientes apoiarem os itens",
                    "Expositores de chicletes e pastilhas bem organizados e alinhados",
                ],
                "pontos_melhoria": [
                    "Alinhar pacotes de pastilhas na frente do expositor",
                    "Manter a área central do balcão sempre livre para agilizar a passagem de compras",
                ],
                "mensagem_whatsapp": (
                    "📸 *Setor: Balcão de Atendimento e Caixa*\n"
                    "👏 Ponto de venda limpo, receptivo e excelente organização dos expositores de balas!\n\n"
                    "💡 *Oportunidades de Melhoria:*\n"
                    "• Alinhar pacotes de pastilhas na frente do expositor.\n"
                    "• Manter a área central do balcão sempre livre para agilizar a passagem de compras.\n\n"
                    "⭐ *Nota do Turno:* 8.8/10 - Caixa impecável e pronto para o atendimento!"
                ),
                "status_analise": res_eval.status_analise.value,
                "requer_revisao": res_eval.requer_revisao,
                "requer_acao_imediata": res_eval.requer_acao_imediata,
                "itens_corrigiveis": [it.model_dump() for it in res_eval.itens_corrigiveis],
                "resumo_operacional": res_eval.resumo_operacional,
                "avaliacao": res_eval.model_dump(),
                "gemini_output": analise.model_dump(),
                "status": "OK",
                "aprovado": True,
                "problemas": ["Alinhar pastilhas", "Manter área central do balcão livre"],
                "orientacao": "Manter a área de apoio livre para agilizar o atendimento.",
                "motivo": None,
            }


# Instância singleton do serviço Gemini
gemini_service = GeminiService()
