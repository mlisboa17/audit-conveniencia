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

---

## 9. 🔄 Fase P1: Ciclo de Correção Antes/Depois e Tickets de Pendência

Implementação do ciclo de correção ágil e inteligente via WhatsApp e PWA mobile:

### 1. Modelo de Dados e Persistência (`database.py`)
- **Tabela `audit_tickets`:**
  - `id`: UUID v4 primário em formato string.
  - `audit_origem_id`: Chave estrangeira referenciando a auditoria que gerou o apontamento.
  - `store_id`: Identificador da loja associada.
  - `operator_id`: Nome ou identificador do operador responsável.
  - `setor`: Setor da loja (ex: `FREEZER_GELADEIRA`, `GONDOLA_GERAL`, `ESTUFA`).
  - `criterio`: Critério não conforme (ex: `espaco_vazio`, `organizacao`, `precificacao`, `limpeza`).
  - `descricao_falha`: Detalhamento específico da inconformidade apontada pelo perito.
  - `foto_origem_path`: Caminho no disco da foto do momento da auditoria que registrou a falha.
  - `status`: Enum de estados (`ABERTO`, `CORRIGIDO`, `EXPIRADO`, `JUSTIFICADO`).
  - `created_at`: Data e hora de abertura do ticket (UTC).
  - `expires_at`: TTL automático de 20 minutos (`created_at + 20 min`).
  - `resolved_at`: Momento exato em que a correção foi homologada.
  - `bonus_creditado`: Pontuação de bônus concedida (padrão: 25 pontos).
- **Funções Assíncronas de Acesso:**
  - `criar_tickets_pendencia(audit_id, store_id, operator_id, setor, itens_corrigiveis, foto_path, session=None)`: Cria tickets individuais para cada inconformidade encontrada com expiração em 20 minutos.
  - `buscar_ticket_aberto(store_id, setor=None, session=None)`: Localiza o ticket aberto mais recente dentro da janela de validade, aplicando expiração preguiçosa (*lazy sweep*) em tickets vencidos (`status = 'EXPIRADO'`).
  - `fechar_ticket(ticket_id, status='CORRIGIDO', bonus_pontos=0, session=None)`: Fecha o ticket, armazena `resolved_at` e registra o bônus concedido.

### 2. Serviço de Perícia Comparativa Visual (`services/gemini_service.py`)
- **Schema Pydantic `GeminiCorrecaoOutput`:**
  - `mesmo_setor`: Validação booleana se a nova imagem retrata o mesmo espaço físico e ângulo da anterior.
  - `problema_resolvido`: Validação booleana se a inconformidade descrita foi sanada.
  - `detalhes_observados`: Descrição visual factual das alterações identificadas entre o Antes e o Depois.
  - `nivel_confianca`: Grau de precisão visual da análise (0.0 a 1.0).
- **System Prompt Comparativo Rigoroso:**
  - Recebe ambas as imagens (Antes/Falha e Depois/Correção).
  - Exige correspondência física de setor/gôndola e proíbe validação de fotos genéricas ou de outro local.
- **Método `comparar_correcao`:**
  - Envio multimodal das duas imagens para o Gemini 2.0 Flash (`temperature=0.1`).
  - Fallback determinístico inteligente para simulação e testes.

### 3. Interceptação e Gamificação no Webhook & PWA (`main.py`)
- **Interceptação Inteligente:**
  - Ao receber uma foto (via webhook do WhatsApp ou captura ao vivo do PWA), o sistema verifica se existe ticket `ABERTO` dentro da janela de 20 minutos para a loja.
  - Em caso positivo, direciona para `gemini_service.comparar_correcao(foto_antes, foto_depois, ...)`.
  - **Se `mesmo_setor == True` e `problema_resolvido == True`:**
    - Ticket marcado como `CORRIGIDO`.
    - Operador recebe **+25 pontos de bônus** imediatos no ranking.
    - Notificação comemorativa disparada no grupo de WhatsApp:
      > ✅ *CORREÇÃO VALIDADA COM SUCESSO!*  
      > 🏪 *Setor:* FREEZER_GELADEIRA  
      > 🎯 *Problema resolvido:* Puxar latas de refrigerante para frente  
      > 🏆 *Bônus:* +25 pontos creditados para Carlos Silva!
  - **Se divergente ou não resolvido:** Segue com o fluxo de auditoria padrão independente.
  - **Se nova auditoria gerar itens corrigíveis:** Gera tickets de pendência com expiração de 20 min e envia alerta visual no WhatsApp convocando a equipe para a oportunidade de bônus.

