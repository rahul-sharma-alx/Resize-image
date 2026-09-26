# Bulk Image Compressor (max 100 KB per image)

A small, self contained project that takes **images** and rewrites them so that no
single file is bigger than **100 KB** (or any other limit you type in) – and, as a
bonus, converts them to another format.

It ships with three front ends on top of one engine:

| Front end | Start it with | Notes |
| --- | --- | --- |
| **Web app** (drag & drop) | `python server.py` or deploy to Vercel | compresses inside the browser, nothing is uploaded |
| **Desktop window** | `python app.py` (or `run_app.bat`) | compresses a whole folder |
| **Command line** | `python image_resizer.py <folder>` | scripts and batch jobs |

---

## 1. Install

Only one dependency is needed: **Pillow**.

```bat
python -m pip install -r requirements.txt
```

The **web app** does not need it at all – it compresses with the browser's own image
encoder, so you can also just open `web\index.html` from disk.

## 2. The web app (drag & drop, single or many images)

```bat
python server.py                 # http://localhost:8765, opens a browser
python server.py --port 5500     # another port
python server.py --no-browser    # do not open a browser window
```

or double click **`run_web.bat`**.

### What it does

1. **Drop images, a batch, or a whole folder** on the drop area (the *browse your files*
   button and <kbd>Ctrl</kbd>+<kbd>V</kbd> paste work too). Every image gets a row with
  its size, dimensions (once its preview loads) and a status chip; select multiple rows
  to remove them together. A dropped folder is
   walked recursively, so **sub folders are included and the structure is kept in the
   zip** – `photos/summer/beach.png` comes back as `photos/summer/beach_min.webp`.
   Prefer clicking? Use *Got a folder with sub folders? → Choose the folder* below the
   drop area.
2. **Choose the output settings**

    Use a quick preset for common email, website or social outputs, then fine-tune any
    setting if needed. Open **Crop & rotate** to adjust an individual image: create a
    freeform or ratio-locked crop, drag it to move, resize any edge or corner, adjust a
    round crop's radius with arrow keys, and rotate by 90 degrees. **Crop & save** downloads
    the edited image as a PNG without replacing the queued original. Edits also apply to
    compressed downloads.

   | setting | meaning |
   | --- | --- |
   | Compression mode | *Fit under a target size* (searches for it) or *Use a fixed quality* (one fast pass) |
   | Max size per image | the byte budget in KB, with 50/100/200/500 KB presets |
   | Quality | used by the *fixed quality* mode (and mirrored by the slider) |
   | Output format | **Keep original**, **JPEG (.jpg)**, **WebP (.webp)** or **PNG (.png)** |
  | Max width / height | optional downscale limit in pixels |
  | Resize to (%) | optional additional scale-down from the dimensions above; never enlarges |
   | Lowest quality before downscaling | below this the picture is scaled instead |
  | Filename suffix or template | use `_min` or `{name}_{index}` (`holiday.jpg` → `holiday_001.webp`) |

3. **Press *Compress*** – the progress bar, the per file status and *Cancel* follow the
  run. Results show `size before → size after`, the new pixel size, the chosen quality,
  how much was saved, and a toggle to compare original and optimized previews. Download
  single files or **Download all (.zip)** – with a
   folder upload the archive reproduces your folders, and the zip is built in the browser
   (no upload, no size limit).

### Notes

* **You always get told what happened.** Every step raises a toast: files added (with the
  count and the total size), a folder read (with the folder name and how many non‑image
  files were ignored), the finished run (with the bytes and the percentage saved), and any
  failure. Toasts can be dismissed, pause while you hover them, and the drop zone itself
  shows a spinner plus *“12 images ready”*. If the queue would be off screen after adding,
  the page scrolls to it and the new rows flash.
* **The layout is responsive** from 320 px phones to wide desktops: single column settings,
  stacked file/result rows, 44 px tap targets, 16 px inputs (no iOS zoom‑in on focus), safe
  area padding for notched phones, landscape‑phone tweaks, plus `prefers-reduced-motion` and
  `prefers-contrast` support.
* Nothing is uploaded: the images are decoded, re‑encoded and downloaded in the same tab.
* `Keep original` keeps JPEG/PNG/WebP. BMP, TIFF, GIF, AVIF and SVG are written as JPEG,
  exactly like the Python engine does. Transparent images are flattened onto white for
  JPEG, and alpha is kept for WebP/PNG.
