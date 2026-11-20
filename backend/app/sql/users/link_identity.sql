INSERT INTO user_identities (user_id, channel, external_id, username, metadata)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (channel, external_id) DO UPDATE SET
    user_id = EXCLUDED.user_id,
    username = COALESCE(EXCLUDED.username, user_identities.username),
    metadata = COALESCE(EXCLUDED.metadata, user_identities.metadata),
    updated_at = NOW()
RETURNING id, user_id, channel, external_id, username, metadata, created_at, updated_at;
