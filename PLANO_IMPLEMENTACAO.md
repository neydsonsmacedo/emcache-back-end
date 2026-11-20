# Plano de Implementação — Camada Financeira, Agente e Canais (`emcache-back-end`)

Este documento consolida o estado atual do projeto (o que já foi construído) e o plano de ação para implementar a camada de serviços financeiros, o núcleo do agente de IA e os adaptadores de canal (Chat Web e Telegram).

---

## 1. Por que centralizar o Agente no Back-end agora

1. **Reuso real entre Telegram e Front-end:** Com a lógica do agente encapsulada como um serviço interno no back-end, o chat da aplicação web e o bot do Telegram tornam-se apenas adaptadores de entrada/saída. Regras de negócio, prompts, *tools* e memória de conversa não ficam duplicados.
2. **Mesma base para o Agente e para os Gráficos:** As perguntas que o agente precisa responder (*"quanto gastei com alimentação este mês?"*, *"qual meu saldo atual?"*) utilizam exatamente as mesmas agregações necessárias para a página de gráficos. Construir a camada de consultas financeiras no back-end atende aos dois propósitos de forma consistente.
3. **Segurança e controle:** Chaves de API de LLM, credenciais da Pluggy, tokens do Telegram e acesso ao PostgreSQL permanecem centralizados no servidor, nunca expostos no front-end.

---

## 2. O que já foi feito

Com base na estrutura atual do repositório, os seguintes blocos já estão implementados:

### Infraestrutura, Banco e CI/CD
- [x] **Docker Compose (`docker-compose.yml`):** Serviços `postgres` (`emcache_db`, Postgres 16 Alpine, limite `512M`) e `backend` (`emcache_backend`, FastAPI, limite `256M`).
- [x] **Deploy automatizado (`SETUP_PLAN.md`, `README.md`):** Pipeline de validação e deploy contínuo via GitHub Actions para a VPS.
- [x] **Schema inicial e migração idempotente (`db/init.sql` e `backend/app/db.py`):**
  - Tabela `institutions` com origens manuais pré-cadastradas (`Carteira`, `Dinheiro`, `Vale Alimentação`, `Vale Refeição`) e suporte a `pluggy_connector_id`.
  - Tabelas Pluggy: `pluggy_items`, `pluggy_accounts`, `pluggy_transactions` (valores em centavos `BIGINT`).
  - Tabela `manual_transactions` (evolução da antiga `transactions`) com `status` (`'completo'` | `'pendente'`), `dados_faltantes`, `canal_entrada` (`'text'`, `'audio'`, `'image'`), `institution_id`, `pluggy_account_id` e `matched_pluggy_transaction_id`.
  - View SQL `unified_transactions` unindo `pluggy_transactions` e `manual_transactions` completas e ainda não conciliadas (`matched_pluggy_transaction_id IS NULL`).

### Integração Pluggy Open Finance e API Base
- [x] **Cliente Pluggy (`backend/app/pluggy_client.py`):** Autenticação, criação de `connect-token`, busca de itens, contas e transações paginadas.
- [x] **Serviço de Sincronização (`backend/app/sync_service.py`):** Sincronização completa de itens/contas/transações, `refresh` com polling opcional e processamento assíncrono de webhooks (`item/*`, `transactions/*`).
- [x] **Endpoints FastAPI (`backend/app/main.py`):**
  - `GET /health`
  - `POST /api/pluggy/connect-token`
  - `POST /api/pluggy/items`, `POST /api/pluggy/items/{item_id}/sync`, `POST /api/pluggy/items/{item_id}/refresh`
  - `GET /api/pluggy/items`, `GET /api/pluggy/accounts`, `GET /api/pluggy/transactions`
  - `POST /api/pluggy/webhook`
  - `GET /api/institutions` e `GET /api/transactions` (leitura da view `unified_transactions`).
- [x] **CLI operacional (`backend/app/cli.py`):** Comandos `register`, `sync`, `refresh` e `status`.
- [x] **Prompt inicial de extração (`otto-prompt`):** Regras de negócio base para extração multimodal e controle de campos pendentes (originalmente desenhado para n8n).

---

## 3. Gaps e Pontos de Atenção no Projeto Atual

1. **Conciliação e duplicidade (parcialmente estruturado no banco, sem lógica de negócio):**
   - A view `unified_transactions` e a coluna `manual_transactions.matched_pluggy_transaction_id` já existem no schema, mas **ainda não há serviço de conciliação** (automática ou assistida) para detectar quando uma transação sincronizada pela Pluggy corresponde a um lançamento manual feito dias antes no chat/Telegram.
2. **Identidade de usuário e vínculo com o Telegram:**
   - `manual_transactions` não possui coluna `user_id`, `pluggy_items` tem apenas `client_user_id` opcional, e `unified_transactions` não filtra por usuário.
   - Para o usuário conversar no Telegram e visualizar os mesmos dados no chat web e nos gráficos, é necessário criar uma entidade `users` (e vínculo `telegram_chat_id` / canais) filtrando todas as leituras e escritas.