* Animated GIF/WebP are reduced to their first frame, EXIF orientation is applied and the
  metadata is dropped by the canvas re-encode.
* `PNGs` have no quality knob, so a small budget is reached by scaling the picture down.
* Settings are remembered in `localStorage`.
* Folder support uses the browser's FileSystemEntry API (Chrome, Edge, Firefox, Opera
  and Safari 14.1+). If a browser only offers a flat file list, dropping a folder falls
  back to the files that were handed over – the *Choose the folder* button still works
  there because it uses `webkitdirectory`.
* Empty folders are not recreated in the zip, and a folder upload is capped at 2 000
  files / 12 levels deep so a stray `C:\` drop cannot hang the tab.

## 3. Share it from your laptop with a public URL

`share.py` runs the app on your machine and opens a public tunnel, so anybody can
reach it – no router setup, no port forwarding, no account needed.

```bat
rem install a tunnel tool once (cloudflared is the nicer one: no warning page)
winget install --id Cloudflare.cloudflared

rem start sharing (prints the public URL, opens the browser)
python share.py
```

or double click **`run_public.bat`**.

```
==============================================================
  ResizeImage - sharing from this laptop
  local   : http://localhost:8765/
  tunnel  : cloudflared
==============================================================
  PUBLIC URL  ->  https://something-funny.trycloudflare.com
  checking the public link ... ok, the app answers on the public link
  share that link with anyone; press Ctrl+C to stop
```

| command | what it does |
| --- | --- |
| `python share.py` | uses `cloudflared` if installed, otherwise `ngrok` |
| `python share.py --provider cloudflared` | force the free `*.trycloudflare.com` link |
| `python share.py --provider ngrok` | use your ngrok account (stable domain on paid plans) |
| `python share.py --provider none` | local only, no tunnel (for testing) |
| `python share.py --port 5500` | prefer another local port |
| `python share.py --no-browser` | do not open a browser window |

### What to know

* **Port 8765 is the default** (not 8000/3000/8080, which development servers usually
  take). It is defined once in `server.py` as `DEFAULT_PORT`; change it there, or pass
  `--port`. If the port is busy anyway, the next free one is used automatically.

* **The URL changes every time you restart** – a "quick tunnel" gets a random name.
  Send the new link to people each time, or keep the window open and share one link.
* **Your laptop must stay on, awake and online** while others use it. Sleep, closing the
  lid or losing internet stops the page immediately. Plug in the power adapter and
  disable sleep for the duration (`powercfg /change standby-timeout-ac 0`).
* **Your personal IP is not exposed**: the tunnel only forwards this one local port, and
  the app itself only serves `index.html`, `styles.css`, `app.js`, `zip.js` and
  `api/health` – no directory listing, no file access.
* **Visitors' images never touch your laptop.** All compression happens in the visitor's
  browser; your machine only sends ~40 KB of HTML/JS/CSS, so thousands of people can use
  the page at the same time.
* A quick tunnel has no uptime guarantee. For a link that is always online, deploy the
  same folder to Vercel (section 4) or use a named Cloudflare tunnel on your own domain.
* Windows firewall: if the page cannot be reached, allow Python for private networks, or
  pick another port with `--port`.
* **`ERR_NGROK_8012` / "dial tcp [::1]:8765: connection refused"** means the tunnel agent
  could not reach the app. It happens when the agent resolves `localhost` to the IPv6
  loopback while the server only listens on IPv4. `share.py` avoids it by pointing the
  tunnel at `127.0.0.1` explicitly, and it checks `/api/health` before opening a tunnel –
  if the local server is down you get that message instead of an ngrok error page.
* **ngrok shows a warning page** to visitors on its free tier ("You are being asked to
  visit a site…"). That is ngrok, not this app; `cloudflared` (the default) has no such
  page. If a stale ngrok session is already running, the script says so and points you at
  `--provider cloudflared`.

## 4. Deploy the web app to Vercel

The app is a plain static site (three files, no build step, no server, no cost), so
Vercel only has to publish the `web/` folder.

```bat
rem one time
npm i -g vercel

rem from this project folder - first deploy
vercel

