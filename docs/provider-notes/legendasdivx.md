# LegendasDivx provider notes

Clean-room target for `legendasdivx`.

## Public behavior

- Site: `https://www.legendasdivx.pt/`
- Login endpoint: `/forum/ucp.php?mode=login`
- Search endpoint shape:
  `/modules.php?name=Downloads&file=jz&d_op=<search|jz_00>&op=<op>&query=<query>&temporada=<season>&episodio=<episode>&imdb=<series_imdb>`
- Supported media: movies and episodes.
- Supported languages:
  - `por` for Portugal Portuguese.
  - `por-BR` for Brazilian Portuguese.
- Requires username and password.
- Supports the legacy `skip_wrong_fps` setting.
- Search responses expose subtitle boxes with hits, language flag, frame rate, release description, uploader, and a download link with `lid`.
- Downloads are direct subtitle files or ZIP/RAR archives. Episode archives should select the subtitle matching the requested season and episode.

## Compatibility quirks

- The site tracks a daily search counter in `<!--pesquisas: N-->`; the provider raises when it reaches the safe cap.
- Episode searches use the series IMDb id path when `series_imdb_id` is available.
- If no series IMDb id is available, the provider searches the series plus `SxxEyy`, then the season pack shape.
- Live unauthenticated search redirects to the account page, so full live smoke requires valid credentials.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
