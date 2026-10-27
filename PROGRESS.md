# 🚀 Checkpoint de Progresso: Auditoria Operacional de Lojas de Conveniência

**Data do Checkpoint:** 27 de Outubro de 2026  
**Status Geral:** ✅ Fase 1 (Estrutura Base, Calibração, Gamificação e Anti-Fraude) Concluída com Sucesso  
**Servidor Backend:** FastAPI + Uvicorn ativo na porta `8000` (`http://localhost:8000`)  
**Instância WhatsApp:** Conectada via Evolution API v2.3.0 (`conveniencia`)  
**Banco de Dados:** SQLite local assíncrono (`app.db`) com migrações automáticas ativas  

---

## 1. 🏗️ Infraestrutura e Containers (`docker-compose.yml`)

- **Evolution API v2.3.0 (`evolution_api_conveniencia`):**
  - Porta `8080`, autenticação via API Key global (`AUTHENTICATION_API_KEY=audit_lojas_secret_2026`).
  - Instância `conveniencia` conectada ao WhatsApp e sincronizada.
  - Webhook operacional apontado para `http://host.docker.internal:8000/webhook/evolution`.
- **PostgreSQL 15 Alpine (`evolution_postgres_conveniencia`):**
  - Persistência das instâncias, sessões e metadados da Evolution API (porta interna `5432`).
- **Redis 7.0 Alpine (`evolution_redis_conveniencia`):**
  - Gerenciamento de filas de mensagens, cache de sessões e rate limit (porta interna `6379`).

---

## 2. 📱 Webhook & Mensageria WhatsApp (`main.py` e `services/evolution_service.py`)

- **Filtragem de Eventos Operacionais:**
  - Tratamento inteligente do campo `event`: eventos do ciclo de vida como `connection.update` são processados imediatamente com HTTP 200, registrando status em log e evitando disparos espúrios.
  - Processamento de mensagens focado no evento `messages.upsert`.
- **Anti-Loop e Prevenção de Auto-Mensagens:**
  - Verificação rigorosa de `key.fromMe == True` para descartar qualquer mensagem gerada pelo próprio bot.
- **Roteamento por Grupos de Lojas (`@g.us`):**
  - O sistema opera primariamente em grupos de WhatsApp das lojas (terminados em `@g.us`).
  - Mensagens de grupos não cadastrados ou mensagens em chats privados (não-diretoria) são ignoradas com segurança.
  - Grupo de teste real amarrado à Loja 1: `120363430718119886@g.us`.
- **Trava Anti-Fraude (WhatsApp):**
  - Inspeção dos campos `contextInfo.isForwarded` e `contextInfo.forwardingScore`.
  - Imagens encaminhadas da galeria ou repassadas de outros grupos são **recusadas imediatamente** com feedback orientador no grupo, solicitando que o atendente tire a foto diretamente na loja.
- **Comando Interativo `!ranking`:**
  - Qualquer colaborador no grupo pode enviar a mensagem `!ranking` para receber instantaneamente o placar das 3 lojas, o turno líder e os melhores operadores com formatação rica em emojis.

---

## 3. 👁️ Inteligência Artificial & Visão Computacional (`services/gemini_service.py`)

- **Modelo Multimodal:** Google Gemini 2.0 Flash (`gemini-2.0-flash`).
- **Calibração de Baixa Criatividade:**
  - `temperature=0.1` configurada para foco estrito na realidade visual física da loja, evitando deduções ou invenções.
- **Raciocínio Visual Obrigatório Passo a Passo (Chain of Thought):**
  - **PASSO 1:** Extração e listagem explícita dos objetos físicos visíveis no campo preliminar `"elementos_visuais_detectados"` (ex: *'cervejeira vertical vermelha Itaipava'*, *'cervejeira amarela Skol'*, *'latas de cerveja'*, *'garrafas pet de refrigerante'*).
  - **PASSO 2:** Classificação do setor baseada estritamente nos objetos físicos detectados no Passo 1.
- **Regra Inegociável de Classificação de Setor:**
  - Se a imagem contiver cervejeiras, geladeiras verticais, portas de vidro com bebidas ou freezers horizontais, o setor **OBRIGATORIAMENTE** deve ser classificado como `"FREEZER_GELADEIRA"`. É terminantemente proibido classificar como Balcão ou Caixa se houver refrigeradores ou bebidas em destaque.
  - `"BALCAO_CAIXA"` só é admitido se o foco central for terminal de pagamento, monitor de PDV ou gaveta de checkout.
  - Demais setores mapeados: `"GONDOLA_PRATELEIRA"`, `"ESTUFA_SALGADOS"`, `"BANHEIRO"`.
- **Validação de Objetos Inválidos (`INVALID_OBJECT`):**
  - Se a foto enviada não retratar um setor operacional da loja (selfies, memes, documentos, prato isolado, piso vazio ou rua), o sistema retorna `setor: "INVALID_OBJECT"`, `score: 0.0`, `conformidade: false` e não valida a auditoria no dia.
  - Retorna mensagem orientadora solicitando foto nítida de uma área da conveniência.