### 4. Suíte de Testes Automatizados (`tests/test_correction_cycle.py`)
- Suíte `pytest` cobrindo 100% dos requisitos da especificação:
  1. `test_abertura_ticket_expiracao_20_minutos`: Abertura correta de ticket com TTL de 20 min (1200s).
  2. `test_comparacao_correcao_resolvida_fecha_ticket_e_adiciona_bonus`: Homologação da correção, fechamento do ticket e crédito de +25 pontos no ranking do operador.
  3. `test_ticket_expirado_nao_credita_bonus_retroativo`: Garantia de que tickets expirados não bonificam nem interceptam novas auditorias.
  4. `test_foto_setor_divergente_nao_valida_pendencia`: Rejeição de validação para fotos de setores divergentes.
- **Resultado:** ✅ 4/4 testes aprovados com sucesso (`pytest tests/test_correction_cycle.py`).

---

## 10. 🛡️ Fase P2: Gamificação Auditável, Limites Anti-Exploit e Trilha de Contestação

Implementação de blindagem de pontuação, tetos diários/turno, comandos operacionais e bônus de consistência:

### 1. Regras Anti-Exploit e Tetos de Pontuação (`database.py`)
- **Teto de Bônus de Correção:**
  - Máximo de **3 bônus de correção (+25 pts)** por operador por turno (+75 pts max).
  - A 4ª tentativa de correção no mesmo turno fecha o ticket como `CORRIGIDO`, mas com `bonus_creditado = 0` e aviso explicativo no WhatsApp: *"Limite de bônus de correção atingido para este turno"*.
  - Implementado via `contar_bonus_correcao_turno` e `aplicar_correcao_ticket`.
- **Trilha de Contestação e Justificativas:**
  - Colunas `justificativa_texto` (TEXT) e `justificado_por` (VARCHAR) persistidas no modelo `AuditTicket` com migração automática no `init_db()`.
  - Função assíncrona `justificar_ticket(ticket_id, motivo, autor) -> bool`:
    - Altera status de `ABERTO` para `JUSTIFICADO`.
    - Registra data de resolução e autor.
    - Exclui o apontamento do cômputo de pendências/falhas operacionais.

### 2. Comandos Operacionais no WhatsApp (`main.py` e `services/evolution_service.py`)
- **Comando `!justificar <ID_TICKET> <MOTIVO>`:**
  - Ex: `!justificar a1b2c3 Falta de estoque no CD central`
  - Valida se o ticket existe na loja e está `ABERTO` (busca por ID completo ou prefixo de 6-8 caracteres).
  - Registra a justificativa e responde com confirmação estruturada:
    > 📝 *JUSTIFICATIVA REGISTRADA COM SUCESSO!*  
    > 🎫 *Ticket:* a1b2c3 | *Setor:* ESTUFA_SALGADOS  
    > 📌 *Motivo:* Falta de estoque no CD central  
    > Operação liberada sem penalidade para a loja.
- **Comando `!pendencias`:**
  - Lista todos os tickets em aberto da loja no momento com tempo restante de TTL.
  - Executa *lazy sweep* atualizando tickets vencidos para `EXPIRADO`.
  - Exibe ID curto, setor, falha e minutos restantes para correção ou contestação.
- **Ignore Set Aprimorado (`evolution_service.py`):**
  - Comandos operacionais `!justificar` e `!pendencias` são filtrados para não serem interpretados erroneamente como nomes de colaboradores.

### 3. Bônus de Consistência - Loja Padrão 10 (`services/evaluator_service.py`)
- **Fechamento e Avaliação de Turno (`avaliar_consistencia_turno`):**
  - Critérios Rígidos:
    1. Mínimo de 4 setores distintos auditados no turno.
    2. Todos os setores auditados devem ter obtido nota calculada >= 9.0.
    3. Zero tickets pendentes não resolvidos (nenhum ticket em status `ABERTO` ou `EXPIRADO`).
  - Premiação:
    - Credita **+50 pontos** de Loja Padrão 10 divididos igualmente entre os operadores ativos do turno.
    - Estimula a equipe a manter a loja no padrão de excelência desde o início do turno.

