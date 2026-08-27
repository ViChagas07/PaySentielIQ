# 🐇 Event-Driven Architecture — RabbitMQ 4 (SentinelaPay / PaySentinelIQ)

> Documento de arquitetura da camada de mensageria. Atualizado com a implementação
> de agosto/2026.

---

## 1. Visão Geral

O SentinelaPay usa **RabbitMQ 4.x** como infraestrutura de eventos assíncronos.
RabbitMQ **não** é banco de dados de histórico: ele apenas transporta eventos; o
histórico definitivo fica no PostgreSQL (`audit_logs`), persistido por um
consumer dedicado.

```
Next.js (frontend)
   │  HTTP (REST/WebSocket) — NUNCA conecta direto no RabbitMQ
   ▼
FastAPI (app/main.py)
   │
   ├── Presentation (routers)  ── publicam eventos via EventPublisher (porta)
   │
   ▼
Application/Domain (use cases + repositories)
   │
   ▼
PostgreSQL ──┬── audit_logs (fonte da verdade do histórico)
             └── processed_events (inbox/idempotência)
   ▲
   │  (consumers)
RabbitMQ ── sentinel.events (topic)
   ├── sentinel.audit           → Audit Worker        → audit_logs
   ├── sentinel.notifications   → Notification Worker → notifications + WS (Redis bridge)
   └── sentinel.email           → Email Worker        → EmailService (SMTP/Console)

Scheduler (python -m app.workers.scheduler)
   │  lê payment_schedules (PostgreSQL)
   └─ publica bill.due_soon / bill.overdue → RabbitMQ → workers acima
```

---

## 2. Módulos

| Módulo | Responsabilidade |
|---|---|
| `app/messaging/domain/` | `EventType` (enum), `EventEnvelope`, portas (`EventPublisher`, `EventConsumer`), erros (`Transient/PermanentProcessingError`). **Zero imports de aio-pika.** |
| `app/messaging/application/` | Handlers de consumo (audit, notification, email), `RetryPolicy`, dedupe (`processed_events`), `BillDueSoonScheduler`. |
| `app/messaging/infrastructure/` | Implementação RabbitMQ (aio-pika): `RabbitMQConnectionManager`, `RabbitMQEventPublisher` (publisher confirms), `RabbitMQEventConsumer` (manual ack + retry + DLQ), topologia, fakes (`NullEventPublisher`, `InMemoryEventPublisher`) e `factory.py` (ponto único de obtenção do publisher). |
| `app/workers/` | Processos executáveis separados: `audit_worker`, `notification_worker`, `email_worker`, `scheduler` + `runner.py` (graceful shutdown). |
| `app/audit/application/` | `AuditLogService` — consultas com filtros/paginação sobre `audit_logs`. |
| `app/notifications/domain/ports.py` | Porta `EmailService` (provider-agnóstica). |
| `app/notifications/infrastructure/` | `SMTPEmailService`, `ConsoleEmailService`, templates de e-mail. |

---

## 3. Exchanges, Queues e Bindings

### Exchange principal

| Exchange | Tipo | Durabilidade |
|---|---|---|
| `sentinel.events` | `topic` | durable |
| `sentinel.dlx` | `topic` | durable |

### Queues e bindings

| Queue | Bindings | Consumer | Efeito |
|---|---|---|---|
| `sentinel.audit` | `#` | Audit Worker | persiste em `audit_logs` (filtra tipos auditáveis) |
| `sentinel.notifications` | `bill.due_soon`, `bill.overdue` | Notification Worker | cria NotificationModel + push WS via Redis |
| `sentinel.email` | `bill.due_soon` | Email Worker | envia e-mail transacional |

### Satélites de retry/DLQ (uma por fila)

| Fila | Papel |
|---|---|
| `<queue>.retry` | delay queue com TTL por mensagem; `x-dead-letter-exchange = sentinel.events` → ao expirar, a mensagem volta ao exchange com a routing key ORIGINAL. |
| `<queue>.dlq` | estacionamento terminal (dead letter) para mensagens inválidas/exauridas. |

---

## 4. Routing Keys (Event Types)

Fonte única de verdade: `app.messaging.domain.event_types.EventType` (nunca usar strings soltas).

```
user.action                      (payload.action = login | logout | register | mfa_verified)
bank_slip.analysis.started | completed | failed
payroll.analysis.started   | completed | failed
document.analysis.started  | completed | failed     (document_type desconhecido)
bill.scheduled | bill.cancelled | bill.paid | bill.due_soon | bill.overdue
notification.created | notification.read
report.viewed
```

