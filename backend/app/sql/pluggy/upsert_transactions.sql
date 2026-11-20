INSERT INTO pluggy_transactions (
    pluggy_transaction_id, pluggy_account_id, pluggy_item_id,
    description, description_raw, amount_cents, currency_code,
    date, type, status, category, category_id,
    payment_method, merchant_name, raw_payload, updated_at
) VALUES (
    %(pluggy_transaction_id)s, %(pluggy_account_id)s, %(pluggy_item_id)s,
    %(description)s, %(description_raw)s, %(amount_cents)s, %(currency_code)s,
    %(date)s, %(type)s, %(status)s, %(category)s, %(category_id)s,
    %(payment_method)s, %(merchant_name)s, %(raw_payload)s, NOW()
)
ON CONFLICT (pluggy_transaction_id) DO UPDATE SET
    description = EXCLUDED.description,
    description_raw = EXCLUDED.description_raw,
    amount_cents = EXCLUDED.amount_cents,
    currency_code = EXCLUDED.currency_code,
    date = EXCLUDED.date,
    type = EXCLUDED.type,
    status = EXCLUDED.status,
    category = EXCLUDED.category,
    category_id = EXCLUDED.category_id,
    payment_method = EXCLUDED.payment_method,
    merchant_name = EXCLUDED.merchant_name,
    raw_payload = EXCLUDED.raw_payload,
    updated_at = NOW();
