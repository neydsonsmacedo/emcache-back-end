UPDATE manual_transactions
SET data = %(data)s::date,
    valor = %(valor)s::bigint,
    type = %(type)s::varchar,
    categoria = %(categoria)s::varchar,
    tipo_transacao = %(tipo_transacao)s::varchar,
    origem = %(origem)s::varchar,
    institution_id = %(institution_id)s::uuid,
    pluggy_account_id = %(pluggy_account_id)s::varchar,
    resumo_item = %(resumo_item)s::varchar,
    status = %(status)s::varchar,
    dados_faltantes = %(dados_faltantes)s::text[],
    updated_at = NOW()
WHERE id = %(id)s::uuid
  AND (%(user_id)s::uuid IS NULL OR user_id = %(user_id)s::uuid)
RETURNING id, user_id, data, valor, type, categoria, tipo_transacao, origem,
          institution_id, pluggy_account_id, matched_pluggy_transaction_id,
          resumo_item, status, dados_faltantes, canal_entrada, raw_input,
          created_at, updated_at;
