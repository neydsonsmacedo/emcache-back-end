DELETE FROM pluggy_transactions
WHERE pluggy_transaction_id = ANY(%s);
