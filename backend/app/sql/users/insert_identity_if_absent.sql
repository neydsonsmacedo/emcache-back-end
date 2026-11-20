INSERT INTO user_identities (user_id, channel, external_id, username, metadata)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (channel, external_id) DO NOTHING;
