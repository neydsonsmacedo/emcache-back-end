INSERT INTO users (name, email)
VALUES (%s, %s)
RETURNING id, name, email, created_at, updated_at;
