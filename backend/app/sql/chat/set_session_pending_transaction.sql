UPDATE chat_sessions
SET pending_transaction_id = %s,
    updated_at = NOW()
WHERE id = %s;