rem production deploy
vercel --prod
```

`vercel.json` already points Vercel at the right folder:

```json
{ "framework": null, "buildCommand": null, "outputDirectory": "web" }
```

Deploying from the Vercel dashboard instead? Import the repository, keep the defaults
*Framework Preset: Other* / *Build Command: (empty)* / *Output Directory: `web`*.

Because the compression runs in the browser there is no function, no database and no
request size limit – and no image ever touches the server.

## 5. Run the desktop application

```bat
python app.py
```

or just double click **`run_app.bat`** (it installs Pillow if it is missing and then
starts the GUI).

The window is divided into three steps:

1. **Folders** – pick the folder with your images. *Save into* is filled automatically
   with `<images folder>\compressed`. Tick *Include sub folders* to walk the whole tree.
   Tick *Replace the original files* if you do not want to keep the originals
   (a warning applies: that cannot be undone).
2. **Target** – *Max size per image (KB)* defaults to **100**. Optionally set a maximum
   width/height and an output format (keep original / JPEG / WebP / PNG).
3. **Start compressing** – a progress bar, a coloured log line per file and a summary
   with *size before → size after* appear while the run is going on. *Cancel* stops the
   batch after the file that is currently processed.

## 6. Run it from the command line

```bat
rem compress everything in C:\photos into C:\photos\compressed (100 KB limit)
python image_resizer.py "C:\photos"

rem different limit and format
python image_resizer.py "C:\photos" --max-kb 250 --format webp

rem replace the originals in place
python image_resizer.py "C:\photos" --overwrite

python image_resizer.py --help
```

| option | meaning |
| --- | --- |
| `folder` | folder with the images (a single image file also works) |
| `-o, --output` | where the results go (default `<folder>/compressed`) |
| `-s, --max-kb` | maximum size of one image in KB (default `100`) |
| `-f, --format` | `original`, `JPEG`, `PNG` or `WEBP` |
| `-w, --max-width`, `--max-height` | downscale limit in pixels |
| `--min-quality` | lowest JPEG/WebP quality that may be used (default `25`) |
| `--no-recursive` | do not walk sub folders |
| `--overwrite` | replace the originals instead of writing a copy |
| `--keep-all` | also re-compress files that are already smaller than the limit |
| `--no-metadata` | strip EXIF/ICC data |
| `-q, --quiet` | only print the final report |

## 7. How the 100 KB target is reached

1. The image is normalised: EXIF rotation is applied, 16 bit / CMYK / palette modes are
   converted and animated files are collapsed to their first frame.
2. An optional maximum width/height is applied once.
3. A **binary search over quality** (25 … 95) finds the *highest* quality that still fits
   into the byte budget.
4. If even quality 25 is too large, the image is **downscaled** by a factor derived from
   the overshoot (`sqrt(target / current)`) and the search runs again – it never goes
   below `min_dimension` (160 px) by default.
5. The best result found is written out. If the target cannot be met, the file is still
   saved with the smallest possible result and the log line says
   `best effort - minimum size reached`.

Files that already satisfy every constraint are **left untouched** (and copied into the
output folder so the folder stays complete). A file is never replaced by a bigger one.

### Supported formats

`.jpg .jpeg .jpe .jfif .png .webp .bmp .tif .tiff .gif`

* `JPEG` and `WEBP` are the best choices for photos (lossy quality knob).
* `PNG` keeps transparency but has no quality knob, so a 100 KB result is achieved by
  downscaling – for photos prefer `JPEG` or `WebP`.
* `BMP`, `TIFF` and `GIF` files are automatically written as `JPEG`, because those
  formats can practically never stay below 100 KB.
* GIF/WebP animations are reduced to their first frame.

### Using the engine from your own code

`image_resizer.compress_image_bytes(data, filename, settings)` compresses an image that is
already in memory (no temp files) and returns a dictionary with `data`, `filename`,
`format`, `original_size`, `compressed_size`, `width`, `height`, `quality`, `skipped`,
`note` and `error` – handy for an upload endpoint or a serverless function:

```python
from image_resizer import CompressionSettings, compress_image_bytes

result = compress_image_bytes(
    open("holiday.jpg", "rb").read(),
    "holiday.jpg",
    CompressionSettings(max_size_bytes=100 * 1024, output_format="WEBP"),
)
if result["error"]:
    raise RuntimeError(result["error"])
