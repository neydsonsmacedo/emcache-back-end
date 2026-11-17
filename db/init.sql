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
