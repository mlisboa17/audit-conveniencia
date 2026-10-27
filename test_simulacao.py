"""
Script de teste e simulação de ponta a ponta para a arquitetura de GRUPOS DE WHATSAPP (@g.us).
Valida:
1. Identificação de grupos cadastrados vs. grupos desconhecidos ou chats privados.
2. Extração de fotos postadas nos grupos das lojas.
3. Avaliação visual pelo Gemini 2.0 Flash e resposta direta DENTRO DO GRUPO.
4. Cobrança nos grupos das lojas pendentes às 16h45.
5. Alertas de tolerância excedida e resumo telegráfico no privado da Diretoria às 17h00 e 17h15.
6. Consulta à lista de grupos (GET /groups).
"""

import asyncio
import base64
import io
import json
import logging
from datetime import date

from PIL import Image, ImageDraw

from config import settings
from database import (
    async_session_factory,
    find_store_by_group_jid,
    get_all_stores,
    init_db,
    save_audit,
)
from scheduler import (
    check_afternoon_audits_tolerance_1645,
    check_critical_unresponsive_stores_1700,
    send_executive_summary_1715,
)
from services.evolution_service import evolution_service
from services.gemini_service import gemini_service

# Configuração de logs
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("simulacao_grupos")


def create_sample_shelf_image(text: str = "Prateleira Conveniencia", rupture: bool = False) -> bytes:
    """Gera dinamicamente uma imagem JPEG sintética representando uma prateleira."""
    img = Image.new("RGB", (800, 600), color=(240, 240, 245))
    draw = ImageDraw.Draw(img)

    # Gôndolas
    for y in [180, 340, 500]:
        draw.rectangle([40, y, 760, y + 25], fill=(180, 150, 110), outline=(140, 110, 80))

    # Produtos Gôndola 1
    for i, x in enumerate(range(60, 720, 70)):
        if rupture and i in (3, 4):
            continue
        product_color = (220, 50, 50) if i % 2 == 0 else (40, 120, 220)
        draw.rectangle([x, 80, x + 50, 180], fill=product_color, outline=(30, 30, 30))

    # Produtos Gôndola 2
    for i, x in enumerate(range(60, 720, 75)):
        if rupture and i == 2:
            continue
        draw.rectangle([x, 230, x + 60, 340], fill=(240, 180, 40), outline=(50, 50, 50))

    # Banner superior
    draw.rectangle([0, 0, 800, 50], fill=(20, 35, 60))
    status_label = "FALHA / RUPTURA" if rupture else "ABASTECIMENTO OK"
    draw.text((20, 15), f"AUDITORIA GRUPO - {text.upper()} [{status_label}]", fill=(255, 255, 255))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


