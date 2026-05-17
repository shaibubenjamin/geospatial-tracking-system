import psycopg2
conn = psycopg2.connect("postgresql://geouser:geopass@localhost:5433/geospatial_tracker")
cur = conn.cursor()
cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='mda_households' ORDER BY ordinal_position")
for r in cur.fetchall():
    print(r)
conn.close()