Convenção: `<domínio>.<entidade/contexto>.<verbo>`.

---

## 5. Event Envelope

Todo evento usa o envelope (v1):

```json
{
  "event_id": "uuid",
  "event_type": "bill.due_soon",
  "occurred_at": "2026-08-27T12:00:00+00:00",
  "source": "paysentinel-api",
  "version": 1,
  "user_id": "uuid|null",
  "tenant_id": "uuid|null",
  "correlation_id": "uuid|null",
  "payload": {},
  "metadata": {}
}
```

Regras de segurança:

- **Nunca** publicar documentos, PDFs, bytes, credenciais, tokens ou senhas.
- `new_event()` sanitiza chaves sensíveis (`password`, `token`, `secret`, ...).
- Objetos grandes → publicar apenas ids + metadata; o consumer busca o resto no banco.
- O `correlation_id` vem do `CorrelationContext` (request_id) quando disponível.

---

## 6. Producers

| Producer | Eventos |
|---|---|
| `app/auth/presentation/router.py` | `user.action` (login, logout) |
| `app/api/documents/router.py` | `bank_slip.*`, `payroll.*`, `document.analysis.*` (started/completed/failed) |
| `app/settings_module/presentation/router.py` | `bill.scheduled`, `bill.cancelled`, `bill.paid` |
| `app/notifications/services.py` + router | `notification.created`, `notification.read` |
| `app/audit/infrastructure/router.py` | `report.viewed` (somente página 1 — evita ruído de polling) |
| `app/workers/scheduler.py` (BillDueSoonScheduler) | `bill.due_soon`, `bill.overdue` |

Todos os producers obtêm o publisher por `app.messaging.infrastructure.factory.get_event_publisher()`
— nenhum endpoint instancia conexões RabbitMQ diretamente.

### Publisher Confirms

O publisher usa canal com `publisher_confirms=True`: `publish()` só retorna `True`
após a confirmação do broker. Falha → `False` + log estruturado + Sentry (não-fatal).
A request HTTP **nunca** falha por causa do broker (resiliência), mas a falha nunca
é silenciosa.

---

## 7. Consumers

Cada worker roda `RabbitMQEventConsumer` + um handler:

| Consumer | Handler | Idempotência |
|---|---|---|
| `sentinel.audit` | `AuditEventHandler` | `processed_events` + coluna única `audit_logs.event_id` |
| `sentinel.notifications` | `NotificationEventHandler` | `processed_events` |
| `sentinel.email` | `EmailEventHandler` | `processed_events` (marcado só após envio) |

### Manual Acknowledgements

Mensagem só é ackada após o handler concluir **com sucesso**. Nenhuma perda
silenciosa: falha transitória → retry; falha permanente → DLQ.

### Retry (application-managed)

- `RetryPolicy(max_attempts=EVENT_RETRY_MAX_ATTEMPTS, backoff=EVENT_RETRY_BACKOFF_SECONDS)`
- Erro **transitório** e tentativas restantes → cópia da mensagem para `<queue>.retry`
  com TTL (backoff 5s → 30s → 120s por padrão) e header `x-retry-count` incrementado.
  A original é ackada; ao expirar, a cópia volta ao exchange com a routing key original.
- Tentativas exauridas (`x-retry-count >= max_attempts`) → `reject(requeue=False)` → DLQ.
- Erro **permanente** (`PermanentProcessingError`, envelope malformado, entidade
  inexistente) → DLQ imediato, sem retry.

### Dead Letter (DLQ)

`<queue>` → `x-dead-letter-exchange=sentinel.dlx`, `x-dead-letter-routing-key=<queue>`
→ `<queue>.dlq`. Mensagens inválidas nunca ficam em loop infinito.

### Escala horizontal

Workers não têm estado local — rode N instâncias; o RabbitMQ faz fair-dispatch
via prefetch (`RABBITMQ_PREFETCH_COUNT`). A idempotência vive no banco.

---

## 8. Fluxo de Auditoria (Relatórios / Activity History)

```
Ação do usuário → router publica evento → sentinel.events
   → sentinel.audit → AuditEventHandler → audit_logs (append-only)
   → GET /api/audit-logs  (leitura paginada, filtros: action, user_id,
                            entity_type, created_after/before, action__icontains)
   → GET /api/audit-logs/me (atividade do próprio usuário)
```

