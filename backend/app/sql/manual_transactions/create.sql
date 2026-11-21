INSERT INTO manual_transactions (
    user_id,
    data,
    valor,
    type,
    categoria,
    tipo_transacao,
    origem,
    institution_id,
    pluggy_account_id,
    resumo_item,
    status,
    dados_faltantes,
    canal_entrada,
    raw_input
)
VALUES (
    %(user_id)s::uuid,
    %(data)s::date,
    %(valor)s::bigint,
    %(type)s::varchar,
    %(categoria)s::varchar,
    %(tipo_transacao)s::varchar,
    %(origem)s::varchar,
    %(institution_id)s::uuid,
    %(pluggy_account_id)s::varchar,
    %(resumo_item)s::varchar,
    %(status)s::varchar,
    %(dados_faltantes)s::text[],
    %(canal_entrada)s::varchar,
    %(raw_input)s::text
)
RETURNING id, user_id, data, valor, type, categoria, tipo_transacao, origem,
          institution_id, pluggy_account_id, matched_pluggy_transaction_id,
          resumo_item, status, dados_faltantes, canal_entrada, raw_input,
          created_at, updated_at;
