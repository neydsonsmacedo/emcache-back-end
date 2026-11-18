CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS transactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Dados da transação
    data DATE,                                      -- 'YYYY-MM-DD' ou NULL
    valor BIGINT,                                   -- Valor em centavos (ex: 4990 para R$ 49,90). NULL se faltar.
    categoria VARCHAR(100),                         -- Ex: 'Alimentação', 'Transporte'
    tipo_transacao VARCHAR(50),                     -- Ex: 'crédito', 'débito', 'pix', 'dinheiro'
    origem VARCHAR(100),                            -- Ex: 'Nubank', 'Itaú', 'Carteira'
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

-- Índice para ordenação rápida no histórico
CREATE INDEX IF NOT EXISTS idx_transactions_data ON transactions (data DESC);

-- ============================================================================
-- Pluggy Open Finance (tabelas dedicadas)
-- ============================================================================

CREATE TABLE IF NOT EXISTS pluggy_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pluggy_item_id VARCHAR(100) UNIQUE NOT NULL,
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

CREATE INDEX IF NOT EXISTS idx_pluggy_accounts_item ON pluggy_accounts (pluggy_item_id);
CREATE INDEX IF NOT EXISTS idx_pluggy_transactions_date ON pluggy_transactions (date DESC);
CREATE INDEX IF NOT EXISTS idx_pluggy_transactions_account ON pluggy_transactions (pluggy_account_id);