3. **Memória de conversa (especialmente para dados pendentes):**
   - `manual_transactions` já prevê `status = 'pendente'` e `dados_faltantes`, mas ainda não existem tabelas no PostgreSQL para histórico de mensagens e estado de sessão (`chat_sessions` / `chat_messages`), essenciais para o agente continuar o diálogo naturalmente na mensagem seguinte.
4. **Infraestrutura e processamento multimodal (áudio/imagem):**
   - Em `docker-compose.yml`, o serviço `backend` está limitado a `256M` de memória. Usando APIs externas via `httpx` (que já está em `backend/requirements.txt`), o consumo tende a ser baixo, mas uploads de áudio/imagem e SDKs adicionais exigem atenção e possível ajuste para `512M` (ou mais).

---

## 4. O que precisa ser feito (Ordem Recomendada)

### Fase 1 — Identidade de Usuário, Sessões e Ajustes de Schema
- [x] Criar tabela `users` (ex: `id`, `name`, `email`, `created_at`) e tabela de vínculo de canais `user_identities` / colunas de canal (ex: `telegram_chat_id` único).
- [x] Adicionar `user_id` em `manual_transactions` e vincular `pluggy_items` ao usuário (`user_id` / `client_user_id`).
- [x] Atualizar a view `unified_transactions` para expor `user_id` e garantir isolamento multiusuário em todas as queries.
- [x] Criar tabelas de persistência de conversa no PostgreSQL (`chat_sessions` e `chat_messages`, com referência opcional a `pending_transaction_id` em `manual_transactions`).
- [x] Atualizar `backend/app/config.py` e `docker-compose.yml` com variáveis de ambiente para LLM, transcrição/visão e Telegram, além de revisar o limite de memória (`256M` → `512M` se necessário).

### Fase 2 — Camada de Serviços Financeiros (`financial_service.py`)
Construir funções determinísticas no back-end (sem dependência de LLM) que servirão tanto ao **Agente** quanto aos **Endpoints de Gráficos**:
- [ ] **Consultas e Agregações (Leitura):**
  - Saldo consolidado (contas `pluggy_accounts` + carteiras/benefícios manuais, quando aplicável).
  - Gastos e receitas por período (dia, semana, mês, intervalo customizado).
  - Agrupamento por categoria e por instituição/origem.
  - Listagem de transações pendentes (`status = 'pendente'`) do usuário.
- [ ] **Operações de Escrita e Conciliação:**
  - Criar transação em `manual_transactions` (com validação de campos e cálculo de `status` / `dados_faltantes`).
  - Atualizar/completar transação pendente existente.
  - Serviço de conciliação (`reconcile_service`): sugerir ou vincular automaticamente `manual_transactions.matched_pluggy_transaction_id` quando uma transação Pluggy equivalente chegar (por valor, data aproximada e instituição/descrição).
- [ ] **Endpoints REST para o Front-end / Gráficos:**
  - Ex.: `GET /api/analytics/summary`, `GET /api/analytics/by-category`, `GET /api/analytics/cashflow` e CRUD/conciliação de `manual_transactions`.

### Fase 3 — Núcleo do Agente (`agent_service.py`) e Pipeline Multimodal
- [ ] Migrar e refinar as regras do `otto-prompt` para o back-end Python (alinhando categorias, `tipo_transacao`, valores em centavos e `institutions`).
- [ ] Implementar cliente leve para LLM/Transcrição/Visão via `httpx` (texto, áudio e imagem de comprovantes/notas).
- [ ] Expor as funções do `financial_service` como **tools** do agente (evitando SQL arbitrário gerado pelo LLM).
- [ ] Implementar orquestração do agente com assinatura desacoplada de canal:
  - Entrada: `(user_id, channel, message | media)`
  - Carregamento de histórico + transação pendente da sessão no PostgreSQL
  - Execução de tools / atualização de estado
  - Saída estruturada (resposta textual + metadados da ação/transação).

### Fase 4 — Adaptadores de Canal (Chat Web e Telegram)
- [ ] **Chat Web:**
  - Criar `POST /api/chat` (suportando texto e upload/anexo de áudio/imagem, com opção de SSE/streaming se desejado).
  - Criar endpoint de histórico `GET /api/chat/history` para o front-end restaurar a conversa.
- [ ] **Telegram:**
  - Criar `POST /api/telegram/webhook` (ou worker dedicado) para receber updates do Bot API (texto, voice/audio, photo).
  - Mapear `telegram_chat_id` → `user_id` (incluindo fluxo simples de vinculação/onboarding caso o usuário ainda não esteja associado).
  - Repassar a mensagem normalizada para o mesmo `agent_service` e enviar a resposta de volta via Telegram Bot API.

### Fase 5 — Testes e Observabilidade
- [ ] Adicionar testes unitários e de integração para:
  - Agregações financeiras e isolamento por `user_id`.
  - Fluxo de transação pendente → completada em múltiplas mensagens.
  - Conciliação entre `manual_transactions` e `pluggy_transactions` sem duplicidade na `unified_transactions`.
  - Webhook do Telegram e endpoint `/api/chat`.
- [ ] Validar consumo de memória no container `emcache_backend` com requisições multimodais reais e atualizar `docker-compose.yml` conforme medição.
