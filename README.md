# food_mcp

A small [FastMCP](https://github.com/jlowin/fastmcp) server that exposes the
publicly available [USDA FoodData Central](https://fdc.nal.usda.gov) nutrition
dataset as MCP tools, aimed at **carb counting** for a home AI agent (e.g.
estimating carbohydrates in a meal from a photo).

## Tools

- `search_food(query, data_types=None)` — fuzzy-search foods by name using
  PostgreSQL trigram similarity, returns top 10 matches with carbs per 100 g.
- `get_portions(fdc_id)` — returns known portion sizes for a food with the
  carbohydrate amount pre-calculated for each portion.

Carbohydrate values come from USDA nutrient id `1005`
("Carbohydrate, by difference", grams per 100 g).

## Contents

- `server.py` — FastMCP server (HTTP streaming transport on port 8000).
- `Dockerfile` / `requirements.txt` — container build.
- `usda_import.sql` — one-shot PostgreSQL 17 import script for the USDA
  FoodData Central CSV dump. Creates a `usda` schema, loads the CSVs, and
  builds the indexes (including trigram + carb partial index) that the
  server queries.

## Setup

1. Download the "Full Download of All Data Types" CSV bundle from
   <https://fdc.nal.usda.gov> and mount it into your Postgres container at
   `/usda_import/`.
2. Run `usda_import.sql` against a Postgres 17 database named `usda`.
3. Build and run the MCP server:

   ```sh
   docker build -t food-mcp .
   docker run -d --name food-mcp \
     --network <postgres-network> \
     -e DATABASE_URL=postgresql://postgres@postgresql17:5432/usda \
     -p 8000:8000 food-mcp
   ```

The MCP endpoint is served at `http://<host>:8000/mcp`.

## Data

All nutrition data is from the USDA FoodData Central program and is in the
public domain. This repo contains no USDA data — only the import script
and the server that queries it.