- **Eliminação de Alucinações & Safety Override:**
  - Proibição de mencionar objetos inexistentes (fios de maquininha, bobinas de papel, cupons fiscais).
  - Oportunidades de melhoria derivadas unicamente do que é visível (alinhamento de rótulos, puxar latas para frente, reposição de rupturas aparentes).
  - Safety Override no parser: caso a IA retorne balcão/caixa mas os elementos detectados contenham cervejeiras/bebidas, o parser corrige o setor automaticamente para `"FREEZER_GELADEIRA"` e remove alucinações de checkout.
- **Tom de Voz Educativo (Fase 1):**
  - Postura de "supervisor orientador", acolhedor e incentivador. Elogia primeiro os acertos, aponta 1 a 3 melhorias construtivas e atribui nota flexível de 0 a 10.

---

## 4. 🎮 Gamificação, Operadores e Resolução de Turnos (`database.py`)

- **Resolução Automática de Turnos:**
  - `T1_MANHA`: 06:00 às 13:59
  - `T2_TARDE`: 14:00 às 21:59
  - `T3_NOITE`: 22:00 às 05:59
- **Motor de Gamificação e Pontuação (`calculate_audit_points`):**
  - `Score >= 9.0`: 120 pontos (100 pontos base + 20 pontos bônus de excelência).
  - `Score entre 7.5 e 8.9`: 90 pontos.
  - `Score entre 6.0 e 7.4`: 60 pontos.
  - `Score < 6.0` (mas > 0): 20 pontos (reconhecimento pelo esforço de envio).
  - `Score <= 0` (`INVALID_OBJECT`): 0 pontos.
  - **Bônus de Pontualidade:** +10 pontos extras se a auditoria for enviada antes das 16h45.
- **Captura e Atribuição do Operador:**
  - **Por Legenda da Foto:** Extrai automaticamente nomes por prefixos como *"auditoria carlos"*, *"feito por Marcos"*, *"operador: Joao"*.
  - **Por Resposta Interativa (15 min):** Se a foto for enviada sem nome, a auditoria fica como pendente de atribuição. A primeira mensagem enviada no grupo nos próximos 15 minutos (ex: *"Carlos"*, *"Marcos T1"*) vincula o operador e credita os pontos correspondentes retroativamente.
- **Modelo de Dados & Migrações:**
  - Tabela `operators` vinculada às lojas com acúmulo de `total_points` e contagem de auditorias.
  - Colunas `operator_name` e `points` integradas na tabela `audits`.
  - Migrações automáticas via SQLite `PRAGMA` no `init_db()`.
- **Card de WhatsApp e API de Ranking:**
  - Endpoint `GET /api/ranking` gerando card com classificação de lojas, turno líder e Top Operadores.

---

## 5. 📸 Captura ao Vivo via PWA (`/capturar/{store_id}`)

- **PWA Mobile-First Responsivo:**
  - Endpoints dedicados: `/capturar/1`, `/capturar/2`, `/capturar/3`.
  - Visual dark mode, moderno e otimizado para celulares dos colaboradores.
- **Trava de Sensor de Câmera de Hardware:**
  - Utilização da diretiva HTML:
    ```html
    <input type="file" accept="image/*" capture="environment" id="cameraInput">
    ```
  - Força o sistema operacional móvel (Android / iOS) a abrir diretamente o sensor da câmera traseira, bloqueando o acesso à galeria ou arquivos pré-existentes.
- **Identificação do Operador:**
  - Campo de texto para o colaborador preencher o seu nome antes de disparar a foto, pontuando no ranking instantaneamente.
- **Feedback Visual em Tempo Real:**
  - Loading com análise do Gemini 2.0 Flash e exibição imediata do badge de conformidade, nota, pontos positivos, oportunidades de melhoria e orientações práticas.

---

## 6. 🧪 Validações Técnicas e Testes Automatizados

- **Verificação Sintática e Imports:**
  - Compilação via `py_compile` em todos os módulos Python: 100% livre de erros (`Exit code 0`).
- **Validação de Gamificação (`test_gamification.py`):**
  - Resolução de turnos (T1, T2, T3) testada com horários limite.
  - Cálculo de faixas de pontos e bônus de pontualidade verificado.
  - Normalização de nomes de operadores via regex.
  - Atribuição de operador por resposta rápida em até 15 minutos.
  - Geração do card de ranking do WhatsApp validada.
  - **Status:** ✅ 100% de Sucesso.
- **Validação de Visão Computacional (`test_gemini_fase1.py`):**
  - Verificação de Chain of Thought no system prompt.
  - Regra inegociável de geladeiras vs balcão/caixa.
  - Salvaguarda do parser (safety override) contra classificações errôneas.
  - Tratamento de `INVALID_OBJECT` com score 0.0.
  - Mocks determinísticos sem alucinações de fios e bobinas.
  - **Status:** ✅ 100% de Sucesso.
