from app.db import get_conn
from app.endpoints import compute_score_py

BATCH_SIZE = 500

def main():
    updated = 0

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, a_rms, a_p95, a_max, jerk_p95, peaks, speed_mps
            FROM imu_windows
            ORDER BY id
        """)
        rows = cur.fetchall()

        updates = []
        for row in rows:
            row_id, a_rms, a_p95, a_max, jerk_p95, peaks, speed_mps = row

            w = {
                "a_rms": a_rms,
                "a_p95": a_p95,
                "a_max": a_max,
                "jerk_p95": jerk_p95,
                "peaks": peaks,
                "speed_mps": speed_mps,
            }
            score = compute_score_py(w)
            updates.append((score, row_id))

        for i in range(0, len(updates), BATCH_SIZE):
            chunk = updates[i:i + BATCH_SIZE]
            cur.executemany(
                "UPDATE imu_windows SET roughness_score = %s WHERE id = %s",
                chunk
            )
            updated += len(chunk)
            print(f"Updated {updated}/{len(updates)} rows")

    print(f"Done. Recalculated {updated} rows.")


if __name__ == "__main__":
    main()