# 🏪 Audit Conveniência - Micro-serviço Backend com Anti-Fraude & IA

Micro-serviço backend em Python para auditoria operacional diária de lojas de conveniência via **Grupos de WhatsApp (Evolution API)**, **Captura ao Vivo via PWA (trava de sensor de câmera)** e visão computacional multimodal (**Google Gemini 2.0 Flash**).

---

## 🛡️ Travas Anti-Fraude Implementadas

1. **Bloqueio de Fotos Encaminhadas no WhatsApp:**
   - No webhook `messages.upsert`, o sistema analisa `contextInfo.isForwarded` e `contextInfo.forwardingScore`.
   - Se a foto foi encaminhada da galeria ou de outro chat, ela é **recusada imediatamente**.
   - O bot responde no grupo:
     > *"⚠️ Foto Recusada: Não são permitidas fotos encaminhadas da galeria. Por favor, tire a foto diretamente pela câmera da loja."*

2. **Validação Estrita de Objeto e Ambiente (Gemini 2.0 Flash):**
   - Regra prioritária de barreira no System Prompt do auditor.
   - A IA exige que a imagem retrate o setor comercial interno da conveniência (geladeiras, freezers, gôndolas, prateleiras ou balcão).
   - Se for enviada uma imagem irrelevante (selfie, meme, documento, comida no prato, chão, teto ou rua), a IA retorna `status: "INVALID_OBJECT"`, nota `0` e o sistema **não contabiliza a auditoria como cumprida no dia**.
   - Resposta automática no grupo:
     > *"❌ Foto Inválida: O sistema identificou que a imagem não pertence a uma área da conveniência (geladeiras, gôndolas ou balcão). Por favor, envie uma foto válida do setor auditado."*

3. **Janela Flexível de Auditoria (06:00 às 22:00):**
   - O sistema aceita e processa auditorias válidas a qualquer momento do dia (Turnos: Manhã, Tarde e Noite).
   - O agendador (APScheduler) às 16h45 verifica se a loja já registrou ao menos uma auditoria válida hoje (`status in ('OK', 'ALERT')`). Se não houver, dispara lembrete amigável no grupo da loja.
   - Às 17h00, dispara alerta à Diretoria caso a loja permaneça sem auditoria.
   - Às 17h15, envia o resumo consolidado no WhatsApp privado do Diretor.

4. **Web App PWA de Captura ao Vivo com Trava de Câmera:**
   - Rota: `GET http://localhost:8000/capturar/{store_id}`
   - Interface web mobile moderna e responsiva para os atendentes das lojas.
   - Utiliza `<input type="file" accept="image/*" capture="environment">` para travar o acionamento direto do sensor da **câmera traseira** do celular, sem permitir selecionar imagens antigas da galeria.
   - Upload automático via `POST /api/upload-auditoria/{store_id}` com análise em tempo real do Gemini e notificação instantânea no grupo do WhatsApp da loja.

---

## 🛠️ Stack Tecnológica

- **Linguagem:** Python 3.11+
- **Framework Web:** FastAPI + Uvicorn
- **Inteligência Artificial:** Google Gemini 2.0 Flash (`google-genai`)
- **WhatsApp Gateway:** Evolution API v2 (`evoapicloud/evolution-api:v2.3.0` + PostgreSQL 15 + Redis 7)
- **Persistência:** SQLite local (`app.db`) com SQLAlchemy 2.0 e `aiosqlite`
- **Agendador:** APScheduler (AsyncIOScheduler integrado ao event loop)
- **Configurações:** Pydantic Settings (`pydantic-settings`) carregadas via `.env`

---

## 📱 Links e Endpoints Principais

- **Swagger / OpenAPI:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **PWA de Captura ao Vivo Loja 1:** [http://localhost:8000/capturar/1](http://localhost:8000/capturar/1)
- **PWA de Captura ao Vivo Loja 2:** [http://localhost:8000/capturar/2](http://localhost:8000/capturar/2)
- **PWA de Captura ao Vivo Loja 3:** [http://localhost:8000/capturar/3](http://localhost:8000/capturar/3)
- **Mensagem de Teste no Grupo Loja 1:** `GET http://localhost:8000/test-group`
- **Listar Grupos do WhatsApp do Bot:** `GET http://localhost:8000/groups`
- **Health Check Operacional:** `GET http://localhost:8000/health`