- **Integridade da Base de Dados (`app.db`):**
  - Tabelas ativas: `stores` (3 lojas configuradas), `audits`, `alerts`, `operators`.
  - Migrações automáticas de schema preservadas e funcionais.

---

## 7. 🎯 Estado para Retomada dos Testes Práticos

O ambiente está totalmente preparado e estável para a continuidade dos testes operacionais:
1. O servidor FastAPI está em execução via Uvicorn na porta `8000`.
2. A Evolution API v2 está ativa na porta `8080` com o WhatsApp conectado e webhook funcional.
3. Para testes de campo com fotos reais de lojas, basta inserir a chave `GEMINI_API_KEY` no arquivo `.env` ou utilizar o mock calibrado existente.

---

## 8. ⚖️ Refatoração P0: Desacoplamento da IA (Perito Visual vs Juiz Determinístico)

Implementação dos **Passos 1, 2 e 3** com arquitetura em duas camadas: o modelo de IA atua exclusivamente como **Perito Visual** (observação objetiva e factual sem juízo de valor ou notas), enquanto o **Evaluator Service** atua como **Juiz Determinístico** (cálculo matemático de pesos, penalidades e conformidade setorial).

### Passo 1: Schemas Pydantic Estritos (`services/gemini_service.py`)
- **`StatusAnalise`:** `CONCLUIDA`, `PARCIAL`, `NAO_VERIFICAVEL`.
- **`Severidade`:** `BAIXA`, `MEDIA`, `ALTA`.
- **`EvidenciaVisual`:** `criterio`, `descricao`, `localizacao`, `conforme`, `severidade`.
- **`GeminiAuditoriaOutput`:** `setor_identificado`, `status_analise`, `nivel_confianca`, `requer_revisao`, `elementos_visuais_detectados`, `evidencias`, `acoes_recomendadas`.
- Eliminação completa de geração subjetiva de notas, pontuações ou bônus por LLM.

### Passo 2: Evaluator Service Determinístico (`services/evaluator_service.py`)
- **Matriz de Pesos Rígida (`CHECKLIST_SETORES`, total = 10 por setor):**
  - `FREEZER_GELADEIRA`: organizacao=3, espaco_vazio=3, precificacao=2, limpeza=2
  - `GONDOLA_GERAL`: organizacao=3, espaco_vazio=3, precificacao=2, limpeza=2
  - `ESTUFA`: limpeza=4, apresentacao=3, organizacao=3
  - `BANHEIRO`: limpeza=5, conservacao=3, reposicao_insumos=2
  - `VIDROS`: limpeza=7, conservacao=3
  - `CAIXA`: organizacao=4, limpeza=3, impulso_visivel=3
- **Sinônimos e Compatibilidade (`SETOR_ALIASES`):**
  - Mapeamento automático de `GONDOLA_PRATELEIRA -> GONDOLA_GERAL`, `ESTUFA_SALGADOS -> ESTUFA`, `BALCAO_CAIXA -> CAIXA`.
- **Penalidades por Severidade:**
  - `BAIXA`: -0.5 pt | `MEDIA`: -1.0 pt | `ALTA`: -2.0 pts
- **Função Matemática `calcular_nota(analise: GeminiAuditoriaOutput) -> ResultadoAvaliacao`:**
  - `base_score = (pontos_obtidos / total_peso_possivel) * 10.0`
  - `nota_final = max(0.0, min(10.0, base_score - penalidade_extra))`
  - `requer_acao_imediata = (severidade_alta_presente) or (nota_final < 7.0)`
  - Caso `NAO_VERIFICAVEL`: nota 0.0, `requer_revisao=True`, `requer_acao_imediata=True`.

### Passo 3: Integração nas Rotas e Webhooks (`main.py` e `services/gemini_service.py`)
- **Pipeline Perito -> Juiz:**
  - `analyze_store_image` instancia `GeminiAuditoriaOutput` e aciona `calcular_nota`.
  - Mocks e tratamento de `INVALID_OBJECT` padronizados para alimentar o avaliador.
- **Webhook Evolution (`POST /webhook/evolution`):**
  - Log operacional estruturado com setor, nota calculada, proporção de conformidade e itens corrigíveis.
  - Persistência no SQLite (`audits.ai_analysis`) enriquecida com `avaliacao` e `gemini_output`.
- **PWA Upload ao Vivo (`POST /api/upload-auditoria/{store_id}`):**
  - Resposta enriquecida com `avaliacao`, `resumo_operacional`, `itens_corrigiveis` e `requer_acao_imediata` sem alterar o contrato de campos anteriores.

### Suíte de Testes 100% Conforme:
- `test_evaluator_service.py`: 5 testes unitários cobrindo todos os cenários determinísticos.
- `test_gemini_fase1.py`: 4 testes de visão computacional, Chain of Thought e anti-alucinação.
- `test_gamification.py`: 4 testes de turnos, pontuação e rankings.
- `test_integration_evaluator.py`: 2 testes de ponta a ponta (PWA e Webhook) integrados ao SQLite e Evolution API.

