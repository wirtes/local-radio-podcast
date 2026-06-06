# Local Radio Podcast

A small Python app that turns a root folder of MP3 directories into private podcast RSS feeds for your local network.

## Setup

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp config.example.toml config.toml
```

Edit `config.toml`:

- Set `server.base_url` to this computer's LAN URL, such as `http://192.168.1.25:8000`.
- Set `feed.root_directory` to the folder that contains your podcast folders.
- Adjust the library title, description, and author.

Every immediate directory inside `feed.root_directory` becomes a separate podcast. For example:

```text
/Users/you/Music/Local Podcasts/
  Morning Show/
    episode-1.mp3
  Interviews/
    guest-a.mp3
```

Find your LAN IP on macOS with:

```sh
ipconfig getifaddr en0
```

## Run

```sh
.venv/bin/flask --app app run --host 0.0.0.0 --port 8000
```

Then open:

```text
http://YOUR_LAN_IP:8000/
```

## Subscribe in Apple Podcasts

On the same local network, open Apple Podcasts and choose:

```text
File -> Follow a Show by URL...
```

Enter:

```text
http://YOUR_LAN_IP:8000/podcasts/PODCAST_ID/feed.xml
```

The homepage lists the feed URL for each podcast folder.

## Behavior

- Every immediate directory inside `feed.root_directory` is exposed as a separate podcast.
- Each podcast recursively scans its own directory for `.mp3` files.
- RSS items are sorted newest-first by ID3 date metadata when available, otherwise by file modification time.
- Episode title, artist, album, date, comment/description, duration, and file size are read from MP3 metadata.
- MP3 files are served only when they were found inside the requested podcast directory.
