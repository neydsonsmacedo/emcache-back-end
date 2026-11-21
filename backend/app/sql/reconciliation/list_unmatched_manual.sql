SELECT mt.id, mt.user_id, mt.data, mt.valor, mt.type, mt.categoria,
       mt.tipo_transacao, mt.origem, mt.institution_id,
       COALESCE(inst.name, mt.origem) AS institution_name,
       mt.pluggy_account_id, mt.resumo_item, mt.status, mt.created_at
FROM manual_transactions mt
LEFT JOIN institutions inst ON inst.id = mt.institution_id
WHERE mt.user_id = %(user_id)s::uuid
  AND mt.status = 'completo'
  AND mt.matched_pluggy_transaction_id IS NULL
  AND mt.valor IS NOT NULL
  AND mt.data IS NOT NULL
  AND (%(manual_transaction_id)s::uuid IS NULL OR mt.id = %(manual_transaction_id)s::uuid)
ORDER BY mt.data DESC, mt.created_at DESC;
