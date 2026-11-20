UPDATE chat_sessions
SET last_message_at = NOW(),
    updated_at = NOW()
WHERE id = %s;
