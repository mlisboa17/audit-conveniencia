#!/usr/bin/env python3
"""
Script de Auditoria Visual Real para Lojas de Conveniência (Zero Mocks).
Varre o diretório dataset/ na raiz do projeto e processa cada imagem
utilizando estritamente a API real do Google Gemini 2.0 Flash e o Avaliador Determinístico.
"""

import json
import logging
import mimetypes
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Configura UTF-8 em stdout e stderr para compatibilidade total com emojis no Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Garante que a raiz do projeto esteja no sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Carrega variáveis de ambiente do .env
from dotenv import load_dotenv
load_dotenv(ROOT_DIR / ".env", override=True)

from config import settings
from services.evaluator_service import calcular_nota
from services.gemini_service import (
    GENAI_AVAILABLE,
    GeminiAuditoriaOutput,
    StatusAnalise,
    gemini_service,
)

# Configuração de Logs
logging.basicConfig(
    level=logging.INFO if settings.DEBUG else logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("audit.real")

DATASET_DIR = ROOT_DIR / "dataset"
RESULTADOS_DIR = DATASET_DIR / "resultados"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def verificar_ambiente_real():
    """Garante que a API real está configurada e proíbe terminantemente mocks."""
    api_key = os.getenv("GEMINI_API_KEY", settings.GEMINI_API_KEY).strip()
    
    if not api_key or api_key == "sua_gemini_api_key_aqui":
        print("\n" + "=" * 60)
        print("❌ ERRO FATAL: GEMINI_API_KEY não configurada no arquivo .env!")
        print("=" * 60)
        print("A auditoria real exige uma chave válida do Google Gemini (Zero Mocks).")
        print("Abra o arquivo .env e configure sua chave:")
        print("GEMINI_API_KEY=AIzaSy...")
        print("=" * 60 + "\n")
        sys.exit(1)

    if not GENAI_AVAILABLE:
        print("\n" + "=" * 60)
        print("❌ ERRO FATAL: Pacote 'google-genai' não está instalado no ambiente!")
        print("=" * 60)
        print("Instale a SDK oficial executando:")
        print("pip install google-genai")
        print("=" * 60 + "\n")
        sys.exit(1)

    # Sincroniza a chave e o modelo com o singleton e força cliente ativo
    gemini_service.api_key = api_key
    gemini_service.model_name = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
    gemini_service._client = None
    client = gemini_service._get_client()
    if not client:
        print("❌ Erro ao instanciar cliente do Google GenAI com a chave fornecida.")
        sys.exit(1)

    # Desativa fallbacks de mock para garantir Zero Mocks absoluto
    def _proibir_mock(*args, **kwargs):
        raise RuntimeError(
            "Uso de mocks proibido! A API real do Gemini falhou ou retornou erro inesperado."
        )

    gemini_service._mock_gatekeeper = _proibir_mock
    gemini_service._mock_perito_setorial = _proibir_mock


def auditar_dataset():
    """Executa a auditoria de todas as fotos presentes no diretório dataset/."""
    # Garante que a pasta dataset e a subpasta de resultados existam
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    RESULTADOS_DIR.mkdir(parents=True, exist_ok=True)

    # Varrer dataset/ buscando arquivos de imagem (ignorando diretórios como resultados/)
    arquivos_foto = sorted([
        f for f in DATASET_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ])

    # Regra 4: Se a pasta estiver vazia
    if not arquivos_foto:
        print("\n⚠️ Nenhuma foto encontrada na pasta 'dataset/'. Adicione fotos reais da loja para auditar.\n")
        print(f"Diretório: {DATASET_DIR.resolve()}")
        print(f"Formatos suportados: {', '.join(sorted(SUPPORTED_EXTENSIONS))}\n")
        return

    # Validação estrita de credenciais e proibição de mocks
    verificar_ambiente_real()

    total_fotos = len(arquivos_foto)
    print("\n" + "=" * 60)
    print(f"🚀 INICIANDO AUDITORIA REAL ({total_fotos} foto{'s' if total_fotos > 1 else ''} no dataset/)")
    print(f"🤖 Modelo Gemini: {gemini_service.model_name} (Zero Mocks)")
    print("=" * 60 + "\n")

    sucessos = 0
    falhas = 0

    for idx, img_path in enumerate(arquivos_foto, 1):
        nome_arquivo = img_path.name
        json_target = RESULTADOS_DIR / f"{nome_arquivo}.json"

        # PASSO 1: Ignora fotos já auditadas anteriormente
        if json_target.exists():
            print(f"[{idx}/{total_fotos}] ⏭️ [Ignorando] {nome_arquivo} (já auditado anteriormente)")
            sucessos += 1
            continue

        print(f"[{idx}/{total_fotos}] Processando: {nome_arquivo}...")

        try:
            with open(img_path, "rb") as f:
                img_bytes = f.read()

            if not img_bytes:
                print(f"⚠️ Imagem vazia ou corrompida: {nome_arquivo}")
                falhas += 1
                continue

            # Detecta tipo MIME
            mime_type = mimetypes.guess_type(str(img_path))[0] or "image/jpeg"

            # 1. Pipeline de Visão (Gatekeeper + Perito Setorial)
            analise: GeminiAuditoriaOutput = gemini_service.analisar_foto(img_bytes, mime_type=mime_type)

            # 2. Avaliador Determinístico
            resultado = analise.resultado_avaliacao or calcular_nota(analise)

            # 3. Formatação do Relatório Terminal Legível
            gatekeeper_status = "Aprovado" if analise.status_analise != StatusAnalise.NAO_VERIFICAVEL else "Rejeitado"
            confianca_pct = int(round(analise.nivel_confianca * 100))
            acao_imediata_str = "Sim" if resultado.requer_acao_imediata else "Não"
            qtd_corrigiveis = len(resultado.itens_corrigiveis)

            print("========================================================")
            print(f"📸 Foto: {nome_arquivo}")
            print(f"🛡️ Gatekeeper: {gatekeeper_status} | Setor: {analise.setor_identificado} | Confiança: {confianca_pct}%")
            print("🔍 Evidências Visuais Reais:")
            if analise.evidencias:
                for ev in analise.evidencias:
                    conforme_str = "Sim" if ev.conforme else "Não"
                    sev_str = ev.severidade.value if ev.severidade else "BAIXA"
                    criterio_fmt = ev.criterio.replace("_", " ").title()
                    print(f"   - {criterio_fmt}: {ev.descricao} (Conforme: {conforme_str} | Severidade: {sev_str})")
            else:
                motivo = analise.acoes_recomendadas[0] if analise.acoes_recomendadas else "Foto rejeitada na triagem inicial"
                print(f"   - {motivo}")

            print(f"⚖️ Nota Calculada: {resultado.nota_calculada:.1f} / 10.0")
            print(f"🚨 Ação Imediata: {acao_imediata_str}")
            print(f"🎫 Itens Pendentes de Correção: {qtd_corrigiveis}")
            print("========================================================\n")

            # 4. Salvar laudo estruturado em dataset/resultados/[nome_do_arquivo].json
            laudo_estruturado = {
                "arquivo": nome_arquivo,
                "caminho_origem": str(img_path.resolve()),
                "data_auditoria": datetime.now().isoformat(),
                "modelo_ia": gemini_service.model_name,
                "gatekeeper": {
                    "status": gatekeeper_status,
                    "aprovado": (gatekeeper_status == "Aprovado"),
                    "setor_detectado": analise.setor_identificado,
                    "confianca_percentual": confianca_pct,
                },
                "perito_setorial": {
                    "setor": analise.setor_identificado,
                    "status_analise": analise.status_analise.value,
                    "nivel_confianca": analise.nivel_confianca,
                    "requer_revisao": analise.requer_revisao,
                    "elementos_visuais_detectados": analise.elementos_visuais_detectados,
                    "evidencias": [ev.model_dump() for ev in analise.evidencias],
                    "acoes_recomendadas": analise.acoes_recomendadas,
                },
                "avaliacao_deterministica": {
                    "setor": resultado.setor,
                    "nota_calculada": resultado.nota_calculada,
                    "status_analise": resultado.status_analise.value,
                    "total_criterios_avaliados": resultado.total_criterios_avaliados,
                    "criterios_conformes": resultado.criterios_conformes,
                    "requer_revisao": resultado.requer_revisao,
                    "requer_acao_imediata": resultado.requer_acao_imediata,
                    "itens_corrigiveis": [it.model_dump() for it in resultado.itens_corrigiveis],
                    "resumo_operacional": resultado.resumo_operacional,
                },
            }

            # Salva como [nome_do_arquivo].json (ex: foto1.jpg.json) e [nome_sem_extensao].json
            with open(json_target, "w", encoding="utf-8") as f_json:
                json.dump(laudo_estruturado, f_json, indent=2, ensure_ascii=False)

            if img_path.stem != nome_arquivo:
                json_target_stem = RESULTADOS_DIR / f"{img_path.stem}.json"
                with open(json_target_stem, "w", encoding="utf-8") as f_json_stem:
                    json.dump(laudo_estruturado, f_json_stem, indent=2, ensure_ascii=False)

            sucessos += 1

        except Exception as e:
            logger.exception(f"Erro ao auditar foto real '{nome_arquivo}': {e}")
            print(f"❌ Erro ao auditar {nome_arquivo}: {e}\n")
            falhas += 1

        finally:
            if idx < total_fotos:
                time.sleep(12)

    print("=" * 80)
    print(f"🏁 AUDITORIA FINALIZADA: {sucessos} concluída(s), {falhas} falha(s).")
    print(f"📁 Laudos salvos em: {RESULTADOS_DIR.resolve()}")
    print("=" * 80 + "\n")

    # Tabela Consolidada de todas as fotos
    print("=" * 80)
    print(f"{'Arquivo':<42} | {'Setor':<18} | {'Nota':<6} | {'Falhas':<6}")
    print("-" * 80)
    for img_path in arquivos_foto:
        nome_arq = img_path.name
        j_path = RESULTADOS_DIR / f"{nome_arq}.json"
        if j_path.exists():
            try:
                with open(j_path, "r", encoding="utf-8") as f_j:
                    d = json.load(f_j)
                s = d.get("avaliacao_deterministica", {}).get("setor") or d.get("gatekeeper", {}).get("setor_detectado", "N/A")
                n = f"{d.get('avaliacao_deterministica', {}).get('nota_calculada', 0.0):.1f}"
                qtd_f = str(len(d.get("avaliacao_deterministica", {}).get("itens_corrigiveis", [])))
                disp_nome = (nome_arq[:39] + "...") if len(nome_arq) > 42 else nome_arq
                print(f"{disp_nome:<42} | {s:<18} | {n:<6} | {qtd_f:<6}")
            except Exception:
                print(f"{nome_arq[:39]:<42} | {'ERRO LEITURA':<18} | {'-':<6} | {'-':<6}")
        else:
            print(f"{nome_arq[:39]:<42} | {'PENDENTE':<18} | {'-':<6} | {'-':<6}")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    auditar_dataset()
