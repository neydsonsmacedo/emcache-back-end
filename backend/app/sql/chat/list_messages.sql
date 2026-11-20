SELECT id, session_id, user_id, role, content, channel, input_type, metadata, created_at
FROM (
    SELECT id, session_id, user_id, role, content, channel, input_type, metadata, created_at
    FROM chat_messages
    WHERE session_id = %s
    ORDER BY created_at DESC
    LIMIT %s
) recent
ORDER BY created_at ASC;
