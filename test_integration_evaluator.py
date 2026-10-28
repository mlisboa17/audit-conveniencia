"""
Teste de Integração de Ponta a Ponta para o Evaluator Service (Passos 2 e 3).
Valida:
1. upload_auditoria_live (/api/upload-auditoria/{store_id}):
   - Execução do mock do Gemini com geração de GeminiAuditoriaOutput
   - Cálculo determinístico via calcular_nota
   - Retorno dos campos 'avaliacao', 'itens_corrigiveis', 'resumo_operacional', 'requer_acao_imediata'
2. Webhook da Evolution API (/webhook/evolution):
   - Recepção de foto no grupo
   - Integração do evaluator determinístico
   - Persistência no SQLite com 'avaliacao' presente no ai_analysis
"""

import asyncio
import io
import json
import logging
from datetime import date, datetime

from httpx import ASGITransport, AsyncClient
from main import app
from database import init_db, async_session_factory, get_store_by_id, Audit
from sqlalchemy import select

logging.basicConfig(level=logging.INFO, format="%(message)s")


async def test_pwa_upload_live_integration():
    print("\n--- [Teste Integração 1] PWA Upload ao Vivo com Evaluator Service ---")
    await init_db()
    async with async_session_factory() as session:
        from sqlalchemy import text
        await session.execute(text("DELETE FROM audit_tickets"))
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Imagem simulada (dummy jpeg bytes com tamanho > 3000 para acionar o mock)
        dummy_image = b"\xff\xd8\xff" + b"0" * 4000 + b"\xff\xd9"
        files = {
            "file": ("foto_balcao.jpg", dummy_image, "image/jpeg"),
        }
        data = {
            "operator_name": "Juliana Teste",
        }

        response = await client.post("/api/upload-auditoria/1", files=files, data=data)
        assert response.status_code == 200, f"Status code {response.status_code}: {response.text}"
        res = response.json()

        # Validações dos campos clássicos
        assert res["status"] in ("OK", "ALERT")
        assert "score" in res
        assert 0.0 <= res["score"] <= 10.0
        assert res["operator"] in ("Juliana", "Juliana Teste")

        # Validações do Evaluator Service (Passo 2 e 3)
        assert "avaliacao" in res, "Campo 'avaliacao' deve estar presente no retorno"
        avaliacao = res["avaliacao"]
        assert isinstance(avaliacao, dict)
        assert "nota_calculada" in avaliacao
        assert "total_criterios_avaliados" in avaliacao
        assert "criterios_conformes" in avaliacao
        assert "resumo_operacional" in avaliacao
        assert "itens_corrigiveis" in avaliacao
        assert "requer_acao_imediata" in avaliacao

        # Validações dos atalhos de alto nível
        assert res["resumo_operacional"] == avaliacao["resumo_operacional"]
        assert res["requer_acao_imediata"] == avaliacao["requer_acao_imediata"]
        assert len(res["itens_corrigiveis"]) == len(avaliacao["itens_corrigiveis"])

        print(f"  [OK] Rota PWA validada: Score={res['score']} | Setor={avaliacao['setor']} | Resumo={res['resumo_operacional']}")


async def test_webhook_evolution_evaluator_integration():
    print("\n--- [Teste Integração 2] Webhook Evolution com Avaliação Determinística ---")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Payload de webhook messages.upsert com imagem não encaminhada (anti-fraude ok)
        dummy_media_bytes = b"0" * 4000
        import base64
        b64_str = base64.b64encode(dummy_media_bytes).decode("ascii")

        webhook_payload = {
            "event": "messages.upsert",
            "instance": "AuditConveniencia",
            "data": {
                "key": {
                    "remoteJid": "120363430718119886@g.us",
                    "fromMe": False,
                    "id": "MSG_EVALUATOR_TEST_01"
                },
                "message": {
                    "imageMessage": {
                        "caption": "Auditoria Freezer Feita por Marcos T1",
                        "mimetype": "image/jpeg",
                        "base64": b64_str,
                        "contextInfo": {
                            "isForwarded": False,
                            "forwardingScore": 0
                        }
                    }
                }
            }
        }

        response = await client.post("/webhook/evolution", json=webhook_payload)
        assert response.status_code == 200, f"Webhook falhou: {response.text}"

        # Verifica no banco de dados se a auditoria foi gravada com 'avaliacao' em ai_analysis
        async with async_session_factory() as session:
            stmt = select(Audit).where(Audit.store_id == 1).order_by(Audit.id.desc()).limit(1)
            result = await session.execute(stmt)
            last_audit = result.scalar_one_or_none()
            assert last_audit is not None, "Auditoria não foi salva no banco"

            parsed_ai = json.loads(last_audit.ai_analysis)
            assert "avaliacao" in parsed_ai, "ai_analysis no banco deve conter 'avaliacao'"
            assert "gemini_output" in parsed_ai, "ai_analysis no banco deve conter 'gemini_output'"
            assert parsed_ai["avaliacao"]["total_criterios_avaliados"] > 0
            print(f"  [OK] Auditoria salva no SQLite com ai_analysis contendo avaliação determinística completa.")


async def main():
    await test_pwa_upload_live_integration()
    await test_webhook_evolution_evaluator_integration()
    print("\n" + "=" * 65)
    print("[SUCESSO] TESTES DE INTEGRAÇÃO DO EVALUATOR SERVICE PASSARAM!")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
