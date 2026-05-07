import os
from contextlib import asynccontextmanager

import asyncpg
from fastmcp import FastMCP

DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    f"postgresql://postgres:{DB_PASSWORD}@postgresql17:5432/usda",
)

DEFAULT_DATA_TYPES = ["foundation_food", "sr_legacy_food", "survey_fndds_food"]
VALID_DATA_TYPES = {
    "foundation_food",
    "sr_legacy_food",
    "survey_fndds_food",
    "branded_food",
}

_pool: asyncpg.Pool | None = None


def _to_float(value) -> float | None:
    return float(value) if value is not None else None


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.execute("SET pg_trgm.similarity_threshold = 0.15")


@asynccontextmanager
async def lifespan(_: FastMCP):
    global _pool
    _pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=5,
        init=_init_connection,
    )
    try:
        yield
    finally:
        await _pool.close()
        _pool = None


mcp = FastMCP("usda-nutrition", lifespan=lifespan)


SEARCH_SQL = """
SELECT
    f.fdc_id,
    f.description,
    f.data_type,
    f.food_category_id,
    similarity(f.description, $1) AS score,
    MAX(CASE WHEN fn.nutrient_id = 1008 THEN fn.amount END) AS kcal_per_100g,
    MAX(CASE WHEN fn.nutrient_id = 1003 THEN fn.amount END) AS protein_per_100g,
    MAX(CASE WHEN fn.nutrient_id = 1004 THEN fn.amount END) AS fat_per_100g,
    MAX(CASE WHEN fn.nutrient_id = 1005 THEN fn.amount END) AS carbs_per_100g
FROM usda.food f
JOIN usda.food_nutrient fn ON fn.fdc_id = f.fdc_id
WHERE fn.nutrient_id = ANY(ARRAY[1003, 1004, 1005, 1008])
  AND f.data_type = ANY($2)
  AND f.description % $1
GROUP BY f.fdc_id, f.description, f.data_type, f.food_category_id
ORDER BY score DESC,
    CASE f.data_type
        WHEN 'foundation_food'   THEN 1
        WHEN 'sr_legacy_food'    THEN 2
        WHEN 'survey_fndds_food' THEN 3
        ELSE 4
    END
LIMIT 10;
"""

PORTIONS_SQL = """
SELECT
    fp.portion_description,
    fp.modifier,
    fp.amount,
    mu.name                                                        AS unit,
    fp.gram_weight,
    ROUND(fp.gram_weight / 100.0 * MAX(CASE WHEN fn.nutrient_id = 1008 THEN fn.amount END), 1) AS kcal_in_portion,
    ROUND(fp.gram_weight / 100.0 * MAX(CASE WHEN fn.nutrient_id = 1003 THEN fn.amount END), 1) AS protein_in_portion,
    ROUND(fp.gram_weight / 100.0 * MAX(CASE WHEN fn.nutrient_id = 1004 THEN fn.amount END), 1) AS fat_in_portion,
    ROUND(fp.gram_weight / 100.0 * MAX(CASE WHEN fn.nutrient_id = 1005 THEN fn.amount END), 1) AS carbs_in_portion
FROM usda.food_portion fp
LEFT JOIN usda.measure_unit mu ON mu.id = fp.measure_unit_id
JOIN usda.food_nutrient fn ON fn.fdc_id = fp.fdc_id
    AND fn.nutrient_id = ANY(ARRAY[1003, 1004, 1005, 1008])
WHERE fp.fdc_id = $1
GROUP BY fp.id, fp.portion_description, fp.modifier, fp.amount, mu.name, fp.gram_weight, fp.seq_num
ORDER BY fp.seq_num;
"""

FOOD_SQL = """
SELECT
    f.description,
    MAX(CASE WHEN fn.nutrient_id = 1008 THEN fn.amount END) AS kcal_per_100g,
    MAX(CASE WHEN fn.nutrient_id = 1003 THEN fn.amount END) AS protein_per_100g,
    MAX(CASE WHEN fn.nutrient_id = 1004 THEN fn.amount END) AS fat_per_100g,
    MAX(CASE WHEN fn.nutrient_id = 1005 THEN fn.amount END) AS carbs_per_100g
FROM usda.food f
JOIN usda.food_nutrient fn ON fn.fdc_id = f.fdc_id
    AND fn.nutrient_id = ANY(ARRAY[1003, 1004, 1005, 1008])
WHERE f.fdc_id = $1
GROUP BY f.description;
"""


@mcp.tool
async def search_food(
    query: str, data_types: list[str] | None = None
) -> list[dict]:
    """Search USDA foods by name using trigram similarity.

    Returns the top 10 matches with kcal, protein, fat, and carbs per 100g.
    Results are ranked by similarity, then data_type priority
    (foundation_food > sr_legacy_food > survey_fndds_food).

    Args:
        query: Food name to search for.
        data_types: Optional filter. Valid values: foundation_food,
            sr_legacy_food, survey_fndds_food, branded_food.
            Defaults to all except branded_food.
    """
    types = data_types if data_types else DEFAULT_DATA_TYPES
    invalid = [t for t in types if t not in VALID_DATA_TYPES]
    if invalid:
        raise ValueError(f"Invalid data_types: {invalid}")

    async with _pool.acquire() as conn:
        rows = await conn.fetch(SEARCH_SQL, query, types)

    return [
        {
            "fdc_id": r["fdc_id"],
            "description": r["description"],
            "data_type": r["data_type"],
            "food_category": r["food_category_id"],
            "kcal_per_100g": _to_float(r["kcal_per_100g"]),
            "protein_per_100g": _to_float(r["protein_per_100g"]),
            "fat_per_100g": _to_float(r["fat_per_100g"]),
            "carbs_per_100g": _to_float(r["carbs_per_100g"]),
            "similarity_score": float(r["score"]),
        }
        for r in rows
    ]


@mcp.tool
async def get_portions(fdc_id: int) -> dict:
    """Get all known portion sizes for a food, with kcal, protein, fat,
    and carbs per portion.

    Lets the agent answer things like "how many carbs in 1 medium banana"
    instead of only per-100g values.

    Args:
        fdc_id: USDA FoodData Central id of the food.
    """
    async with _pool.acquire() as conn:
        food = await conn.fetchrow(FOOD_SQL, fdc_id)
        if food is None:
            raise ValueError(f"No food found with fdc_id={fdc_id}")
        portion_rows = await conn.fetch(PORTIONS_SQL, fdc_id)

    portions = [
        {
            "portion_description": r["portion_description"],
            "modifier": r["modifier"],
            "amount": _to_float(r["amount"]),
            "unit": r["unit"],
            "gram_weight": _to_float(r["gram_weight"]),
            "kcal_in_portion": _to_float(r["kcal_in_portion"]),
            "protein_in_portion": _to_float(r["protein_in_portion"]),
            "fat_in_portion": _to_float(r["fat_in_portion"]),
            "carbs_in_portion": _to_float(r["carbs_in_portion"]),
        }
        for r in portion_rows
    ]

    return {
        "fdc_id": fdc_id,
        "description": food["description"],
        "kcal_per_100g": _to_float(food["kcal_per_100g"]),
        "protein_per_100g": _to_float(food["protein_per_100g"]),
        "fat_per_100g": _to_float(food["fat_per_100g"]),
        "carbs_per_100g": _to_float(food["carbs_per_100g"]),
        "portions": portions,
    }


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