O frontend (`/audit-logs` e `/reports`) **nunca** consulta RabbitMQ — somente a API.

Mapeamento conceitual (spec → tabela existente):

| Campo conceitual | Coluna real (`audit_logs`) |
|---|---|
| event_type | `action` |
| metadata | `details` (JSONB) |
| occurred_at | `occurred_at` (novo) |
| event_id (dedupe) | `event_id` (novo, único) |

---

## 9. Fluxo de E-mail (bill.due_soon)

```
Scheduler (a cada BILL_SCHEDULER_INTERVAL_SECONDS)
  → consulta payment_schedules (status=pending, vencimento ≤ BILL_DUE_SOON_DAYS dias)
  → publica bill.due_soon (event_id determinístico por (bill, dia))
  → sentinel.email → EmailEventHandler:
      1. dedupe check (processed_events) — redelivery nunca reenvia
      2. resolve usuário + preferência email_alerts (desativado → skip)
      3. renderiza template (texto + HTML: nome, conta, valor, vencimento,
         dias restantes, link do SentinelaPay)
      4. envia via EmailService (SMTP em produção / Console em dev)
      5. grava processed_events SOMENTE após o envio ter sucesso
         (falha → TransientProcessingError → retry com backoff)
  → sentinel.notifications → cria notificação in-app + push WebSocket
```

O scheduler **não** envia e-mail nem cria notificação diretamente — só publica.

---

## 10. Notificação In-App (Notification Center existente)

Nenhum segundo sistema de notificações foi criado. O `NotificationEventHandler`
reutiliza `NotificationModel` e o bridge Redis Pub/Sub → WebSocket já existente
(`publish_via_redis`, canal `ws:notifications`). Canais futuros (WhatsApp,
Telegram, Slack) entram como novos canais dentro do mesmo handler/serviço.

---

## 11. Consistência DB ↔ Eventos (decisão: publisher confirms pós-commit)

**Avaliamos o Outbox Pattern** e decidimos **não** introduzi-lo nesta fase,
por decisão arquitetural documentada:

- Nenhum evento da plataforma é financeiro-transacional nem irreversível
  (auditoria, notificações e e-mails são reconstruíveis a partir do banco).
- Eventos são publicados **após o commit** do caso de uso, com publisher
  confirms; falha de publicação gera log estruturado + alerta Sentry — nunca
  perda silenciosa.
- Todos os consumidores são idempotentes (`processed_events` + event_id
  determinístico no scheduler), então uma eventual republicação manual é segura.

Se o projeto evoluir para eventos que exijam atomicidade estrita (ex.: emissão de
relatórios regulatórios), o Outbox pode ser adicionado pontualmente: transação
grava `outbox_event` junto do dado de negócio; um worker publica e marca como
enviado. A porta `EventPublisher` já isola essa evolução — nenhum use case mudaria.

---

## 12. Observabilidade

- Logs estruturados JSON (padrão existente) + campos `extra`: `event_id`,
  `event_type`, `queue`, `consumer`, `correlation_id`, `user_id`, `retry_count`,
  `duration_ms`, `success`.
- `CorrelationContext` propagado para dentro dos handlers a partir do envelope.
- Health checks:
  - `GET /health` — processo vivo;
  - `GET /ready` — database, redis **e rabbitmq** (conexão real, não "processo ativo");
  - `GET /health/full` — DB, Redis, RabbitMQ, LLM.
- Métricas do consumer: logs `event_consumed`, `event_retry_scheduled`,
  `event_dead_lettered`, `email_dispatched`, `audit_persisted`.

---

## 13. Graceful Shutdown

- API: lifespan fecha o publisher (`close_event_publisher()`).
- Workers: `runner.run_worker` captura SIGINT/SIGTERM → cancela consumo,
  fecha canal e conexão → exit code 0.
- Scheduler: para o loop, aguarda o scan corrente (timeout 30s), fecha publisher.
- Tarefa Celery: cria/fecha publisher dentro do loop efêmero (nunca deixa
  conexão presa a um loop morto).

---

## 14. Variáveis de Ambiente (novas)

