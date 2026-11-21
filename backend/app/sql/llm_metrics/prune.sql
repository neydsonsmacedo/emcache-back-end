DELETE FROM llm_calls
WHERE created_at < NOW() - make_interval(days => %(days)s::int);
