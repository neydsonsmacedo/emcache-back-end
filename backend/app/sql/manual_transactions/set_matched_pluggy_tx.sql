UPDATE manual_transactions
SET matched_pluggy_transaction_id = %(pluggy_transaction_id)s::varchar,
    updated_at = NOW()
WHERE id = %(id)s::uuid
  AND (%(user_id)s::uuid IS NULL OR user_id = %(user_id)s::uuid)
RETURNING id, user_id, data, valor, type, categoria, tipo_transacao, origem,
          institution_id, pluggy_account_id, matched_pluggy_transaction_id,
          resumo_item, status, dados_faltantes, canal_entrada, raw_input,
          created_at, updated_at;
