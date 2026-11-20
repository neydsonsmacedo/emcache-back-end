INSERT INTO pluggy_accounts (
    pluggy_account_id, pluggy_item_id, type, subtype, name, number,
    balance_cents, currency_code, credit_limit_cents, available_credit_limit_cents,
    raw_payload, updated_at
) VALUES (
    %(pluggy_account_id)s, %(pluggy_item_id)s, %(type)s, %(subtype)s, %(name)s, %(number)s,
    %(balance_cents)s, %(currency_code)s, %(credit_limit_cents)s, %(available_credit_limit_cents)s,
    %(raw_payload)s, NOW()
)
ON CONFLICT (pluggy_account_id) DO UPDATE SET
    type = EXCLUDED.type,
    subtype = EXCLUDED.subtype,
    name = EXCLUDED.name,
    number = EXCLUDED.number,
    balance_cents = EXCLUDED.balance_cents,
    currency_code = EXCLUDED.currency_code,
    credit_limit_cents = EXCLUDED.credit_limit_cents,
    available_credit_limit_cents = EXCLUDED.available_credit_limit_cents,
    raw_payload = EXCLUDED.raw_payload,
    updated_at = NOW();
