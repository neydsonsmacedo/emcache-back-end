UPDATE manual_transactions mt
SET institution_id = inst.id
FROM institutions inst
WHERE mt.institution_id IS NULL
  AND mt.origem IS NOT NULL
  AND (
      LOWER(TRIM(mt.origem)) = LOWER(inst.name)
      OR LOWER(TRIM(mt.origem)) = inst.slug
  );
