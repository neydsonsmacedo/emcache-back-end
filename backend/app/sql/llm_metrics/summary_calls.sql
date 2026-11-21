SELECT COUNT(*) AS llm_calls,
       COUNT(DISTINCT run_id) AS runs_with_calls,
       COUNT(*) FILTER (WHERE status = 'error') AS error_calls,
       COUNT(*) FILTER (WHERE is_fallback) AS fallback_calls,
       COALESCE(SUM(prompt_tokens), 0)::bigint AS prompt_tokens,
       COALESCE(SUM(completion_tokens), 0)::bigint AS completion_tokens,
       COALESCE(SUM(total_tokens), 0)::bigint AS total_tokens,
       COALESCE(SUM(estimated_cost_usd), 0)::float AS estimated_cost_usd,
       AVG(latency_ms)::float AS avg_latency_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)::float AS p95_latency_ms
FROM llm_calls
WHERE (%(user_id)s::uuid IS NULL OR user_id = %(user_id)s::uuid)
  AND (%(date_from)s::date IS NULL OR created_at >= %(date_from)s::date)
  AND (%(date_to)s::date IS NULL OR created_at < %(date_to)s::date + 1);
