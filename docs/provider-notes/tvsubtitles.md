# TVsubtitles

The homepage search form posts `qs` to `/search1.php`. On 8 October 2026, submitting a show from that form returned HTTP 404. The homepage links to `/tvshows.html`, which still serves the full show index. The provider reads a show ID and start year from that index, then follows its linked season and episode pages.

The index response is large, so the provider caches it for one hour per worker instance. The worker reuses that instance for later searches and keeps one generated desktop Firefox/Linux identity and cookie session across its requests.

Downloads still use `/download-{subtitle_id}.html`. The page splits its archive path across sequential `s1`, `s2`, and later variables; the provider joins those segments and fetches the ZIP from the site.
