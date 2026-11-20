SELECT u.id, u.name, u.email, u.created_at, u.updated_at,
       ui.channel, ui.external_id, ui.username
FROM user_identities ui
JOIN users u ON u.id = ui.user_id
WHERE ui.channel = %s
  AND ui.external_id = %s;
