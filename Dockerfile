# USDA Nutrition FastMCP server
#
# unRAID deployment notes:
# - Connect this container to the same Docker network as `postgresql17`
#   so it can resolve the hostname `postgresql17`.
# - Map a host port of your choice to container port 8000.
# - The MCP endpoint will be served at http://<host>:<port>/mcp
# - Supply the Postgres password via the DB_PASSWORD environment variable
#   (the default connection string is
#   postgresql://postgres:$DB_PASSWORD@postgresql17:5432/usda).
# - Or override the full connection string via DATABASE_URL if your setup
#   differs.
# - FastMCP 2.x leaves DNS rebinding protection off by default (LAN-safe).

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PASSWORD=""

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

EXPOSE 8000

CMD ["python", "server.py"]
