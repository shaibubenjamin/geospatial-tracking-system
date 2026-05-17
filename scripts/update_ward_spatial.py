"""
Spatially assign ward_name to mda_households by intersecting each household GPS
point with the loaded ward polygons (wards table, project_id=1).
"""
import psycopg2, os, time

DB = os.environ.get("DATABASE_URL_SYNC",
                    "postgresql://geouser:geopass@localhost:5433/geospatial_tracker")

def main():
    conn = psycopg2.connect(DB)
    cur  = conn.cursor()

    # 1. Add column if absent
    cur.execute("ALTER TABLE mda_households ADD COLUMN IF NOT EXISTS ward_name text")
    conn.commit()
    print("✓ Column ward_name ensured")

    # 2. Count rows we will process
    cur.execute("SELECT COUNT(*) FROM mda_households WHERE geom IS NOT NULL")
    total = cur.fetchone()[0]
    print(f"  Households with geometry: {total:,}")

    # 3. Spatial join: ST_Within → ward_name
    t0 = time.time()
    cur.execute("""
        UPDATE mda_households h
        SET ward_name = w.ward_name
        FROM wards w
        WHERE h.geom IS NOT NULL
          AND w.project_id = 1
          AND ST_Within(h.geom, w.geom)
    """)
    updated = cur.rowcount
    conn.commit()
    print(f"  Updated {updated:,} rows with ward_name in {time.time()-t0:.1f}s")

    # 4. Stats
    cur.execute("SELECT COUNT(*) FROM mda_households WHERE ward_name IS NOT NULL")
    with_ward = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM mda_households WHERE ward_name IS NULL AND geom IS NOT NULL")
    no_ward   = cur.fetchone()[0]
    print(f"  Records with ward   : {with_ward:,}")
    print(f"  Records without ward: {no_ward:,}  (GPS outside all ward polygons)")

    # 5. Top wards
    cur.execute("""
        SELECT ward_name, COUNT(*) AS n
        FROM mda_households
        WHERE ward_name IS NOT NULL
        GROUP BY ward_name
        ORDER BY n DESC
        LIMIT 5
    """)
    print("\n  Top 5 wards by household count:")
    for row in cur.fetchall():
        print(f"    {row[0]:30s}: {row[1]:,}")

    cur.close(); conn.close()
    print("\n✓ Ward update complete.")

if __name__ == "__main__":
    main()