open(result["filename"], "wb").write(result["data"])   # holiday.webp
```

## 8. Tests

```bat
python -m unittest discover -s tests -v
node tests\js\web.test.js
node tests\js\markup.test.js
```

`tests/test_image_resizer.py` creates its own images, runs the real compressor and
asserts that every result stays below the limit, that originals are untouched, that
transparency is flattened for JPEG, that the output tree mirrors the input tree, that
corrupt files are reported without stopping the batch, and that cancel / overwrite /
CLI behave as documented.

`tests/test_app_gui.py` drives the actual window (fills the folder fields, presses
*Start*, pumps the event loop) and checks the produced file and the log. It is skipped
automatically when Tk is unavailable.

`tests/test_web_server.py` starts `server.py` on a free port and checks that the page and
all three assets are delivered with the right content types, that `api/health` answers and
that unknown paths return 404.

`tests/test_share.py` covers the public sharing helper: the tunnel tool is selected and
invoked with the right port, the public URL patterns are recognised, the shared server
answers on `/api/health`, the self check accepts a running app and a busy port is skipped.

`tests/js/web.test.js` (Node, no packages needed) loads `web/app.js` into a small DOM and
canvas stand-in and exercises the real application logic: one dropped image, a batch with
duplicate names, the quality search, the downscale fallback, format conversion, non image
files being rejected and the zip writer – the produced zip is finally re-opened with
Python's `zipfile` to prove it is a valid archive.

`tests/js/markup.test.js` makes sure every element id that `app.js` wires up exists exactly
once in `index.html`, that the page loads the shipped assets and that the format select
still offers the three writable formats.

## 9. Good to know

* The exit code of the command line tool is `0` when everything succeeded, `1` when at
  least one file failed (for example a corrupt or password protected image) and `2` for
  a wrong usage or a folder that does not exist.
* Scanning never hides your pictures: only the output folder of the *current* run is
  skipped, and only when it really sits **inside** the folder you picked. An output
  folder that is a *parent* of the image folder (for example
  `C:\Downloads\WEBSITE IMAGES` while the pictures live in
  `C:\Downloads\WEBSITE IMAGES\STM\…`) is not a problem – the images are processed.
  If a run finds nothing, the log and the command line say so instead of stopping
  silently.
* If you keep several generated folders inside the source folder (`compressed`,
  `compressed-old`, …) those older results are scanned again – they are simply reported
  as *Left unchanged* because they already fit the limit.
* The folder is scanned in the background (GUI) / before the first file (CLI) and the
  scan result is reported with its own line (`Scanning …`, `Found N image(s)`), so a slow
  network or OneDrive folder no longer looks like a frozen program.
* A failed file never changes the original and never stops the batch; the log lists it
  in red and the summary repeats it under *Failures*.
* JPEG keeps moving the quality knob only down to `--min-quality` (25 by default). Below
  that the resolution is reduced instead, which looks better than a very low quality
  photo.
* The web app deliberately uses the *browser* encoder instead of Pillow: that is what makes
  a free, static Vercel deployment possible and keeps large batches off any server. The
  algorithm is the same (ceiling first, then a quality search, then downscaling), and
  `compress_image_bytes` is available if you ever want Pillow quality server side.

## 10. Project layout

```
ResizeImage/
├─ web/                          the web app (this is what Vercel publishes)
│  ├─ index.html                 page with drop zone, settings, queue and results
│  ├─ styles.css                 dark, responsive styling
│  ├─ app.js                     intake, size search, format conversion, downloads
│  └─ zip.js                     dependency free zip writer for "Download all"
├─ server.py                     local web server for the app
├─ share.py                      local server + public tunnel (the public URL)
├─ app.py                        Tkinter GUI
├─ image_resizer.py              compression engine + command line interface
├─ vercel.json                   static hosting configuration
├─ .vercelignore                 keeps the Python files out of the upload
├─ requirements.txt              Pillow
├─ run_app.bat                   Windows launcher for the GUI
├─ run_web.bat                   Windows launcher for the local web app
├─ run_public.bat                Windows launcher that prints a public URL
├─ README.md
├─ tests/
│  ├─ test_image_resizer.py      engine + CLI test suite
│  ├─ test_app_gui.py            GUI end to end test
│  ├─ test_web_server.py         web server test
│  ├─ test_share.py              public sharing helper test
│  └─ js/
│     ├─ harness.js              DOM/canvas stand-in for the browser app
│     ├─ web.test.js             browser app test suite
│     └─ markup.test.js          page/script consistency checks
└─ tools/
   └─ make_demo_images.py        writes sample images to try the tool on
```

## 11. Try it without your own pictures

```bat
python tools\make_demo_images.py demo_images
python image_resizer.py demo_images -o demo_out --max-kb 100
```

The helper creates a big photo, an incompressible noise PNG, a transparent logo, an
already small JPEG, a nested album and one broken file – everything the compressor has
to cope with. Both folders can be deleted again afterwards.
