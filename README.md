# M3uApp

A Python application for managing M3U playlists, now with a native desktop interface and embedded movie/TV player. The desktop app does not start a web server or use a browser.

## macOS desktop app

The [macOS build workflow](https://github.com/dcionut/M3uApp/actions/workflows/macos-app.yml) builds a self-contained `M3uApp.app` for Apple Silicon (`arm64`) and Intel (`x86_64`). After a successful run, download the matching artifact from the run's **Artifacts** section (GitHub sign-in required). Extract the artifact, then extract the enclosed ZIP and move `M3uApp.app` to Applications. Python is bundled; you do not need to install it.

These builds are not Apple Developer signed or notarized. macOS may show a security warning; only open a build you trust using the supported **System Settings → Privacy & Security → Open Anyway** flow. The app requires macOS 13 or newer. GitHub build artifacts expire after the repository's configured retention period.

To run the desktop source instead (Python 3.10+):

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-desktop.txt
python3 desktop.py
```

Select a row and use **Play**, **Edit**, **Download**, or **Delete entry**. Double-click a row to play it. Native controls include pause, stop, volume, seeking when supported, and fullscreen (Escape exits). Import/export and clearing the library work directly in native file dialogs. Downloaded files are kept when clearing entries.

The desktop library and downloads are stored in the system application-data directory (on macOS, `~/Library/Application Support/M3uApp/M3uApp`). **Open data folder** shows the actual location. When running from source, an existing `data` library next to `desktop.py` is copied on first launch if no desktop library exists. Original files remain unchanged. For a packaged app, first migrate by running the desktop source from your old app folder, or copy `data/library.sqlite3` and `data/downloads` into the desktop data folder while the app is closed. Back up existing data before replacing it.

Playback uses Qt Multimedia and its available codecs, with support depending on your platform and provider. Browser CORS restrictions do not apply, but DRM, custom provider playback headers, expired links, DNS errors, and unsupported codecs may still prevent playback. Direct movie downloading uses the existing downloader; HLS/DASH recording remains unsupported. Active downloads are interrupted if you confirm quitting.

To build locally on macOS:

```sh
python3 -m pip install -r requirements-desktop.txt pyinstaller==6.19.0
python3 -m PyInstaller --noconfirm --clean --windowed --name M3uApp --osx-bundle-identifier com.m3uapp.desktop desktop.py
```

The bundle is written to `dist/M3uApp.app`. Build separately on each target architecture. The workflow checks the packaged app starts; playback with real provider media still needs verification on your Mac.

## Original browser version

The previous browser version remains available and uses only the Python standard library:

```sh
python app.py
```

Open `http://127.0.0.1:8765` in a browser on the same machine. Use `python app.py --port 9000` for another port. Keep the app bound to localhost; it is intended for a single trusted local user, with no authentication.

- Import `.m3u` or `.m3u8` playlists (UTF-8). Imports append entries; importing twice creates duplicates.
- Use **Clear library** to remove all playlist entries before importing a different list. Confirmation is required; downloaded files and running downloads are preserved.
- Search titles, filter groups, and use **Edit** to assign any group name.
- Edit titles, URLs, and metadata as a JSON object with string values. Common keys include `tvg-id`, `tvg-name`, `tvg-logo`, `year`, `genre`, and `description`.
- Export the library as an extended M3U playlist. Attribute metadata is preserved; arbitrary playlist directives and custom durations are not retained.
- Select **Download** for direct HTTP/HTTPS movie files. Files are saved under `data/downloads/` on the server machine, not the browser's machine. Progress and failures appear in the sidebar. Three downloads can run concurrently. HLS/DASH manifest recording, DRM, custom authentication headers, and resuming are not supported. Active downloads stop when the app exits; partial files from interrupted processes may need manual removal. Only download media you have permission to save.

The SQLite library lives in `data/library.sqlite3`; back up the `data` folder to preserve your library and downloads. Download job history is kept in memory for the current session. No external API keys or services are required.

Run validation:

```sh
python -m unittest discover -s tests -v
```

Provider links using `|User-Agent=...&Referer=...` and `#EXTVLCOPT:http-user-agent` / `http-referrer` directives are supported for downloads. HTTP errors appear with guidance in the Downloads panel. Expired links must be refreshed from your provider. TLS certificate verification remains enabled.

## Play movies and TV

Click **Play** on an entry to open the in-app player. Standard video controls provide pause, volume, seeking (when supported by the source), and fullscreen. **Stop / Close** releases the stream. Switching entries stops the previous stream.

Direct movie playback uses your browser's supported codecs (MP4/H.264/AAC is widely supported). Live HLS `.m3u8` streams use native playback where available, or bundled HLS.js 1.6.15 (Apache-2.0; see `vendor/hls.LICENSE`). No CDN connection is needed to load the player. For extensionless HLS URLs, add `"stream-type": "hls"` to the entry metadata.

Media is loaded directly from the provider in your browser. Providers must allow CORS for HLS.js. Custom User-Agent/Referer headers used for downloads cannot be applied by the browser player. MPEG-TS direct streams, DASH, DRM, and codecs unsupported by the browser are not converted; such entries may still require VLC. Playback does not fix unreachable hostnames or expired provider links.