### 4. Suíte de Testes Automatizados (`tests/test_gamification_p2.py`)
- Suíte `pytest` cobrindo 100% dos requisitos da especificação:
  1. `test_limite_teto_bonus_correcao`: Valida 3 bônus de +25 pts e bloqueio com 0 pts na 4ª correção.
  2. `test_comando_justificar_ticket`: Valida contestação via WhatsApp, persistência do motivo e transição para `JUSTIFICADO`.
  3. `test_comando_pendencias_loja`: Valida listagem de pendências ativas com TTL e expiração preguiçosa.
  4. `test_bonus_consistencia_loja_padrao_10`: Valida concessão de +50 pts com 4 setores >= 9.0 e bloqueio caso haja pendências não resolvidas.
- **Resultado:** ✅ 4/4 testes aprovados com sucesso (`pytest tests/test_gamification_p2.py`).

---

## 11. 🎯 Fase P3: Pipeline de Visão Blindada em 2 Etapas (Gatekeeper + Perito Especialista do Setor)

Implementação do pipeline de visão computacional em 2 estágios desacoplados para eliminar alucinações de elementos, descarte imediato de ruídos/memes/fotos ilegíveis e foco cirúrgico no setor confirmado:

### 1. Gatekeeper de Sanidade e Triagem Visual (`services/gemini_service.py`)
- **Schema Pydantic `GatekeeperOutput`:**
  - `is_conveniencia_valida: bool`: Validação estrita se a imagem pertence ao ecossistema de conveniência/posto de combustível.
  - `qualidade_imagem_ok: bool`: Checagem rigorosa de foco, iluminação, enquadramento e resolução.
  - `motivo_rejeicao: Optional[str]`: Motivo claro da recusa (ex: *'Foto muito escura'*, *'Imagem não pertence a uma conveniência'*, *'Foto excessivamente borrada'*).
  - `setor_detectado: str`: `FREEZER_GELADEIRA`, `ESTUFA`, `BANHEIRO`, `VIDROS`, `CAIXA`, `GONDOLA_GERAL`, `OUTRO`.
  - `nivel_certeza_setor: float`: Nível de certeza da classificação entre 0.0 e 1.0.
  - Implementa protocolo `__await__` para interoperabilidade síncrona/assíncrona transparente.
- **Diretrizes Rígidas de Triagem:**
  - Descarte imediato de selfies, pessoas em primeiro plano, documentos, comprovantes/notinhas fiscais, prints de celular/conversas, memes, fotos pretas/subexpostas, chão/teto sem contexto e fotos borradas.
  - Se rejeitado: encerra a auditoria imediatamente na Etapa 1, retornando `StatusAnalise.NAO_VERIFICAVEL`, nota 0.0, `requer_revisao=True` e `evidencias=[]`, sem acionar o perito do setor.
  - **Regra Inegociável:** Qualquer imagem com geladeiras expositoras verticais, cervejeiras, freezers ou portas de vidro de bebidas é cravada como `FREEZER_GELADEIRA`.

### 2. Prompts de Peritos Especialistas Setoriais (`services/gemini_service.py`)
- Prompts cirúrgicos isolados por setor no perito visual:
  - `PROMPT_ESPECIALISTA_FREEZER`: Foco obrigatório em frenteamento (facing), espaços vazios (rupturas visíveis), limpeza de vidros/portas e organização. Proibição expressa de tentar ler marcas ilegíveis ou alucinar fios/bobinas de checkout.
  - `PROMPT_ESPECIALISTA_ESTUFA`: Foco em transparência do vidro frontal (ausência de gordura/sujeira), organização das bandejas e salgados dourados/apetitosos. Proibição expressa de inferir temperatura ou validade física de alimentos.
  - `PROMPT_ESPECIALISTA_BANHEIRO`: Foco em lixeira com saco plástico, insumos (papel toalha/papel higiênico/sabonete), higiene de pia/espelho e piso seco. Proibição expressa de pontuar precificação, estoque ou itens de impulso no banheiro.
  - `PROMPT_ESPECIALISTA_GONDOLA_GERAL`: Foco em alinhamento de embalagens de snacks/mercearia, ausência de pacotes caídos no fundo e frentes com espaços vazios.
  - `PROMPT_ESPECIALISTA_CAIXA`: Foco no balcão de checkout desobstruído para clientes, ausência de lixo/pertences pessoais e displays de impulso alinhados.
  - `PROMPT_ESPECIALISTA_VIDROS`: Foco nas vidraças externas da fachada e portas de entrada, sem poeira grossa e conservação de faixas/adesivos.

