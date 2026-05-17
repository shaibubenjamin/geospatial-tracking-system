import psycopg2, os
DB = os.environ.get("DATABASE_URL_SYNC", "postgresql://geouser:geopass@localhost:5433/geospatial_tracker")
conn = psycopg2.connect(DB)
cur  = conn.cursor()

# Check geometry column SRIDs
cur.execute("""
    SELECT f_table_name, f_geometry_column, srid, type
    FROM geometry_columns
    WHERE f_table_name IN ('mda_households','grids','settlements','wards','lgas')
    ORDER BY f_table_name
""")
print("Geometry column SRIDs:")
for r in cur.fetchall():
    print(f"  {r[0]}.{r[1]}  SRID={r[2]}  type={r[3]}")

# Check actual SRID of a sample household point
cur.execute("SELECT ST_SRID(geom) FROM mda_households WHERE geom IS NOT NULL LIMIT 1")
hh_srid = cur.fetchone()
print(f"\nmda_households sample ST_SRID: {hh_srid[0] if hh_srid else 'N/A'}")

# Check actual SRID of a sample grid
cur.execute("SELECT ST_SRID(geom) FROM grids WHERE geom IS NOT NULL LIMIT 1")
gr_srid = cur.fetchone()
print(f"grids sample ST_SRID: {gr_srid[0] if gr_srid else 'N/A'}")

# Test ST_Within count
cur.execute("""
    SELECT COUNT(*) FROM mda_households h
    JOIN grids g ON ST_Within(h.geom, g.geom)
    WHERE h.geom IS NOT NULL AND g.project_id = 1
    LIMIT 1
""")
print(f"\nHouseholds ST_Within any grid: {cur.fetchone()[0]:,}")

# Sample coords check
cur.execute("SELECT latitude, longitude, ST_AsText(geom) FROM mda_households WHERE geom IS NOT NULL LIMIT 3")
print("\nSample household geoms:")
for r in cur.fetchall():
    print(f"  lat={r[0]}, lon={r[1]}, geom={r[2][:60]}")

cur.execute("SELECT ST_AsText(ST_Centroid(geom)) FROM grids WHERE project_id=1 LIMIT 3")
print("\nSample grid centroids:")
for r in cur.fetchall():
    print(f"  {r[0]}")

cur.close(); conn.close()
