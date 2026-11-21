SELECT user_id,
       COUNT(*) AS llm_calls,
       COUNT(*) FILTER (WHERE status = 'error') AS error_calls,
       COALESCE(SUM(total_tokens), 0)::bigint AS total_tokens,
       COALESCE(SUM(estimated_cost_usd), 0)::float AS estimated_cost_usd,
       AVG(latency_ms)::float AS avg_latency_ms
FROM llm_calls
WHERE (%(date_from)s::date IS NULL OR created_at >= %(date_from)s::date)
  AND (%(date_to)s::date IS NULL OR created_at < %(date_to)s::date + 1)
GROUP BY user_id
ORDER BY total_tokens DESC;
