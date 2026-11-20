SELECT pluggy_account_id, pluggy_item_id
FROM pluggy_accounts
WHERE pluggy_account_id = ANY(%s);
