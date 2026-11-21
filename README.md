# Music Dashboard

My listening history from ListenBrainz, with top artists, albums, tracks and genres, a listening heatmap, and charts for how my listening changes over time.

## Run locally

You'll need Docker Compose. For a preview without a ListenBrainz account, follow the [sample-data setup](docs/development.md#local-compose-synthetic-mode). To use your own history, follow the [ListenBrainz setup](docs/development.md#real-data-first-run).

Open http://127.0.0.1:8080 once the stack is running.

- [Development](docs/development.md)
- [How the data is counted](docs/architecture.md#statistics-date-and-display-semantics-t-005t-006-constraints)
- [Deployment and backups](docs/deployment.md)

The dashboard has no login. If you publish it, anyone can see the stats. Deletes on ListenBrainz aren't automatically reflected here.
