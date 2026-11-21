SELECT pt.pluggy_transaction_id,
       pt.pluggy_account_id,
       pt.pluggy_item_id,
       pi.user_id,
       pi.institution_id,
       ABS(pt.amount_cents)::BIGINT AS amount_cents,
       pt.date::DATE AS transaction_date,
       pt.type,
       pt.description,
       (
           SELECT mt.id
           FROM manual_transactions mt
           WHERE mt.matched_pluggy_transaction_id = pt.pluggy_transaction_id
           LIMIT 1
       ) AS already_matched_manual_id
FROM pluggy_transactions pt
JOIN pluggy_items pi ON pi.pluggy_item_id = pt.pluggy_item_id
WHERE pt.pluggy_transaction_id = %(pluggy_transaction_id)s::varchar
LIMIT 1;