| Variável | Default | Descrição |
|---|---|---|
| `RABBITMQ_ENABLED` | `true` | `false` = API usa NullEventPublisher; workers recusam iniciar |
| `RABBITMQ_URL` | `amqp://psi:psi_secret@localhost:5672/` | conexão AMQP (credenciais via env) |
| `RABBITMQ_EXCHANGE` | `sentinel.events` | exchange principal (topic) |
| `RABBITMQ_DLX` | `sentinel.dlx` | dead-letter exchange |
| `RABBITMQ_PREFETCH_COUNT` | `10` | fair dispatch por consumer |
| `EVENT_SOURCE` | `paysentinel-api` | origem lógica no envelope |
| `EVENT_RETRY_MAX_ATTEMPTS` | `3` | tentativas antes da DLQ |
| `EVENT_RETRY_BACKOFF_SECONDS` | `5,30,120` | backoff por tentativa (CSV) |
| `EMAIL_ENABLED` | `false` | `false` = ConsoleEmailService (loga, não envia) |
| `EMAIL_FROM` | `SentinelaPay <no-reply@paysentineliq.com>` | remetente |
| `SMTP_HOST/PORT/USERNAME/PASSWORD/USE_TLS` | localhost/587/–/–/true | SMTP relay |
| `APP_BASE_URL` | `http://localhost:3000` | links nos e-mails |
| `BILL_SCHEDULER_ENABLED` | `true` | liga/desliga o scheduler standalone |
| `BILL_SCHEDULER_INTERVAL_SECONDS` | `3600` | cadência do scan |
| `BILL_DUE_SOON_DAYS` | `2` | janela "próximo do vencimento" |

---

## 15. Desenvolvimento Local

```bash
cd Back-end

# 1. Infra (Postgres + Redis + RabbitMQ)
docker compose -f docker/docker-compose.yml up -d postgres redis rabbitmq

# 2. Management UI: http://localhost:15672  (psi / psi_secret por padrão)
#    AMQP:          localhost:5672

# 3. Configurar .env (copiar de .env.example e ajustar RABBITMQ_URL etc.)

# 4. API
python -m uvicorn app.main:create_app --factory --reload

# 5. Workers (terminais separados)
python -m app.workers.audit_worker
python -m app.workers.notification_worker
python -m app.workers.email_worker

# 6. Scheduler (bill.due_soon)
python -m app.workers.scheduler

# 7. Testes
pytest tests/unit -q                                        # sem broker
RABBITMQ_TEST_URL=amqp://guest:guest@localhost:5672/ \
  pytest tests/integration/test_rabbitmq_integration.py -v   # com broker real
```

Ou suba tudo com Docker:

```bash
docker compose -f docker/docker-compose.yml up -d
# inclui api, celery-worker/beat, audit-worker, notification-worker,
# email-worker e scheduler
```

### Migrations

```bash
poetry run alembic upgrade head   # aplica 0004_messaging_activity
# ou: a API cria/verifica tabelas no startup (comportamento existente)
```

---

## 16. Limitações / Decisões Registradas

1. **Outbox não implementado** — ver seção 11 (decisão deliberada e revertível).
2. **`report.viewed` apenas na página 1** da listagem de audit-logs — evita
   inundar o histórico com requests de paginação/polling.
3. **`notification.viewed` não rastreado**: o feed de notificações é consultado
   por polling (15s) — registrar cada GET poluiria a auditoria. Rastreia-se
   `notification.read` (marcação) e `notification.created` em vez disso.
4. **Scheduler config-driven**: a detecção de "próximo do vencimento" usa a
   janela `BILL_DUE_SOON_DAYS`. As preferências legadas `reminder_preferences`
   (frequência) permanecem armazenadas para uma futura feature de lembretes
   recorrentes; as preferências de canal (`email_alerts` etc.) são respeitadas
   pelos consumers.
5. **Queues classic** (não quorum): o fluxo depende de per-message TTL para
   backoff, recurso não suportado por quorum queues no RabbitMQ 4. Volume atual
   é baixo; para produção de alta escala, migrar para quorum com filas de retry
   de TTL fixo por nível de backoff.
6. **`get_engine()` adicionado a `app/shared/database.py`**: corrige chamadas
   pré-existentes que falhavam silenciosamente (persistência de análise,
   migrations no startup e health checks).
7. **Login/auth endpoints do projeto usam dados mock** (estado pré-existente);
   os eventos `user.action` publicados por eles carregam esses ids até que a
   autenticação real seja implementada.
8. **`audit_logs` reutilizado como activity_logs** (em vez de criar tabela
   nova): mesmo propósito, evita duplicação e preserva o histórico LGPD
   existente.
