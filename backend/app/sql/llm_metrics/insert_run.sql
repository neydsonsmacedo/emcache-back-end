INSERT INTO agent_runs (
    id, user_id, session_id, channel, input_type, status, started_at, finished_at,
    latency_ms, llm_calls, tool_calls, tools_used, prompt_tokens, completion_tokens,
    total_tokens, estimated_cost_usd, fallback_used, transaction_id,
    error_type, error_message
)
VALUES (
    %(id)s::uuid, %(user_id)s::uuid, %(session_id)s::uuid, %(channel)s, %(input_type)s,
    %(status)s, %(started_at)s, %(finished_at)s, %(latency_ms)s, %(llm_calls)s,
    %(tool_calls)s, %(tools_used)s::text[], %(prompt_tokens)s, %(completion_tokens)s,
    %(total_tokens)s, %(estimated_cost_usd)s, %(fallback_used)s,
    %(transaction_id)s::uuid, %(error_type)s, %(error_message)s
);
