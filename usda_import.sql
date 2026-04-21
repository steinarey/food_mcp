-- =============================================================================
-- USDA FoodData Central — PostgreSQL 17 Import
-- =============================================================================
--
-- USAGE:
--   docker exec -i <container> psql -U <user> -d <database> \
--     -f /usda_import/usda_fdc_import.sql
--
-- PREREQUISITES:
--   - CSV files must be mounted into the container at /usda_import/
--   - Files required: food_category.csv, measure_unit.csv, nutrient.csv,
--     food_nutrient_derivation.csv, food.csv, food_nutrient.csv, food_portion.csv
--   - All from the "Full Download of All Data Types" at https://fdc.nal.usda.gov
--
-- NOTES:
--   - Creates a 'usda' schema. Safe to re-run: drops and recreates all tables.
--   - food_nutrient is large (~27M rows), expect 5-15 min depending on disk speed.
--   - food_portion is staged through a temp table to filter rows with empty fdc_id.
--   - Carbohydrate nutrient_id is 1005 ("Carbohydrate, by difference", grams/100g).
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 0. Extensions and schema
-- -----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pg_trgm;

DROP SCHEMA IF EXISTS usda CASCADE;
CREATE SCHEMA usda;

-- Include public in search_path so gin_trgm_ops is found
SET search_path = usda, public;
SET work_mem = '256MB';

-- -----------------------------------------------------------------------------
-- 1. Lookup tables
-- -----------------------------------------------------------------------------

CREATE TABLE food_category (
    id          INTEGER PRIMARY KEY,
    code        TEXT,
    description TEXT NOT NULL
);

