"""init schema

Revision ID: 20260308_0001
Revises: 
Create Date: 2026-03-08 00:00:00.000000
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260308_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable PostGIS
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis;")

    # devices
    op.execute("""
    CREATE TABLE IF NOT EXISTS devices (
        id BIGSERIAL PRIMARY KEY,
        serial TEXT NOT NULL UNIQUE,
        model TEXT,
        owner TEXT,
        installed_at TIMESTAMPTZ,
        meta JSONB
    );
    """)

    # trips
    op.execute("""
    CREATE TABLE IF NOT EXISTS trips (
        id BIGSERIAL PRIMARY KEY,
        device_id BIGINT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
        started_at TIMESTAMPTZ NOT NULL,
        ended_at TIMESTAMPTZ
    );
    CREATE INDEX IF NOT EXISTS trips_device_id_idx ON trips(device_id, started_at);
    """)

    # imu_windows (география и индексы)
    op.execute("""
    CREATE TABLE IF NOT EXISTS imu_windows (
        id BIGSERIAL PRIMARY KEY,
        device_id BIGINT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
        trip_id BIGINT REFERENCES trips(id) ON DELETE SET NULL,
        t_start TIMESTAMPTZ NOT NULL,
        t_end   TIMESTAMPTZ NOT NULL,
        lat DOUBLE PRECISION NOT NULL,
        lon DOUBLE PRECISION NOT NULL,
        geom GEOGRAPHY(Point, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat),4326)) STORED,
        speed_mps REAL,
        hdop REAL,
        sats SMALLINT,
        a_rms REAL,
        a_p95 REAL,
        a_max REAL,
        jerk_p95 REAL,
        peaks SMALLINT,
        e_0_5 REAL,
        e_5_12 REAL,
        e_12_30 REAL,
        roughness_score REAL,
        payload JSONB,
        created_at TIMESTAMPTZ DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS imu_windows_device_time_idx ON imu_windows(device_id, t_start);
    CREATE INDEX IF NOT EXISTS imu_windows_tend_idx ON imu_windows(t_end);
    CREATE INDEX IF NOT EXISTS imu_windows_geom_gist ON imu_windows USING GIST(geom);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS imu_windows;")
    op.execute("DROP TABLE IF EXISTS trips;")
    op.execute("DROP TABLE IF EXISTS devices;")
    # расширение postgis можно оставить