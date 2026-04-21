# USDA Nutrition FastMCP server
#
# unRAID deployment notes:
# - Connect this container to the same Docker network as `postgresql17`
#   so it can resolve the hostname `postgresql17`.
# - Map a host port of your choice to container port 8000.
# - The MCP endpoint will be served at http://<host>:<port>/mcp
# - Override the database URL via the DATABASE_URL environment variable
#   if your setup differs from the default
#   (postgresql://postgres@postgresql17:5432/usda).

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=postgresql://postgres@postgresql17:5432/usda

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

EXPOSE 8000

CMD ["python", "server.py"]
