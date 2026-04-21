import os
from contextlib import asynccontextmanager

import asyncpg
from fastmcp import FastMCP

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres@postgresql17:5432/usda"
)

DEFAULT_DATA_TYPES = ["foundation_food", "sr_legacy_food", "survey_fndds_food"]
VALID_DATA_TYPES = {
    "foundation_food",
    "sr_legacy_food",
    "survey_fndds_food",
    "branded_food",
}

_pool: asyncpg.Pool | None = None


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
    fn.amount AS carbs_per_100g,
    similarity(f.description, $1) AS score
FROM usda.food f
JOIN usda.food_nutrient fn ON fn.fdc_id = f.fdc_id
WHERE fn.nutrient_id = 1005
  AND f.data_type = ANY($2)
  AND f.description % $1
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
    mu.name AS unit,
    fp.gram_weight,
    ROUND(fp.gram_weight / 100.0 * fn.amount, 1) AS carbs_in_portion
FROM usda.food_portion fp
LEFT JOIN usda.measure_unit mu ON mu.id = fp.measure_unit_id
JOIN usda.food_nutrient fn ON fn.fdc_id = fp.fdc_id
    AND fn.nutrient_id = 1005
WHERE fp.fdc_id = $1
ORDER BY fp.seq_num;
"""

FOOD_SQL = """
SELECT f.description, fn.amount AS carbs_per_100g
FROM usda.food f
JOIN usda.food_nutrient fn ON fn.fdc_id = f.fdc_id
    AND fn.nutrient_id = 1005
WHERE f.fdc_id = $1;
"""


@mcp.tool
async def search_food(
    query: str, data_types: list[str] | None = None
) -> list[dict]:
    """Search USDA foods by name using trigram similarity.

    Returns the top 10 matches with carbohydrate content per 100g.
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
            "carbs_per_100g": float(r["carbs_per_100g"]),
            "similarity_score": float(r["score"]),
        }
        for r in rows
    ]


@mcp.tool
async def get_portions(fdc_id: int) -> dict:
    """Get all known portion sizes for a food, with carbs per portion.

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
            "amount": float(r["amount"]) if r["amount"] is not None else None,
            "unit": r["unit"],
            "gram_weight": float(r["gram_weight"])
            if r["gram_weight"] is not None
            else None,
            "carbs_in_portion": float(r["carbs_in_portion"])
            if r["carbs_in_portion"] is not None
            else None,
        }
        for r in portion_rows
    ]

    return {
        "fdc_id": fdc_id,
        "description": food["description"],
        "carbs_per_100g": float(food["carbs_per_100g"]),
        "portions": portions,
    }


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