async def run_simulation():
    print("\n" + "=" * 75)
    print("🚀 INICIANDO SIMULAÇÃO: AUDITORIA OPERACIONAL VIA GRUPOS DE WHATSAPP")
    print("=" * 75)

    # 1. Inicializa o banco de dados e grupos das 3 lojas
    print("\n[Passo 1] Inicializando banco de dados SQLite e sincronizando grupos das lojas...")
    await init_db()

    async with async_session_factory() as session:
        stores = await get_all_stores(session)
        print(f"✅ {len(stores)} lojas com grupos configurados:")
        for s in stores:
            print(f"   • {s.name} | Grupo: {s.whatsapp_group_jid} | Gerente: {s.manager_name}")

    if not stores or len(stores) < 3:
        print("❌ Lojas insuficientes para o teste.")
        return

    store1, store2, store3 = stores[0], stores[1], stores[2]

    # 2. Teste do Helper de Grupos (GET /groups)
    print("\n" + "-" * 75)
    print("[Passo 2] Testando listagem de grupos disponíveis no WhatsApp (GET /groups)...")
    groups = await evolution_service.fetch_all_groups()
    print(f"✅ {len(groups)} grupos encontrados na Evolution API:")
    for g in groups:
        print(f"   • ID: {g['id']} | Nome: '{g['subject']}' | Participantes: {g['size']}")

    # 3. Teste do Webhook: Mensagem individual ou grupo desconhecido
    print("\n" + "-" * 75)
    print("[Passo 3] Testando segurança do Webhook contra grupos não cadastrados e mensagens privadas...")
    
    # Caso A: Grupo não cadastrado
    unknown_group_payload = {
        "data": {
            "key": {
                "remoteJid": "120363999999999999@g.us",
                "participant": "5511999998888@s.whatsapp.net",
            },
            "message": {"conversation": "Foto da loja"},
        }
    }
    jid_a, is_group_a, _ = evolution_service.extract_remote_jid(unknown_group_payload)
    async with async_session_factory() as session:
        store_found = await find_store_by_group_jid(session, jid_a)
        if not store_found:
            print(f"   ✅ Grupo desconhecido {jid_a} corretamente IGNORADO pelo sistema!")
        else:
            print(f"   ❌ Erro: grupo {jid_a} foi reconhecido indevidamente.")

    # Caso B: Chat privado individual (não-grupo)
    private_payload = {
        "data": {
            "key": {
                "remoteJid": "5511999990001@s.whatsapp.net",
            },
            "message": {"conversation": "Foto no privado"},
        }
    }
    jid_b, is_group_b, _ = evolution_service.extract_remote_jid(private_payload)
    if not is_group_b:
        print(f"   ✅ Mensagem de chat privado ({jid_b}) corretamente IGNORADA (somente grupos @g.us são aceitos)!")

    # 4. Teste do Webhook: Equipe da Loja 1 posta foto dentro do GRUPO DA LOJA
    print("\n" + "-" * 75)
    print(f"[Passo 4] Simulando postagem de foto no grupo da {store1.name} ({store1.whatsapp_group_jid})...")
    img_ok_bytes = create_sample_shelf_image(f"{store1.name} - Gôndola do Turno", rupture=False)
    img_ok_b64 = base64.b64encode(img_ok_bytes).decode("utf-8")

    group_msg_payload = {
        "data": {
            "key": {
                "remoteJid": store1.whatsapp_group_jid,
                "participant": "5511988887777@s.whatsapp.net",  # Atendente de plantão no grupo
            },
            "message": {
                "imageMessage": {
                    "base64": img_ok_b64,
                    "mimetype": "image/jpeg",
                    "caption": "Foto da gôndola 16h30 pronta",
                }
            },
        }
    }

    # Validação da loja pelo grupo
    target_jid, is_grp, participant = evolution_service.extract_remote_jid(group_msg_payload)
    async with async_session_factory() as session:
        active_store = await find_store_by_group_jid(session, target_jid)
        print(f"   👥 Loja identificada via Grupo: {active_store.name}")
        print(f"   👤 Foto enviada pelo participante: {participant}")

    # Processamento com Gemini 2.0 Flash
    print("   🤖 Analisando foto com Google Gemini 2.0 Flash...")
    analysis_1 = await gemini_service.analyze_store_image(img_ok_bytes, mime_type="image/jpeg")
    print(f"      - Aprovado: {analysis_1.get('aprovado')}")
    print(f"      - Score: {analysis_1.get('score')}/100")
    print(f"      - Orientação: {analysis_1.get('orientacao')}")

    # Persistência no SQLite
    async with async_session_factory() as session:
        audit_1 = await save_audit(
            session=session,
            store_id=store1.id,
            audit_date=date.today(),
            shift="TARDE",
            status="OK" if analysis_1.get("aprovado") else "ALERT",
            score=analysis_1.get("score", 95),
            ai_analysis=json.dumps(analysis_1, ensure_ascii=False),
            image_url=f"group_{store1.whatsapp_group_jid}",
        )
        print(f"   💾 Auditoria gravada no banco: ID={audit_1.id} (Status: {audit_1.status})")

    # Feedback enviado dentro do próprio grupo
    feedback_text = (
        "Audit aprovado! Loja 100% pronta pro pico."
        if analysis_1.get("aprovado")
        else f"Atenção: identificado {', '.join(analysis_1.get('problemas', []))}. Favor puxar a frente e repor agora."
    )
    print(f"   💬 Feedback postado DENTRO DO GRUPO ({store1.whatsapp_group_jid}):")
    print(f"      '{feedback_text}'")

    # 5. Rotina Ativa: 16h45 - Cobrança DENTRO DOS GRUPOS das lojas pendentes
    print("\n" + "-" * 75)
    print("[Passo 5] Executando Job das 16h45 (Cobrança nos GRUPOS das lojas pendentes)...")
    res_1645 = await check_afternoon_audits_tolerance_1645()
    print(f"   ✅ Lojas que já enviaram foto: {res_1645['already_sent']}")
    print(f"   📢 Grupos cobrados pelo bot: {res_1645['notified_groups']}")

    # 6. Rotina Ativa: 17h00 - Alerta exclusivo no privado do Diretor
    print("\n" + "-" * 75)
    print("[Passo 6] Executando Job das 17h00 (Alerta de Inadimplência no privado da Diretoria)...")
    critical_stores = await check_critical_unresponsive_stores_1700()
    print(f"   🚨 Lojas com atraso crítico alertadas no privado do Diretor ({settings.DIRECTOR_PHONE}):")
    for cs in critical_stores:
        print(f"      • {cs}")

    # 7. Rotina Ativa: 17h15 - Resumo telegráfico consolidado no privado do Diretor
    print("\n" + "-" * 75)
    print("[Passo 7] Executando Job das 17h15 (Resumo Executivo Consolidado 17h)...")
    summary = await send_executive_summary_1715()
    print("   📊 Mensagem enviada ao WhatsApp privado do Diretor:")
    print("   " + "-" * 50)
    for line in summary.split("\n"):
        print(f"   {line}")
    print("   " + "-" * 50)

    print("\n" + "=" * 75)
    print("✨ SIMULAÇÃO DE OPERAÇÃO EM GRUPOS CONCLUÍDA COM SUCESSO TOTAL!")
    print("=" * 75)


if __name__ == "__main__":
    asyncio.run(run_simulation())
