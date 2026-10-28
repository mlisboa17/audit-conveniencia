"""
Serviço de integração com a Evolution API (WhatsApp).
Responsável pelo envio de mensagens para GRUPOS (@g.us) e contatos privados,
download de mídias e listagem de grupos do WhatsApp.
"""

import base64
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
import httpx

from config import clean_phone_number, clean_whatsapp_jid, settings

logger = logging.getLogger("audit.evolution")


class EvolutionService:
    """Cliente HTTP assíncrono para operações na Evolution API."""

    def __init__(self) -> None:
        self.base_url = settings.EVOLUTION_API_URL.rstrip("/")
        self.api_key = settings.EVOLUTION_API_KEY
        self.instance = settings.EVOLUTION_INSTANCE_NAME

    def _get_headers(self) -> dict:
        """Monta os cabeçalhos de autenticação da Evolution API."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["apikey"] = self.api_key
        return headers

    async def send_text_message(self, recipient: str, text: str) -> bool:
        """
        Envia uma mensagem de texto para um GRUPO (@g.us) ou número individual via Evolution API.
        
        Args:
            recipient: ID do grupo (ex: '120363000000000001@g.us') ou telefone (DDI+DDD+Num).
            text: Conteúdo da mensagem a ser enviada.
            
        Returns:
            bool: True se o envio foi bem-sucedido, False caso contrário.
        """
        cleaned_recipient = clean_whatsapp_jid(recipient)
        if not cleaned_recipient:
            logger.warning("[Evolution] Destinatário inválido ou vazio.")
            return False

        endpoint = f"{self.base_url}/message/sendText/{self.instance}"
        payload = {
            "number": cleaned_recipient,
            "text": text,
            "options": {
                "delay": 1000,
                "presence": "composing",
                "linkPreview": False,
            },
        }

        tipo_destinatario = "Grupo" if "@g.us" in cleaned_recipient else "Contato"
        logger.info(f"[Evolution] Enviando mensagem para {tipo_destinatario} {cleaned_recipient} via {endpoint}...")

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(endpoint, json=payload, headers=self._get_headers())
                if response.status_code in (200, 201):
                    logger.info(f"[Evolution] Mensagem enviada com sucesso para {cleaned_recipient}.")
                    return True
                else:
                    logger.error(
                        f"[Evolution] Erro ao enviar mensagem para {cleaned_recipient}: "
                        f"Status {response.status_code} - Resposta: {response.text}"
                    )
                    return False
        except httpx.ConnectError:
            logger.warning(
                f"[Evolution API Offline/Mock] Não foi possível conectar a {self.base_url}. "
                f"Mensagem simulada enviada para {tipo_destinatario} {cleaned_recipient}: '{text[:70]}...'"
            )
            return False
        except Exception as e:
            logger.exception(f"[Evolution] Exceção inesperada ao enviar para {cleaned_recipient}: {e}")
            return False

    async def fetch_all_groups(self) -> List[Dict[str, Any]]:
        """
        Consulta a Evolution API para listar todos os grupos de WhatsApp em que o bot está inserido.
        Auxilia a obter os JIDs (@g.us) das lojas.
        """
        endpoint = f"{self.base_url}/group/fetchAllGroups/{self.instance}?getParticipants=false"
        logger.info(f"[Evolution] Buscando grupos cadastrados no WhatsApp via {endpoint}...")

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(endpoint, headers=self._get_headers())
                if response.status_code == 200:
                    data = response.json()
                    # A Evolution API pode retornar uma lista direta ou um dict com chave 'groups'
                    groups = data if isinstance(data, list) else data.get("groups", [])
                    formatted = []
                    for g in groups:
                        formatted.append({
                            "id": g.get("id") or g.get("jid"),
                            "subject": g.get("subject") or g.get("name", "Sem Nome"),
                            "size": g.get("size") or len(g.get("participants", [])),
                        })
                    logger.info(f"[Evolution] {len(formatted)} grupos encontrados na Evolution API.")
                    return formatted
                else:
                    logger.warning(f"[Evolution] Falha ao listar grupos: Status {response.status_code}")
        except httpx.ConnectError:
            logger.warning("[Evolution Mock] Servidor Evolution offline. Retornando lista de grupos simulada.")
        except Exception as e:
            logger.error(f"[Evolution] Erro ao consultar grupos: {e}")

        # Fallback de demonstração / ambiente de testes
        return [
            {"id": "120363000000000001@g.us", "subject": "Loja 1 - Equipe Centro (Operacional)", "size": 6},
            {"id": "120363000000000002@g.us", "subject": "Loja 2 - Equipe Jardins (Operacional)", "size": 8},
            {"id": "120363000000000003@g.us", "subject": "Loja 3 - Equipe Paulista (Operacional)", "size": 5},
        ]

    async def download_media(self, url: str) -> Optional[bytes]:
        """Faz download dos bytes de uma mídia através da URL informada."""
        if not url:
            return None

        logger.info(f"[Evolution] Baixando mídia de {url[:80]}...")
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=self._get_headers())
                if response.status_code == 200:
                    return response.content
                else:
                    logger.error(f"[Evolution] Falha no download da mídia: HTTP {response.status_code}")
                    return None
        except Exception as e:
            logger.error(f"[Evolution] Exceção ao baixar mídia da URL: {e}")
            return None

    @staticmethod
    def extract_image_bytes(payload: dict) -> Tuple[Optional[bytes], str, Optional[str]]:
        """
        Analisa a estrutura do payload recebido da Evolution API e extrai os bytes da imagem.
        Funciona tanto para mensagens enviadas em grupos quanto no privado.
        """
        mime_type = "image/jpeg"

        # 1. Base64 direto no payload de teste ('image_base64')
        if "image_base64" in payload and payload["image_base64"]:
            raw_b64 = payload["image_base64"]
            if "," in raw_b64:
                header, raw_b64 = raw_b64.split(",", 1)
                if "png" in header.lower():
                    mime_type = "image/png"
            try:
                data = base64.b64decode(raw_b64)
                return data, mime_type, "base64_payload"
            except Exception as e:
                logger.error(f"[Evolution] Falha ao decodificar base64 direto: {e}")

        # 2. Navegação na estrutura padrão da Evolution API
        data_block = payload.get("data", payload)
        message_block = data_block.get("message", {})
        image_message = message_block.get("imageMessage", {})

        # Extrai mimetype se disponível
        if "mimetype" in image_message:
            mime_type = image_message["mimetype"]

        # Base64 dentro do imageMessage ou message
        b64_candidate = image_message.get("base64") or message_block.get("base64")
        if b64_candidate:
            if "," in b64_candidate:
                header, b64_candidate = b64_candidate.split(",", 1)
                if "png" in header.lower():
                    mime_type = "image/png"
            try:
                data = base64.b64decode(b64_candidate)
                return data, mime_type, "base64_evolution"
            except Exception as e:
                logger.error(f"[Evolution] Falha ao decodificar base64 da Evolution: {e}")

        # URL para download
        url_candidate = (
            payload.get("image_url")
            or image_message.get("url")
            or message_block.get("mediaUrl")
        )

        return None, mime_type, url_candidate

    @staticmethod
    def extract_remote_jid(payload: dict) -> Tuple[Optional[str], bool, Optional[str]]:
        """
        Identifica a origem da mensagem:
        - Se for de um grupo, remoteJid termina em '@g.us'.
        - Extrai também o 'participant' (quem enviou dentro do grupo).

        Returns:
            Tuple[remote_jid, is_group, participant]
        """
        # Formatos simplificados de simulação
        raw_jid = (
            payload.get("whatsapp_group_jid")
            or payload.get("group_jid")
            or payload.get("remoteJid")
        )
        participant = payload.get("sender_phone") or payload.get("participant")

        if not raw_jid:
            # Estrutura padrão Evolution API (v1 / v2)
            data_block = payload.get("data", payload)
            key_block = data_block.get("key", {})
            raw_jid = (
                key_block.get("remoteJid")
                or data_block.get("remoteJid")
                or payload.get("remoteJid")
            )
            participant = (
                key_block.get("participant")
                or data_block.get("participant")
                or data_block.get("sender")
                or payload.get("sender")
            )

        if not raw_jid:
            return None, False, None

        cleaned_jid = clean_whatsapp_jid(raw_jid)
        is_group = cleaned_jid.endswith("@g.us")
        clean_part = clean_phone_number(participant) if participant else None

        return cleaned_jid, is_group, clean_part

    @staticmethod
    def is_forwarded_message(payload: dict) -> bool:
        """
        Verifica se a foto/mensagem recebida foi encaminhada de outro chat ou galeria no WhatsApp.
        Inspeciona contextInfo.isForwarded e contextInfo.forwardingScore.
        """
        data_block = payload.get("data", payload)
        if not isinstance(data_block, dict):
            return False

        message_block = data_block.get("message", {})
        if not isinstance(message_block, dict):
            return False

        image_message = message_block.get("imageMessage", {})
        if not isinstance(image_message, dict):
            image_message = {}

        # Busca contextInfo em imageMessage, message ou payload raiz
        context_info = (
            image_message.get("contextInfo")
            or message_block.get("contextInfo")
            or data_block.get("contextInfo")
            or payload.get("contextInfo")
            or {}
        )

        if isinstance(context_info, dict):
            is_forwarded = context_info.get("isForwarded", False)
            forwarding_score = context_info.get("forwardingScore", 0)
            if is_forwarded or (isinstance(forwarding_score, (int, float)) and forwarding_score > 0):
                return True

        # Verifica possíveis flags diretas
        if payload.get("isForwarded") or data_block.get("isForwarded") or image_message.get("isForwarded"):
            return True

        return False

    @staticmethod
    def extract_caption(payload: dict) -> Optional[str]:
        """
        Extrai a legenda (caption) associada a uma foto postada no WhatsApp.
        """
        # Checagem em payload direto
        if "caption" in payload and payload["caption"]:
            return str(payload["caption"]).strip()

        data_block = payload.get("data", payload)
        if not isinstance(data_block, dict):
            return None

        message_block = data_block.get("message", {})
        if not isinstance(message_block, dict):
            return None

        image_message = message_block.get("imageMessage", {})
        if isinstance(image_message, dict) and image_message.get("caption"):
            return str(image_message["caption"]).strip()

        if message_block.get("caption"):
            return str(message_block["caption"]).strip()

        return None

    @staticmethod
    def extract_text_message(payload: dict) -> Optional[str]:
        """
        Extrai o texto simples de mensagens de texto recebidas da Evolution API.
        """
        if "text" in payload and isinstance(payload["text"], str):
            return payload["text"].strip()

        data_block = payload.get("data", payload)
        if not isinstance(data_block, dict):
            return None

        message_block = data_block.get("message", {})
        if not isinstance(message_block, dict):
            return None

        # 1. Mensagem de texto simples
        if message_block.get("conversation"):
            return str(message_block["conversation"]).strip()

        # 2. Mensagem de texto estendida (com citação ou formatação)
        extended = message_block.get("extendedTextMessage", {})
        if isinstance(extended, dict) and extended.get("text"):
            return str(extended["text"]).strip()

        # 3. Campo text direto no data_block
        if data_block.get("text") and isinstance(data_block["text"], str):
            return data_block["text"].strip()

        return None

    @staticmethod
    def extract_operator_name_from_text(text: Optional[str]) -> Optional[str]:
        """
        Extrai e normaliza o nome do operador a partir de legendas ou respostas rápidas no grupo.
        Exemplos tratados:
        - 'Carlos' -> 'Carlos'
        - 'Marcos T1' -> 'Marcos'
        - 'Operador João' -> 'João'
        - 'Operadora Ana Paula' -> 'Ana Paula'
        - 'Auditoria por Lucas' -> 'Lucas'
        - 'Atendente Beatriz' -> 'Beatriz'
        - 'Turno 2 - Fernanda' -> 'Fernanda'
        """
        if not text:
            return None

        cleaned = text.strip()

        # Palavras ignoradas (comandos, saudações e confirmações genéricas)
        ignore_set = {
            "!ranking", "ranking", "ajuda", "!ajuda", "menu", "status",
            "!pendencias", "!pendências", "pendencias", "pendências",
            "!justificar", "justificar",
            "ok", "blz", "valeu", "obrigado", "obrigada", "sim", "nao", "não",
            "bom dia", "boa tarde", "boa noite", "ola", "olá", "oi"
        }
        lower_cleaned = cleaned.lower()
        if lower_cleaned in ignore_set or lower_cleaned.startswith("!justificar") or lower_cleaned.startswith("justificar ") or len(cleaned) > 50:
            return None

        # Remove quebras de linha e símbolos no início
        cleaned = re.sub(r"[\r\n]+", " ", cleaned)
        cleaned = re.sub(r"^[!/\\#\*\-_]+", "", cleaned)

        # Prefixos operacionais comuns
        prefixes = [
            r"^operador[a]?\s*[:\-]?\s*",
            r"^atendente\s*[:\-]?\s*",
            r"^colaborador[a]?\s*[:\-]?\s*",
            r"^auditoria\s+(?:feita\s+)?(?:por\s+)?",
            r"^foto\s+(?:de\s+|por\s+)?",
            r"^turno\s*[1-3t]?\s*[:\-]?\s*",
        ]
        for pattern in prefixes:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()

        # Sufixos de turno (ex: "Marcos T1", "João - T2", "Pedro (Manhã)")
        suffixes = [
            r"\s*[-_–]\s*t[1-3].*$",
            r"\s+t[1-3].*$",
            r"\s*\((?:t[1-3]|manh[aã]|tarde|noite).*\)$",
            r"\s+turno\s*[1-3]?.*$",
        ]
        for pattern in suffixes:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()

        # Remove caracteres de formatação do WhatsApp (*negrito*, _itálico_, etc.)
        cleaned = re.sub(r"[\*\_\~\`]+", "", cleaned).strip()

        # Validação final de nome próprio (2 a 30 caracteres, letras e acentos)
        if 2 <= len(cleaned) <= 30 and re.match(r"^[A-Za-zÀ-ÖØ-öø-ÿ\s]+$", cleaned):
            words = [w for w in cleaned.split() if len(w) > 0]
            if words and len(words) <= 4:
                return " ".join(words).title()

        return None



# Instância singleton para uso global
evolution_service = EvolutionService()
