CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- Dimensão de Instituições Financeiras / Origens
-- ============================================================================

CREATE TABLE IF NOT EXISTS institutions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(150) NOT NULL,
    slug VARCHAR(100) UNIQUE NOT NULL,
    pluggy_connector_id INTEGER UNIQUE,                 -- NULL para origens manuais (ex: Carteira, Dinheiro)
    kind VARCHAR(30) NOT NULL DEFAULT 'bank',           -- 'bank' | 'wallet' | 'benefit'
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

-- ============================================================================
-- Pluggy Open Finance (tabelas dedicadas)
-- ============================================================================

CREATE TABLE IF NOT EXISTS pluggy_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pluggy_item_id VARCHAR(100) UNIQUE NOT NULL,
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

CREATE TABLE IF NOT EXISTS pluggy_accounts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pluggy_account_id VARCHAR(100) UNIQUE NOT NULL,
    pluggy_item_id VARCHAR(100) NOT NULL REFERENCES pluggy_items(pluggy_item_id) ON DELETE CASCADE,
    type VARCHAR(50) NOT NULL,                        -- 'BANK' | 'CREDIT'
    subtype VARCHAR(50),                              -- 'CHECKINGS_ACCOUNT' | 'SAVINGS_ACCOUNT' | 'CREDIT_CARD'
    name VARCHAR(255),
    number VARCHAR(100),
    balance_cents BIGINT NOT NULL DEFAULT 0,          -- Saldo em centavos
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
    amount_cents BIGINT NOT NULL,                     -- Valor em centavos
    currency_code VARCHAR(10) NOT NULL DEFAULT 'BRL',
    date TIMESTAMPTZ NOT NULL,
    type VARCHAR(20) NOT NULL,                        -- 'DEBIT' | 'CREDIT'
    status VARCHAR(20),                               -- 'POSTED' | 'PENDING'
    category VARCHAR(150),
    category_id VARCHAR(50),
    payment_method VARCHAR(50),
    merchant_name VARCHAR(255),
    raw_payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- Transações Manuais (chat / áudio / imagem)
-- ============================================================================

CREATE TABLE IF NOT EXISTS manual_transactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Dados da transação
    data DATE,                                      -- 'YYYY-MM-DD' ou NULL
    valor BIGINT,                                   -- Valor em centavos (ex: 4990 para R$ 49,90). NULL se faltar.
    type VARCHAR(20) NOT NULL DEFAULT 'DEBIT',      -- 'DEBIT' | 'CREDIT'
    categoria VARCHAR(100),                         -- Ex: 'Alimentação', 'Transporte'
    tipo_transacao VARCHAR(50),                     -- Ex: 'crédito', 'débito', 'pix', 'dinheiro'
    origem VARCHAR(100),                            -- Texto histórico/fallback (ex: 'Nubank', 'Itaú', 'Carteira')
    institution_id UUID REFERENCES institutions(id) ON DELETE SET NULL,
    pluggy_account_id VARCHAR(100) REFERENCES pluggy_accounts(pluggy_account_id) ON DELETE SET NULL,
    matched_pluggy_transaction_id VARCHAR(100) UNIQUE REFERENCES pluggy_transactions(pluggy_transaction_id) ON DELETE SET NULL,
    resumo_item VARCHAR(255) NOT NULL,              -- Descrição básica do item/gasto

    -- Controle de completude
    status VARCHAR(20) NOT NULL DEFAULT 'pendente', -- 'completo' | 'pendente'
    dados_faltantes TEXT[] DEFAULT '{}',            -- Lista de campos faltantes ex: ARRAY['origem', 'valor']

    -- Metadados operacionais
    canal_entrada VARCHAR(20) NOT NULL,             -- 'text', 'audio', 'image'
    raw_input TEXT,                                 -- Texto/entrada bruta recebida
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pluggy_items_institution ON pluggy_items (institution_id);
CREATE INDEX IF NOT EXISTS idx_pluggy_accounts_item ON pluggy_accounts (pluggy_item_id);
CREATE INDEX IF NOT EXISTS idx_pluggy_transactions_date ON pluggy_transactions (date DESC);
CREATE INDEX IF NOT EXISTS idx_pluggy_transactions_account ON pluggy_transactions (pluggy_account_id);
CREATE INDEX IF NOT EXISTS idx_manual_transactions_data ON manual_transactions (data DESC);
CREATE INDEX IF NOT EXISTS idx_manual_transactions_institution ON manual_transactions (institution_id);
CREATE INDEX IF NOT EXISTS idx_manual_transactions_status ON manual_transactions (status);

-- ============================================================================
-- Visão Unificada (Pluggy + Manuais completas e não conciliadas)
-- ============================================================================

CREATE OR REPLACE VIEW unified_transactions AS
SELECT
    pt.id::TEXT AS id,
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
