"""
Script utilitário para inicialização, provisionamento e conexão do WhatsApp na Evolution API v2.
- Cria a instância 'conveniencia' caso inexistente.
- Configura o webhook para receber fotos e mensagens do grupo.
- Gera o QR Code e abre automaticamente no navegador para leitura rápida.
- Monitora a conexão até confirmação do pareamento.
"""

import asyncio
import os
import sys
import tempfile
import webbrowser
from typing import Optional
import httpx

# Garante suporte a UTF-8 no stdout e stderr do console Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from config import settings


class WhatsAppManager:
    """Gerenciador de instância e conexão com a Evolution API v2."""

    def __init__(self) -> None:
        self.base_url = settings.EVOLUTION_API_URL.rstrip("/")
        self.api_key = settings.EVOLUTION_API_KEY
        self.instance = settings.EVOLUTION_INSTANCE_NAME
        self.headers = {
            "apikey": self.api_key,
            "Content-Type": "application/json",
        }

    async def wait_for_api_ready(self, max_retries: int = 15, delay: float = 2.0) -> bool:
        """Aguarda a Evolution API em http://localhost:8080 responder."""
        print(f"[*] Aguardando a Evolution API em {self.base_url} responder...")
        async with httpx.AsyncClient(timeout=10.0) as client:
            for attempt in range(1, max_retries + 1):
                try:
                    res = await client.get(self.base_url)
                    if res.status_code in (200, 404):
                        print("[OK] Evolution API pronta e aceitando conexoes!")
                        return True
                except (httpx.ConnectError, httpx.RemoteProtocolError):
                    pass
                await asyncio.sleep(delay)
                print(f"   ... tentativa {attempt}/{max_retries}")
        print("[ERRO] Nao foi possivel conectar a Evolution API apos varias tentativas.")
        return False

    async def check_instance_exists(self) -> bool:
        """Verifica se a instância já foi provisionada."""
        endpoint = f"{self.base_url}/instance/fetchInstances"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.get(endpoint, headers=self.headers)
                if res.status_code == 200:
                    data = res.json()
                    instances = data if isinstance(data, list) else data.get("instances", [])
                    for inst in instances:
                        name = (
                            inst.get("name")
                            or inst.get("instanceName")
                            or inst.get("instance", {}).get("instanceName")
                        )
                        if name == self.instance:
                            return True
            except Exception as e:
                print(f"[AVISO] Erro ao consultar instancias: {e}")
        return False

    async def create_instance(self) -> bool:
        """Cria a instância com driver WhatsApp-Baileys na Evolution API v2."""
        print(f"[*] Provisionando nova instancia '{self.instance}'...")
        endpoint = f"{self.base_url}/instance/create"
        payload = {
            "instanceName": self.instance,
            "token": self.api_key,
            "qrcode": True,
            "integration": "WHATSAPP-BAILEYS",
            "rejectCall": False,
            "msgCall": "Chamadas nao suportadas neste canal operacional.",
            "groupsIgnore": False,
            "alwaysOnline": True,
            "readMessages": True,
            "readStatus": False,
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                res = await client.post(endpoint, json=payload, headers=self.headers)
                if res.status_code in (200, 201):
                    print(f"[OK] Instancia '{self.instance}' criada com sucesso!")
                    return True
                else:
                    print(f"[AVISO] Resposta da criacao: HTTP {res.status_code} - {res.text}")
                    return False
            except Exception as e:
                print(f"[ERRO] Falha ao criar instancia: {e}")
                return False

    async def configure_webhook(self) -> None:
        """Configura o webhook para encaminhar fotos e eventos de mensagens para o FastAPI."""
        print(f"[*] Configurando Webhook da instancia '{self.instance}'...")
        endpoint = f"{self.base_url}/webhook/set/{self.instance}"
        payload = {
            "webhook": {
                "enabled": True,
                "url": "http://host.docker.internal:8000/webhook/evolution",
                "webhookByEvents": False,
                "webhookBase64": True,
                "events": [
                    "MESSAGES_UPSERT",
                    "CONNECTION_UPDATE",
                ],
            }
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.post(endpoint, json=payload, headers=self.headers)
                if res.status_code in (200, 201):
                    print("[OK] Webhook configurado com sucesso (http://host.docker.internal:8000/webhook/evolution)!")
                else:
                    print(f"[AVISO] Falha ao configurar webhook: {res.status_code} - {res.text}")
            except Exception as e:
                print(f"[AVISO] Erro ao definir webhook: {e}")

    async def get_connection_state(self) -> str:
        """Retorna o estado atual da conexão ('open', 'connecting', 'close')."""
        endpoint = f"{self.base_url}/instance/connectionState/{self.instance}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.get(endpoint, headers=self.headers)
                if res.status_code == 200:
                    data = res.json()
                    instance_data = data.get("instance", data)
                    return instance_data.get("state", "close")
            except Exception:
                pass
        return "close"

    async def connect_and_display_qr(self) -> None:
        """Solicita o QR Code de autenticação e abre no navegador."""
        print(f"[*] Gerando QR Code para pareamento do WhatsApp na instancia '{self.instance}'...")
        endpoint = f"{self.base_url}/instance/connect/{self.instance}"

        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                res = await client.get(endpoint, headers=self.headers)
                if res.status_code != 200:
                    print(f"[ERRO] Erro ao solicitar conexao: {res.status_code} - {res.text}")
                    return

                data = res.json()
                b64_qr = data.get("base64")
                pairing_code = data.get("pairingCode")

                if b64_qr:
                    if not b64_qr.startswith("data:image"):
                        b64_qr = f"data:image/png;base64,{b64_qr}"

                    html_content = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <title>Conectar WhatsApp - Loja de Conveniencia</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f172a;
      color: #f8fafc;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      margin: 0;
    }}
    .card {{
      background: #1e293b;
      padding: 32px 40px;
      border-radius: 16px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.5);
      text-align: center;
      max-width: 440px;
      border: 1px solid #334155;
    }}
    h1 {{ font-size: 20px; margin-bottom: 8px; color: #38bdf8; }}
    p {{ color: #94a3b8; font-size: 14px; margin-bottom: 24px; line-height: 1.5; }}
    .qr-box {{
      background: #ffffff;
      padding: 16px;
      border-radius: 12px;
      display: inline-block;
      margin-bottom: 20px;
    }}
    img {{ width: 280px; height: 280px; display: block; }}
    .instructions {{
      font-size: 13px;
      color: #cbd5e1;
      text-align: left;
      background: #0f172a;
      padding: 14px 18px;
      border-radius: 8px;
    }}
    .instructions ol {{ margin: 0; padding-left: 20px; }}
    .instructions li {{ margin-bottom: 6px; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Conectar WhatsApp</h1>
    <p>Escaneie o QR Code abaixo com o aparelho que gerenciara as auditorias das lojas.</p>
    <div class="qr-box">
      <img src="{b64_qr}" alt="QR Code WhatsApp" />
    </div>
    <div class="instructions">
      <ol>
        <li>Abra o WhatsApp no celular</li>
        <li>Toque em <b>Mais opcoes</b> ou <b>Configuracoes</b></li>
        <li>Selecione <b>Aparelhos conectados</b> &gt; <b>Conectar aparelho</b></li>
        <li>Aponte a camera para esta tela</li>
      </ol>
    </div>
  </div>
</body>
</html>
"""
                    temp_html = os.path.join(tempfile.gettempdir(), "evolution_qrcode.html")
                    with open(temp_html, "w", encoding="utf-8") as f:
                        f.write(html_content)

                    print(f"\n[OK] Abrindo QR Code no seu navegador: file://{temp_html}")
                    webbrowser.open(f"file://{temp_html}")
                elif pairing_code:
                    print(f"[CHAVE] Codigo de pareamento numerico: {pairing_code}")
                else:
                    print("[INFO] Resposta da conexao recebida.")

            except Exception as e:
                print(f"[ERRO] Excecao ao conectar: {e}")

    async def monitor_connection(self, max_wait_seconds: int = 60) -> None:
        """Aguarda a leitura do QR Code pelo usuário."""
        print("\n[*] Aguardando leitura do QR Code no WhatsApp (timeout: 60s)...")
        for elapsed in range(0, max_wait_seconds, 3):
            state = await self.get_connection_state()
            if state == "open":
                print("\n" + "=" * 60)
                print("[SUCESSO] WhatsApp CONECTADO COM SUCESSO!")
                print("   Instancia 'conveniencia' pronta para receber auditorias!")
                print("=" * 60)
                return
            await asyncio.sleep(3)
            print(f"   ... status atual: '{state}' ({elapsed}s)")

        print("[AVISO] Tempo limite de espera atingido. Se nao conseguiu escanear, execute o script novamente.")


async def main():
    print("\n" + "=" * 60)
    print("INICIALIZADOR WHATSAPP - EVOLUTION API v2")
    print("=" * 60)

    manager = WhatsAppManager()

    # 1. Espera a API responder
    ready = await manager.wait_for_api_ready()
    if not ready:
        return

    # 2. Cria a instância se não existir
    exists = await manager.check_instance_exists()
    if not exists:
        created = await manager.create_instance()
        if not created:
            return
    else:
        print(f"[INFO] Instancia '{manager.instance}' ja existe na Evolution API.")

    # 3. Configura o webhook
    await manager.configure_webhook()

    # 4. Verifica se já está conectado
    state = await manager.get_connection_state()
    if state == "open":
        print(f"[OK] Instancia '{manager.instance}' ja esta conectada e online no WhatsApp!")
        return

    # 5. Gera e exibe o QR Code
    await manager.connect_and_display_qr()

    # 6. Monitora o pareamento
    await manager.monitor_connection()


if __name__ == "__main__":
    asyncio.run(main())
