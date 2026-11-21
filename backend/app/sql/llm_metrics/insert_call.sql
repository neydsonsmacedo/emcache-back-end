INSERT INTO llm_calls (
    run_id, user_id, created_at, operation, provider, model, llm_config_id, attempt,
    is_fallback, status, http_status, error_type, error_message, latency_ms,
    time_to_first_token_ms, prompt_tokens, completion_tokens, total_tokens,
    cached_tokens, audio_seconds, finish_reason, tool_calls_requested,
    estimated_cost_usd, metadata
)
VALUES (
    %(run_id)s::uuid, %(user_id)s::uuid, %(created_at)s, %(operation)s, %(provider)s,
    %(model)s, %(llm_config_id)s::uuid, %(attempt)s, %(is_fallback)s, %(status)s,
    %(http_status)s, %(error_type)s, %(error_message)s, %(latency_ms)s,
    %(time_to_first_token_ms)s, %(prompt_tokens)s, %(completion_tokens)s,
    %(total_tokens)s, %(cached_tokens)s, %(audio_seconds)s, %(finish_reason)s,
    %(tool_calls_requested)s::text[], %(estimated_cost_usd)s, %(metadata)s
);
