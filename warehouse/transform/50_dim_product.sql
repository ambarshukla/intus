-- dim_product: the smallest dimension, and the one most likely to be wrong.
--
-- Product code and name arrive on every subscription row, denormalised. Taking
-- DISTINCT over both columns would silently produce two rows for one product
-- the day a name is edited upstream and the extract carries both spellings —
-- and the duplicate key would only surface when a fact join started
-- multiplying rows. Grouping by the code and taking one name makes the
-- single-row-per-product rule explicit instead of accidental.

MERGE INTO warehouse.dim_product AS target
USING (
    SELECT
        product_code,
        max(product_name) AS product_name
    FROM staging.crm_subscription
    WHERE product_code IS NOT NULL AND product_code <> ''
    GROUP BY product_code
) AS source
ON target.product_code = source.product_code

WHEN MATCHED AND target.product_name IS DISTINCT FROM source.product_name
    THEN UPDATE SET product_name = source.product_name

WHEN NOT MATCHED THEN
    INSERT (product_code, product_name)
    VALUES (source.product_code, source.product_name);
