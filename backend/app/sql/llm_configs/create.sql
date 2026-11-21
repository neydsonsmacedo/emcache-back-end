INSERT INTO llm_provider_configs (
    user_id, name, provider, base_url, api_key_encrypted, api_key_last4,
    chat_model, vision_model, transcription_model, priority, is_active
)
VALUES (
    %(user_id)s::uuid, %(name)s, %(provider)s, %(base_url)s, %(api_key_encrypted)s,
    %(api_key_last4)s, %(chat_model)s, %(vision_model)s, %(transcription_model)s,
    %(priority)s::smallint, %(is_active)s::boolean
)
RETURNING id, user_id, name, provider, base_url, api_key_encrypted, api_key_last4,
          chat_model, vision_model, transcription_model, priority, is_active,
          created_at, updated_at;
