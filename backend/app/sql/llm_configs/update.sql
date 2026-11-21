UPDATE llm_provider_configs
SET name = %(name)s,
    provider = %(provider)s,
    base_url = %(base_url)s,
    api_key_encrypted = %(api_key_encrypted)s,
    api_key_last4 = %(api_key_last4)s,
    chat_model = %(chat_model)s,
    vision_model = %(vision_model)s,
    transcription_model = %(transcription_model)s,
    priority = %(priority)s::smallint,
    is_active = %(is_active)s::boolean,
    updated_at = NOW()
WHERE id = %(id)s::uuid
  AND user_id = %(user_id)s::uuid
RETURNING id, user_id, name, provider, base_url, api_key_encrypted, api_key_last4,
          chat_model, vision_model, transcription_model, priority, is_active,
          created_at, updated_at;
