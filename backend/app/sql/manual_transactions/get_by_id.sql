SELECT mt.id, mt.user_id, mt.data, mt.valor, mt.type, mt.categoria,
       mt.tipo_transacao, mt.origem, mt.institution_id,
       COALESCE(inst.name, mt.origem) AS institution_name,
       mt.pluggy_account_id, mt.matched_pluggy_transaction_id,
       mt.resumo_item, mt.status, mt.dados_faltantes,
       mt.canal_entrada, mt.raw_input, mt.created_at, mt.updated_at
FROM manual_transactions mt
LEFT JOIN institutions inst ON inst.id = mt.institution_id
WHERE mt.id = %(id)s::uuid
  AND (%(user_id)s::uuid IS NULL OR mt.user_id = %(user_id)s::uuid)
LIMIT 1;
