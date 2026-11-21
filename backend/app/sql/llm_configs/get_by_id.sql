SELECT id, user_id, name, provider, base_url, api_key_encrypted, api_key_last4,
       chat_model, vision_model, transcription_model, priority, is_active,
       created_at, updated_at
FROM llm_provider_configs
WHERE id = %(id)s::uuid
  AND user_id = %(user_id)s::uuid;
