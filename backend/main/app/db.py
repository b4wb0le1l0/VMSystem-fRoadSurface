import psycopg
from psycopg_pool import ConnectionPool
from .settings import settings

pool = ConnectionPool(
    conninfo=settings.database_url,
    min_size=1,
    max_size=10,
    timeout=10
)

def get_conn():
    return pool.connection()  # context manager