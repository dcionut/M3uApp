# M3uApp

A local Python application for managing M3U playlists. Python 3.10+ is required; there are no third-party dependencies.

```sh
python app.py
```

Open `http://127.0.0.1:8765` in a browser on the same machine. Use `python app.py --port 9000` for another port. Keep the app bound to localhost; it is intended for a single trusted local user, with no authentication.

- Import `.m3u` or `.m3u8` playlists (UTF-8). Imports append entries; importing twice creates duplicates.
- Search titles, filter groups, and use **Edit** to assign any group name.
- Edit titles, URLs, and metadata as a JSON object with string values. Common keys include `tvg-id`, `tvg-name`, `tvg-logo`, `year`, `genre`, and `description`.
- Export the library as an extended M3U playlist. Attribute metadata is preserved; arbitrary playlist directives and custom durations are not retained.
- Select **Download** for direct HTTP/HTTPS movie files. Files are saved under `data/downloads/` on the server machine, not the browser's machine. Progress and failures appear in the sidebar. Three downloads can run concurrently. HLS/DASH manifest recording, DRM, authentication headers, and resuming are not supported. Active downloads stop when the app exits; partial files from interrupted processes may need manual removal. Only download media you have permission to save.

The SQLite library lives in `data/library.sqlite3`; back up the `data` folder to preserve your library and downloads. Download job history is kept in memory for the current session. No external API keys or services are required.

Run validation:

```sh
python -m unittest discover -s tests -v
```
