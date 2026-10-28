"""
Utilitários de nomenclatura e formatação visual (WhatsApp) para o pipeline de auditoria por imagem.
"""

import re
import secrets
from datetime import datetime
from typing import Optional

SEP = "━━━━━━━━━━━━━━━━━━━━"

# Código: DDMMAAAA_HHMMSS_LJ{store_id}_{HASH4}
CODIGO_REGEX = re.compile(r"\d{8}_\d{6}_LJ\d+_[A-F0-9]{4}")

# Julgamento da diretoria: "<codigo> SIM|NAO|PROCEDE|NEGAR <nota> [motivo]"
JULGAMENTO_REGEX = re.compile(
    r"^(?P<codigo>\d{8}_\d{6}_LJ\d+_[A-F0-9]{4})\s+"
    r"(?P<decisao>SIM|NAO|NÃO|PROCEDE|NEGAR)\s+"
    r"(?P<nota>\d{1,2})"
    r"(?:\s+(?P<motivo>.*))?$",
    re.IGNORECASE | re.DOTALL,
)

DEFESA_REGEX = re.compile(r"^\s*\*?DEFESA\*?\s*:\s*(?P<texto>.+)$", re.IGNORECASE | re.DOTALL)


def gerar_codigo_imagem(store_id: int) -> str:
    """Gera código único: DDMMAAAA_HHMMSS_LJ{store_id}_{HASH4}."""
    timestamp = datetime.now().strftime("%d%m%Y_%H%M%S")
    salt = secrets.token_hex(2).upper()
    return f"{timestamp}_LJ{store_id}_{salt}"


def extrair_codigo(texto: Optional[str]) -> Optional[str]:
    """Encontra um código de auditoria dentro de um texto (ex: mensagem citada)."""
    if not texto:
        return None
    m = CODIGO_REGEX.search(texto.upper())
    return m.group(0) if m else None


def formatar_nota_visual(nota: float) -> str:
    """Retorna semáforo, barra visual e status formatado com emojis."""
    nota = float(nota or 0.0)
    nota_arredondada = max(0, min(10, round(nota)))

    if nota >= 8.0:
        icone, bloco, status = "🟢", "🟩", "*CONFIÁVEL / REGULAR*"
    elif nota >= 5.0:
        icone, bloco, status = "🟡", "🟨", "*DIVERGÊNCIA / ATENÇÃO*"
    else:
        icone, bloco, status = "🔴", "🟥", "*CRÍTICO / NÃO PROCEDE*"

    barra = bloco * nota_arredondada + "⬜" * (10 - nota_arredondada)

    digitos = {
        "0": "0️⃣", "1": "1️⃣", "2": "2️⃣", "3": "3️⃣", "4": "4️⃣",
        "5": "5️⃣", "6": "6️⃣", "7": "7️⃣", "8": "8️⃣", "9": "9️⃣",
        ".": "·",
    }
    nota_str = f"{nota:.1f}".rstrip("0").rstrip(".")
    nota_icones = "".join(digitos.get(c, c) for c in nota_str)

    return f"{icone} {nota_icones}/🔟\n`[{barra}]` {status}"
