INSERT INTO chat_sessions (user_id, channel, external_session_id)
VALUES (%s, %s, %s)
ON CONFLICT (user_id, channel, external_session_id)
WHERE external_session_id IS NOT NULL
DO UPDATE SET updated_at = NOW()
RETURNING id, user_id, channel, external_session_id,
          pending_transaction_id, context, last_message_at,
          created_at, updated_at;
