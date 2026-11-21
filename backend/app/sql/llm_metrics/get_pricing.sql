SELECT provider, model,
       input_per_million_usd::float AS input_per_million_usd,
       output_per_million_usd::float AS output_per_million_usd,
       cached_input_per_million_usd::float AS cached_input_per_million_usd,
       audio_per_minute_usd::float AS audio_per_minute_usd
FROM llm_model_pricing;