### 3. Orquestração do Fluxo `analisar_foto` & Integração
- **Método `analisar_foto(img_bytes, mime_type)`:**
  - Etapa 1: Executa `validar_foto_conveniencia(img_bytes)`. Em caso de rejeição, retorna imediatamente `GeminiAuditoriaOutput` com `NAO_VERIFICAVEL` e nota 0.0 calculada pelo avaliador determinístico.
  - Etapa 2: Com sanidade aprovada, injeta exclusivamente o prompt cirúrgico do setor detectado no Gemini 2.0 Flash (`temperature=0.1`).
  - Encaminha o laudo validado diretamente para `evaluator_service.calcular_nota(...)`.
- **Compatibilidade:** `analyze_store_image` delega para o pipeline desacoplado e mantém compatibilidade total com webhooks, comandos e PWA.

### 4. Suíte de Testes Automatizados (`tests/test_vision_pipeline.py`)
- `test_gatekeeper_rejeita_imagem_fora_de_contexto`: Valida descarte de memes/comprovantes gerando `NAO_VERIFICAVEL` sem acionar perito.
- `test_gatekeeper_rejeita_qualidade_escura_ou_borrada`: Valida descarte de fotos degradadas com `qualidade_imagem_ok = False`.
- `test_perito_setorial_freezer_aplica_regras_estritas`: Valida que laudo de freezer contém critérios estritos e 0 critérios de banheiro/estufa/caixa.
- `test_perito_setorial_outros_setores_regras_estritas`: Valida isolamento entre banheiro e estufa.
- `test_pipeline_integrado_foto_valida`: Valida execução integrada das duas etapas e pontuação matemática consistente pelo avaliador.
- `test_gatekeeper_prioridade_inegociavel_freezer`: Valida que fotos de geladeiras nunca são desviadas para Caixa.
- **Resultado:** ✅ 6/6 testes aprovados com sucesso (`pytest tests/test_vision_pipeline.py`).

---

## 12. 📸 Auditoria de Fotos Reais em `dataset/` (`scripts/auditar_fotos_reais.py`)

Criação da infraestrutura para processamento em lote de fotos reais de conveniência consumindo a API real do Gemini (Zero Mocks):

### 1. Diretório Oficial `dataset/`
- Localização oficial na raiz do projeto: `dataset/`
- Subdiretório para persistência de laudos JSON: `dataset/resultados/`
- Formatos de imagem suportados: `.jpg`, `.jpeg`, `.png`.

### 2. Script de Execução Real (`scripts/auditar_fotos_reais.py`)
- **Carregamento de Ambiente:** Carrega `.env` com `python-dotenv` e valida `GEMINI_API_KEY`.
- **Política Zero Mocks:**
  - Bloqueia a execução imediatamente se `GEMINI_API_KEY` for o placeholder ou estiver vazia.
  - Sobrescreve métodos de fallback para lançar exceção explícita caso a chamada real falhe, garantindo que nenhum laudo sintético seja gerado.
- **Detecção de Pasta Vazia:**
  - Exibe aviso explicativo caso `dataset/` não contenha imagens:
    *"⚠️ Nenhuma foto encontrada na pasta 'dataset/'. Adicione fotos reais da loja para auditar."*
- **Pipeline de Execução por Foto:**
  1. Submete bytes a `gemini_service.analisar_foto(img_bytes)`.
  2. Encaminha laudo a `evaluator_service.calcular_nota(analise)`.
  3. Exibe relatório visual no terminal com formatação rica:
     - Foto, Status do Gatekeeper, Setor, Confiança, Evidências com Severidade, Nota / 10.0, Ação Imediata e Itens Corrigíveis.
  4. Salva o laudo completo estruturado em `dataset/resultados/[nome_do_arquivo].json`.
- **Mecanismos de Resiliência Implementados:**
  - *Retomada Inteligente (Skip):* Ignora arquivos já auditados anteriormente com a mensagem `⏭️ [Ignorando] {nome_arquivo}`.
  - *Pacing e Backoff Exponencial:* Intervalo de 12 segundos e retry automático com chaveamento de modelos caso haja sobrecarga ou esgotamento de cota diária.
- **Resultado Operacional Real (Zero Mocks):**
  - **14/14 fotos auditadas com 100% de sucesso** consumindo a API oficial do Google Gemini.
  - Laudos estruturados e determinísticos salvos em `dataset/resultados/`.

