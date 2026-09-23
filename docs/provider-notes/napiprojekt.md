# NapiProjekt provider notes

## Search behavior

- Polish subtitle search checks the file fingerprint, then searches the site catalog unless hash-only mode is enabled.
- Hash-only mode is an opt-in workaround for catalog search failures. It can return fewer results and ignores author filters.
- Author filters apply to catalog rows. They do not affect fingerprint matches in hash-only mode.
- Hash downloads use HTTPS. Catalog challenges use ai-cloudscraper and may fall back to the configured FlareSolverr endpoint.

## License notes

The Provider Hub implementation is maintained for this catalog under its repository license.
