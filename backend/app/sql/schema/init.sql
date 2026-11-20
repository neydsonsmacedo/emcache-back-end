CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(150),
    email VARCHAR(255) UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_identities (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel VARCHAR(30) NOT NULL,
    external_id VARCHAR(150) NOT NULL,
    username VARCHAR(150),
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (channel, external_id)
);

CREATE TABLE IF NOT EXISTS institutions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(150) NOT NULL,
    slug VARCHAR(100) UNIQUE NOT NULL,
    pluggy_connector_id INTEGER UNIQUE,
    kind VARCHAR(30) NOT NULL DEFAULT 'bank',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO institutions (name, slug, pluggy_connector_id, kind)
VALUES
    ('Carteira', 'carteira', NULL, 'wallet'),
    ('Dinheiro', 'dinheiro', NULL, 'wallet'),
    ('Vale Alimentação', 'vale-alimentacao', NULL, 'benefit'),
    ('Vale Refeição', 'vale-refeicao', NULL, 'benefit')
ON CONFLICT (slug) DO NOTHING;

CREATE TABLE IF NOT EXISTS pluggy_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pluggy_item_id VARCHAR(100) UNIQUE NOT NULL,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    institution_id UUID REFERENCES institutions(id) ON DELETE SET NULL,
    connector_id INTEGER,
    connector_name VARCHAR(150),
    status VARCHAR(50) NOT NULL DEFAULT 'UPDATING',
    execution_status VARCHAR(50),
    client_user_id VARCHAR(150),
    last_updated_at TIMESTAMPTZ,
    next_auto_sync_at TIMESTAMPTZ,
    raw_payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE pluggy_items
    ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS institution_id UUID REFERENCES institutions(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS pluggy_accounts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pluggy_account_id VARCHAR(100) UNIQUE NOT NULL,
    pluggy_item_id VARCHAR(100) NOT NULL REFERENCES pluggy_items(pluggy_item_id) ON DELETE CASCADE,
    type VARCHAR(50) NOT NULL,
    subtype VARCHAR(50),
    name VARCHAR(255),
    number VARCHAR(100),
    balance_cents BIGINT NOT NULL DEFAULT 0,
    currency_code VARCHAR(10) NOT NULL DEFAULT 'BRL',
    credit_limit_cents BIGINT,
    available_credit_limit_cents BIGINT,
    raw_payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pluggy_transactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pluggy_transaction_id VARCHAR(100) UNIQUE NOT NULL,
    pluggy_account_id VARCHAR(100) NOT NULL REFERENCES pluggy_accounts(pluggy_account_id) ON DELETE CASCADE,
    pluggy_item_id VARCHAR(100) NOT NULL REFERENCES pluggy_items(pluggy_item_id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    description_raw TEXT,
    amount_cents BIGINT NOT NULL,
    currency_code VARCHAR(10) NOT NULL DEFAULT 'BRL',
    date TIMESTAMPTZ NOT NULL,
    type VARCHAR(20) NOT NULL,
    status VARCHAR(20),
    category VARCHAR(150),
    category_id VARCHAR(50),
    payment_method VARCHAR(50),
    merchant_name VARCHAR(255),
    raw_payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

DO $$
BEGIN
    IF to_regclass('public.transactions') IS NOT NULL
       AND to_regclass('public.manual_transactions') IS NULL THEN
        ALTER TABLE transactions RENAME TO manual_transactions;
    END IF;
END $$;

ALTER INDEX IF EXISTS idx_transactions_data RENAME TO idx_manual_transactions_data;

CREATE TABLE IF NOT EXISTS manual_transactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    data DATE,
    valor BIGINT,
    type VARCHAR(20) NOT NULL DEFAULT 'DEBIT',
    categoria VARCHAR(100),
    tipo_transacao VARCHAR(50),
    origem VARCHAR(100),
    institution_id UUID REFERENCES institutions(id) ON DELETE SET NULL,
    pluggy_account_id VARCHAR(100) REFERENCES pluggy_accounts(pluggy_account_id) ON DELETE SET NULL,
    matched_pluggy_transaction_id VARCHAR(100) UNIQUE REFERENCES pluggy_transactions(pluggy_transaction_id) ON DELETE SET NULL,
    resumo_item VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pendente',
    dados_faltantes TEXT[] DEFAULT '{}',
    canal_entrada VARCHAR(20) NOT NULL,
    raw_input TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE manual_transactions
    ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS type VARCHAR(20) NOT NULL DEFAULT 'DEBIT',
    ADD COLUMN IF NOT EXISTS institution_id UUID REFERENCES institutions(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS pluggy_account_id VARCHAR(100) REFERENCES pluggy_accounts(pluggy_account_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS matched_pluggy_transaction_id VARCHAR(100) UNIQUE REFERENCES pluggy_transactions(pluggy_transaction_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_user_identities_user ON user_identities (user_id, channel);
CREATE INDEX IF NOT EXISTS idx_pluggy_items_user ON pluggy_items (user_id);
CREATE INDEX IF NOT EXISTS idx_pluggy_items_client_user ON pluggy_items (client_user_id);
CREATE INDEX IF NOT EXISTS idx_pluggy_items_institution ON pluggy_items (institution_id);
CREATE INDEX IF NOT EXISTS idx_pluggy_accounts_item ON pluggy_accounts (pluggy_item_id);
CREATE INDEX IF NOT EXISTS idx_pluggy_transactions_date ON pluggy_transactions (date DESC);
CREATE INDEX IF NOT EXISTS idx_pluggy_transactions_account ON pluggy_transactions (pluggy_account_id);
CREATE INDEX IF NOT EXISTS idx_manual_transactions_user ON manual_transactions (user_id);
CREATE INDEX IF NOT EXISTS idx_manual_transactions_data ON manual_transactions (data DESC);
CREATE INDEX IF NOT EXISTS idx_manual_transactions_institution ON manual_transactions (institution_id);
CREATE INDEX IF NOT EXISTS idx_manual_transactions_status ON manual_transactions (status);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel VARCHAR(30) NOT NULL,
    external_session_id VARCHAR(150),
    pending_transaction_id UUID REFERENCES manual_transactions(id) ON DELETE SET NULL,
    context JSONB DEFAULT '{}'::jsonb,
    last_message_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_sessions_user_channel_ext
    ON chat_sessions (user_id, channel, external_session_id)
    WHERE external_session_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_sessions_user_channel_default
    ON chat_sessions (user_id, channel)
    WHERE external_session_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions (user_id, last_message_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    channel VARCHAR(30) NOT NULL,
    input_type VARCHAR(20) NOT NULL DEFAULT 'text',
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages (session_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_chat_messages_user ON chat_messages (user_id, created_at DESC);

DROP VIEW IF EXISTS unified_transactions;

CREATE VIEW unified_transactions AS
SELECT
    pt.id::TEXT AS id,
    pi.user_id,
    'pluggy'::VARCHAR(20) AS source,
    pt.date AS occurred_at,
    pt.date::DATE AS transaction_date,
    ABS(pt.amount_cents) AS amount_cents,
    pt.currency_code,
    pt.type,
    pt.status,
    pt.description,
    pt.category,
    pt.payment_method,
    pt.merchant_name,
    pi.institution_id,
    COALESCE(inst.name, pi.connector_name) AS institution_name,
    pt.pluggy_account_id AS account_id,
    pa.name AS account_name,
    pt.pluggy_transaction_id,
    pt.created_at
FROM pluggy_transactions pt
JOIN pluggy_accounts pa ON pa.pluggy_account_id = pt.pluggy_account_id
JOIN pluggy_items pi ON pi.pluggy_item_id = pt.pluggy_item_id
LEFT JOIN institutions inst ON inst.id = pi.institution_id

UNION ALL

SELECT
    mt.id::TEXT AS id,
    mt.user_id,
    'manual'::VARCHAR(20) AS source,
    COALESCE(mt.data::TIMESTAMPTZ, mt.created_at) AS occurred_at,
    COALESCE(mt.data, mt.created_at::DATE) AS transaction_date,
    COALESCE(mt.valor, 0) AS amount_cents,
    'BRL'::VARCHAR(10) AS currency_code,
    mt.type,
    CASE WHEN mt.status = 'completo' THEN 'POSTED' ELSE 'PENDING' END AS status,
    mt.resumo_item AS description,
    mt.categoria AS category,
    mt.tipo_transacao AS payment_method,
    NULL::VARCHAR(255) AS merchant_name,
    mt.institution_id,
    COALESCE(inst.name, mt.origem) AS institution_name,
    mt.pluggy_account_id AS account_id,
    pa.name AS account_name,
    mt.matched_pluggy_transaction_id AS pluggy_transaction_id,
    mt.created_at
FROM manual_transactions mt
LEFT JOIN institutions inst ON inst.id = mt.institution_id
LEFT JOIN pluggy_accounts pa ON pa.pluggy_account_id = mt.pluggy_account_id
WHERE mt.status = 'completo'
  AND mt.matched_pluggy_transaction_id IS NULL;