-- measure_unit has only id and name in current release (no abbreviation column)
CREATE TABLE measure_unit (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

-- rank is NUMERIC as some rows use float values, and can be empty
CREATE TABLE nutrient (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    unit_name    TEXT,
    nutrient_nbr TEXT,
    rank         NUMERIC(8, 1)
);

-- source_id not present in current release
CREATE TABLE food_nutrient_derivation (
    id          INTEGER PRIMARY KEY,
    code        TEXT NOT NULL,
    description TEXT
);

-- -----------------------------------------------------------------------------
-- 2. Food master
-- food_category_id is a text description in current release, not a FK integer
-- food.csv has only 5 columns in current release (no scientific_name/food_key)
-- -----------------------------------------------------------------------------

CREATE TABLE food (
    fdc_id           INTEGER PRIMARY KEY,
    data_type        TEXT NOT NULL,
    description      TEXT NOT NULL,
    food_category_id TEXT,
    publication_date DATE
);

CREATE INDEX food_description_trgm_idx ON food USING gin (description gin_trgm_ops);
CREATE INDEX food_data_type_idx        ON food (data_type);

-- -----------------------------------------------------------------------------
-- 3. Nutrient values (~27M rows)
-- amount uses NUMERIC(20,4) to handle scientific notation values (e.g. 1.96E8)
-- All nullable columns use FORCE_NULL to handle quoted empty strings ("")
-- -----------------------------------------------------------------------------

CREATE TABLE food_nutrient (
    id                  BIGINT PRIMARY KEY,
    fdc_id              INTEGER NOT NULL REFERENCES food(fdc_id),
    nutrient_id         INTEGER NOT NULL REFERENCES nutrient(id),
    amount              NUMERIC(20, 4),
    data_points         INTEGER,
    derivation_id       INTEGER REFERENCES food_nutrient_derivation(id),
    min                 NUMERIC(20, 4),
    max                 NUMERIC(20, 4),
    median              NUMERIC(20, 4),
    loq                 NUMERIC(20, 4),
    footnote            TEXT,
    min_year_acquired   INTEGER,
    percent_daily_value NUMERIC(8, 2)
);

CREATE INDEX food_nutrient_fdc_id_idx      ON food_nutrient (fdc_id);
CREATE INDEX food_nutrient_nutrient_id_idx ON food_nutrient (nutrient_id);

-- -----------------------------------------------------------------------------
-- 4. Portions
-- Staged through a temp table to filter rows with empty fdc_id
-- -----------------------------------------------------------------------------

CREATE TABLE food_portion (
    id                  INTEGER PRIMARY KEY,
    fdc_id              INTEGER NOT NULL REFERENCES food(fdc_id),
    seq_num             INTEGER,
    amount              NUMERIC(10, 4),
    measure_unit_id     INTEGER REFERENCES measure_unit(id),
    portion_description TEXT,
    modifier            TEXT,
    gram_weight         NUMERIC(10, 4),
    data_points         INTEGER,
    footnote            TEXT,
    min_year_acquired   INTEGER
);

CREATE INDEX food_portion_fdc_id_idx ON food_portion (fdc_id);

-- =============================================================================
-- IMPORT
-- Disable FK checks during bulk load for speed
-- =============================================================================

SET session_replication_role = replica;

\echo '--- Importing food_category ---'
COPY food_category (id, code, description)
FROM '/usda_import/food_category.csv'
WITH (FORMAT csv, HEADER true, NULL '');

\echo '--- Importing measure_unit ---'
COPY measure_unit (id, name)
FROM '/usda_import/measure_unit.csv'
WITH (FORMAT csv, HEADER true, NULL '');

\echo '--- Importing nutrient ---'
COPY nutrient (id, name, unit_name, nutrient_nbr, rank)
FROM '/usda_import/nutrient.csv'
WITH (FORMAT csv, HEADER true, NULL '', FORCE_NULL (rank, unit_name, nutrient_nbr));

\echo '--- Importing food_nutrient_derivation ---'
COPY food_nutrient_derivation (id, code, description)
FROM '/usda_import/food_nutrient_derivation.csv'
WITH (FORMAT csv, HEADER true, NULL '');

\echo '--- Importing food ---'
COPY food (fdc_id, data_type, description, food_category_id, publication_date)
FROM '/usda_import/food.csv'
WITH (FORMAT csv, HEADER true, NULL '', FORCE_NULL (food_category_id, publication_date));

\echo '--- Importing food_nutrient (large, may take 5-15 minutes) ---'
COPY food_nutrient (id, fdc_id, nutrient_id, amount, data_points, derivation_id,
                    min, max, median, loq, footnote, min_year_acquired, percent_daily_value)
FROM '/usda_import/food_nutrient.csv'
WITH (FORMAT csv, HEADER true, NULL '', FORCE_NULL (
    amount, data_points, derivation_id, min, max, median,
    loq, footnote, min_year_acquired, percent_daily_value
));

\echo '--- Staging food_portion (filtering rows with empty fdc_id) ---'
CREATE TEMP TABLE food_portion_stage (
    id                  TEXT,
    fdc_id              TEXT,
    seq_num             TEXT,
    amount              TEXT,
    measure_unit_id     TEXT,
    portion_description TEXT,
    modifier            TEXT,
    gram_weight         TEXT,
    data_points         TEXT,
    footnote            TEXT,
    min_year_acquired   TEXT
);

COPY food_portion_stage (id, fdc_id, seq_num, amount, measure_unit_id, portion_description,
                          modifier, gram_weight, data_points, footnote, min_year_acquired)
FROM '/usda_import/food_portion.csv'
WITH (FORMAT csv, HEADER true, NULL '');

INSERT INTO food_portion
SELECT
    id::INTEGER,
    fdc_id::INTEGER,
    NULLIF(seq_num, '')::INTEGER,
    NULLIF(amount, '')::NUMERIC,
    NULLIF(measure_unit_id, '')::INTEGER,
    NULLIF(portion_description, ''),
    NULLIF(modifier, ''),
    NULLIF(gram_weight, '')::NUMERIC,
    NULLIF(data_points, '')::INTEGER,
    NULLIF(footnote, ''),
    NULLIF(min_year_acquired, '')::INTEGER
FROM food_portion_stage
WHERE fdc_id IS NOT NULL AND fdc_id != '';

SET session_replication_role = DEFAULT;

-- -----------------------------------------------------------------------------
-- Post-import indexes and statistics
-- -----------------------------------------------------------------------------

\echo '--- Building carb partial index ---'
CREATE INDEX food_nutrient_carbs_idx
    ON food_nutrient (fdc_id, amount)
    WHERE nutrient_id = 1005;

\echo '--- Analyzing tables ---'
ANALYZE food_category;
ANALYZE measure_unit;
ANALYZE nutrient;
ANALYZE food_nutrient_derivation;
ANALYZE food;
ANALYZE food_nutrient;
ANALYZE food_portion;

-- =============================================================================
-- Verification
-- =============================================================================

\echo '--- Row counts ---'
SELECT 'food_category'          AS tbl, COUNT(*) FROM usda.food_category
UNION ALL
SELECT 'measure_unit',                  COUNT(*) FROM usda.measure_unit
UNION ALL
SELECT 'nutrient',                      COUNT(*) FROM usda.nutrient
UNION ALL
SELECT 'food_nutrient_derivation',      COUNT(*) FROM usda.food_nutrient_derivation
UNION ALL
SELECT 'food',                          COUNT(*) FROM usda.food
UNION ALL
SELECT 'food_nutrient',                 COUNT(*) FROM usda.food_nutrient
UNION ALL
SELECT 'food_portion',                  COUNT(*) FROM usda.food_portion;

\echo '--- Carbohydrate nutrients (1005 should be primary) ---'
SELECT id, name, unit_name, nutrient_nbr
FROM usda.nutrient
WHERE name ILIKE '%carbohydrate%'
ORDER BY name;

\echo '--- Sample carb query (banana) ---'
SELECT
    f.fdc_id,
    f.description,
    f.data_type,
    fn.amount AS carbs_per_100g
FROM usda.food f
JOIN usda.food_nutrient fn ON fn.fdc_id = f.fdc_id
WHERE fn.nutrient_id = 1005
  AND f.description ILIKE '%banana%'
  AND f.data_type IN ('foundation_food', 'sr_legacy_food')
ORDER BY
    CASE f.data_type
        WHEN 'foundation_food' THEN 1
        WHEN 'sr_legacy_food'  THEN 2
    END
LIMIT 5;

\echo '--- Import complete ---'