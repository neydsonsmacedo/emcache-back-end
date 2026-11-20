INSERT INTO chat_messages (
    session_id, user_id, role, content, channel, input_type, metadata
)
VALUES (%s, %s, %s, %s, %s, %s, %s)
RETURNING id, session_id, user_id, role, content, channel, input_type, metadata, created_at;
