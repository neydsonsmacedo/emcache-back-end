SELECT COUNT(*) AS runs,
       COUNT(*) FILTER (WHERE status <> 'success') AS failed_runs,
       COUNT(*) FILTER (WHERE fallback_used) AS fallback_runs,
       AVG(latency_ms)::float AS avg_run_latency_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)::float AS p95_run_latency_ms,
       AVG(llm_calls)::float AS avg_llm_calls_per_run,
       AVG(tool_calls)::float AS avg_tool_calls_per_run
FROM agent_runs
WHERE (%(user_id)s::uuid IS NULL OR user_id = %(user_id)s::uuid)
  AND (%(date_from)s::date IS NULL OR started_at >= %(date_from)s::date)
  AND (%(date_to)s::date IS NULL OR started_at < %(date_to)s::date + 1);
