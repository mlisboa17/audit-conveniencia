"""
Configurações da aplicação utilizando Pydantic Settings.
Carrega variáveis de ambiente a partir do arquivo .env com validação e tipagem forte.
"""

import re
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator


def clean_whatsapp_jid(recipient: Optional[str]) -> str:
    """
    Normaliza um identificador do WhatsApp.
    - Se for um grupo (@g.us), preserva o sufixo @g.us e limpa espaços/caracteres espúrios.
    - Se for um número individual (@s.whatsapp.net ou puro número), extrai apenas dígitos.
    """
    if not recipient:
        return ""
    recipient = recipient.strip()
    if "@g.us" in recipient:
        prefix = recipient.split("@")[0].strip()
        # Mantém apenas dígitos e hifens do ID do grupo (ex: 120363123456789 ou 55119999-1234)
        clean_prefix = re.sub(r"[^\d\-]", "", prefix)
        return f"{clean_prefix}@g.us"
    # Individual: remove sufixos e caracteres não numéricos
    clean_num = clean_phone_number(recipient)
    return clean_num


def clean_phone_number(phone: Optional[str]) -> str:
    """
    Remove caracteres não numéricos de uma string de telefone.
    Remove sufixos comuns do WhatsApp (@s.whatsapp.net, @g.us).
    """
    if not phone:
        return ""
    # Remove sufixos do WhatsApp se existirem
    phone = phone.split("@")[0]
    # Mantém apenas dígitos
    cleaned = re.sub(r"\D", "", phone)
    return cleaned



class Settings(BaseSettings):
    """Configurações globais do micro-serviço de auditoria."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False
    )

    # Google Gemini
    GEMINI_API_KEY: str = Field(
        default="",
        description="Chave da API do Google Gemini Studio"
    )
    GEMINI_MODEL: str = Field(
        default="gemini-2.0-flash",
        description="Nome do modelo Gemini multimodal (ex: gemini-2.0-flash ou gemini-2.5-flash)"
    )

    # Evolution API (WhatsApp)
    EVOLUTION_API_URL: str = Field(
        default="http://localhost:8080",
        description="URL base da Evolution API"
    )
    EVOLUTION_API_KEY: str = Field(
        default="",
        description="Token de autenticação da Evolution API"
    )
    EVOLUTION_INSTANCE_NAME: str = Field(
        default="conveniencia",
        description="Nome da instância WhatsApp na Evolution API"
    )

    # Contato da Diretoria (Alertas Executivos)
    DIRECTOR_PHONE: str = Field(
        default="5511999990000",
        description="Telefone do diretor para receber alertas (DDI+DDD+Numero)"
    )

    # Banco de Dados SQLite Assíncrono
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./app.db",
        description="URL de conexão SQLAlchemy com driver aiosqlite"
    )

    # Servidor FastAPI
    PORT: int = Field(default=8000, description="Porta do servidor")
    HOST: str = Field(default="0.0.0.0", description="Host de ligação do servidor")
    DEBUG: bool = Field(default=True, description="Modo debug com logs detalhados")

    @field_validator("DIRECTOR_PHONE", mode="after")
    @classmethod
    def normalize_director_phone(cls, v: str) -> str:
        return clean_phone_number(v)


# Instância singleton acessível por toda a aplicação
settings = Settings()
