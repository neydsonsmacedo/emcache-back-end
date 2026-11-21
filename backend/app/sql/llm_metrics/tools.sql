SELECT tool AS tool_name, COUNT(*) AS uses
FROM agent_runs, unnest(tools_used) AS tool
WHERE (%(user_id)s::uuid IS NULL OR user_id = %(user_id)s::uuid)
  AND (%(date_from)s::date IS NULL OR started_at >= %(date_from)s::date)
  AND (%(date_to)s::date IS NULL OR started_at < %(date_to)s::date + 1)
GROUP BY tool
ORDER BY uses DESC;
