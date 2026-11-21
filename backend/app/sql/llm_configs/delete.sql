DELETE FROM llm_provider_configs
WHERE id = %(id)s::uuid
  AND user_id = %(user_id)s::uuid
RETURNING id;
