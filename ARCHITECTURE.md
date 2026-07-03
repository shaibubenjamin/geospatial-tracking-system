# ERITAS Architecture

Editable diagram: [`eritas-network-architecture.excalidraw`](eritas-network-architecture.excalidraw).
Open it at [excalidraw.com](https://excalidraw.com) (drag the file onto the canvas, or Menu -> Open).

ERITAS pulls field data from multiple sources, processes it on a central server,
and serves it to a web dashboard and an Android app.

```
Data Sources  ->  (batch sync)  ->  Central ERITAS Server  ->  (Live API)  ->  Visualization
```

## Data Sources

- **CommCare HQ** (primary) via its OData feed.
- **Other connected sources**: Kobo, ODK, DHIS2, SurveyCTO, REST, Google Sheets, Google Drive, Databricks.
- **File uploads**: CSV, Excel / MLOS, Shapefile.

Ingestion is **batch and incremental** (scheduled and on demand), watermarked, and safe
to re-run mid campaign. It is not live streaming.

## Central ERITAS Server

Compute tier (EC2, Docker):

- **Connectors** - one built-in ingestion layer that pulls from every source.
- **Sync Worker** - the scheduler plus queue consumer that runs the incremental pulls.
- **Redis** - cache plus job queue.
- **API** - FastAPI on Uvicorn.

Data tier (managed):

- **RDS** - PostgreSQL with the PostGIS extension, encrypted, with point-in-time backups.

Data path: Connectors -> Sync Worker -> **writes** to RDS. The API **reads** from RDS and
**caches** responses in Redis. Redis and the workers run in the compute tier; RDS is the
managed data store.

Deploy: EC2, Docker, GitHub Actions (OIDC), ECR, ALB with TLS.

## Visualization

- **Web Dashboard** - MapLibre GL JS and Chart.js (coverage, trends, data quality, KPIs).
- **Mobile App** - Android and Kotlin, offline capable, syncs when back online.

The dashboard and the app share one live view over the **Live API** (HTTPS, JWT, cached reads).

## Note on the diagram icons

The icons embedded in the Excalidraw file are simple placeholders, not official brand
logos. To use real logos, open the file in Excalidraw and drag official logo images onto
each node, then remove the placeholder.
