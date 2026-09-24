# LF Docs — web version of LF Order Finder

`https://tools.northernfruit.com/lfdocs/`

Type an order number, get the Laserfiche order folder and its documents, open either in the **Laserfiche web client** or, via a downloaded `.lfe` shortcut, in the **Laserfiche Windows client**. Installable as a Chrome/Edge app so it runs in its own small window. Each user signs in with their own Laserfiche account; the server never stores passwords.

## How it works

- **Lookup** — exact `\Sales\Orders\<number>` first; if that folder doesn't exist, folder names under `\Sales\Orders` are matched as `<number>*` (so `80670` → `80670-0`). One match opens; several are listed. Wildcards can be typed (`8067*`, `80670-?`).
- **Opening** — an **Open with** toggle (remembered per browser) picks the client:
  - *Web client*: folders open `browse.aspx?db=<repo>#?id=…`, documents `docview.aspx?db=<repo>&id=…`, in a new tab of the normal browser.
  - *Windows client*: the app serves a `.lfe` shortcut (`GET api/lfe/<id>`) — folders as `<entry id='…' makeroot='n'/>`, documents as `mode='1'` (imaged, document viewer) or `mode='2'` (electronic, native application). The browser downloads it and the Laserfiche Windows client opens it. Each `.lfe` starts a new client window, as the client itself works.
  - Clicking a folder *row* (not its name) browses into it inside the app.
- **Sign-in** — the app calls the API Server's `/Token` endpoint with the user's Laserfiche/LFDS/Windows credentials and keeps the returned bearer token in an encrypted, HttpOnly cookie for the token's lifetime. On expiry the app simply asks the user to sign in again. If the site is behind `oauth2-proxy`, the M365 email's local part is pre-filled as the user name.
- **Document types** — managed on the ⚙ **Settings** page inside the app (Name, Root folder with a repository browser, Match, Subfolders, Field name). There is a **shared default** list that everyone starts with, editable by the admins named in `ADMIN_USERS`, and every user can **customize their own** list on top (add, remove, reorder) with a *Reset to defaults* button. Lists are stored under `./data/` (a Docker volume), keyed by Laserfiche user name, so they follow the user to any PC. Each type becomes a tab; one type = no tab bar.

## Deploy (Docker, same pattern as the other tools)

```bash
cd /var/www/html/lfdocs            # or wherever the sibling apps live
cp .env.example .env
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # paste as SECRET_KEY in .env
docker compose up -d --build       # listens on 127.0.0.1:8097
curl -s http://127.0.0.1:8097/api/health
```

Then add `nginx-lfdocs.conf` inside the `tools.northernfruit.com` server block and `nginx -t && systemctl reload nginx`. The `location /lfdocs/` block strips the prefix and passes `X-Forwarded-Prefix`; the container is told its prefix with `BASE_PATH=/lfdocs` so the session cookie and the PWA scope are limited to that path. Keep the `oauth2-proxy` `auth_request` lines if you want the page M365-gated like the others; drop them if not — the app's own Laserfiche sign-in is what actually controls access to documents either way.

Port **8097** is the next free one after `lfcapture` (8096); change it in `docker-compose.yml`, the nginx snippet and `PORT` if you'd rather use another.

## Settings (`.env`)

| Variable | Default | Notes |
|---|---|---|
| `SECRET_KEY` | — | Required. Encrypts the session cookie. Changing it signs everyone out. |
| `BASE_PATH` | `/lfdocs` | Must match the nginx location |
| `APP_TITLE` | `LF Docs` | Shown in the header and window title |
| `LF_API_URL` | `https://lf.northernfruit.com/LFRepositoryAPI` | API Server |
| `LF_API_VERSION` | `v1` | `v1` or `v2` |
| `LF_REPOSITORY` | `NorthernFruit` | |
| `LF_WEB_URL` | `https://lf.northernfruit.com/laserfiche` | Web client |
| `LF_VERIFY_TLS` | `true` | Set `false` only for a certificate the container can't validate |
| `ADMIN_USERS` | — | Laserfiche user names allowed to edit the shared defaults (comma-separated; `DOMAIN\` optional). Everyone can edit their own list. |
| `LOOKUPS` | `orders\|Order\|\Sales\Orders\|prefix\|0` | Initial shared defaults, only used until an admin saves the shared list in the app. `id\|Label\|\Root\|match\|subfolders`, `;`-separated |
| `DATA_DIR` | `/srv/data` | Where `lookups.json` (shared) and `users/<name>.json` (per user) live — mapped to `./data` by compose |

Once running, manage types on the ⚙ Settings page rather than in `.env`: admins see a **My types / Shared defaults** switch; everyone else edits only their own list. Match styles: `prefix` (folder named the text or `text*`), `contains` (`*text*`), `field` (template field equals the text — lists matching documents). **Sub** searches anywhere under the root (vendor\year\invoice; nested hits show their sub-path). The **…** button browses the repository for the root folder. The `LOOKUPS` format is the same one the desktop utility stores.

## Installing it as an app

Open `https://tools.northernfruit.com/lfdocs/` in Chrome or Edge, then:

- **Chrome**: address-bar install icon, or ⋮ → *Cast, save, and share* → *Install page as app…*
- **Edge**: ⋯ → *Apps* → *Install this site as an app*

It opens in its own window without tabs or address bar, pins to the taskbar, and remembers whatever size you leave it at — a narrow window with just the search box and list works fine. Keyboard: `Enter` finds, `/` jumps to the search box, `Esc` clears it.

To push it to everyone without clicks, use the browser policy (GPO or Intune) `WebAppInstallForceList`, e.g. for Edge:

```json
[{ "url": "https://tools.northernfruit.com/lfdocs/", "default_launch_container": "window", "create_desktop_shortcut": true }]
```

## Making `.lfe` one click (Windows client option)

The first time, Chrome/Edge show the downloaded `.lfe` in the download bubble; the user clicks it to open. To skip that click from then on: right-click the download → **Always open files of this type** (Chrome) / **Always open** (Edge). The file type is registered by the Laserfiche Windows client, so nothing else is needed on the PC.

To set it for everyone by policy (GPO/Intune), so nobody has to do the above:

- Chrome: `AutoOpenFileTypes` = `["lfe"]` and `AutoOpenAllowedForURLs` = `["https://tools.northernfruit.com"]`
- Edge: `AutoOpenFileTypes` = `["lfe"]` and `AutoOpenAllowedForURLs` = `["https://tools.northernfruit.com"]`

`.lfe` files are ignored by the app's service worker cache and require the signed-in session, like every other API call.

## Files

```
app/main.py         FastAPI app: session cookie, /api/config, /api/login, /api/logout, /api/lookup/{kind}, /api/folder/{id}, /api/lfe/{id}, /api/lookups (GET/PUT/DELETE), /api/subfolders/{id}
data/               shared defaults (lookups.json) and per-user lists (users/<name>.json) — Docker volume, git-ignored
app/lf.py           Repository API client (token, ByPath, folder children, SimpleSearches)
static/             index.html, app.js, style.css, manifest.webmanifest, sw.js, icons/
Dockerfile, docker-compose.yml, requirements.txt, .env.example, nginx-lfdocs.conf
```

## Notes

- Simple search returns at most 100 folders per pattern; if a pattern is that broad the list is truncated — type more digits.
- The service worker only caches the app shell (HTML/JS/CSS/icons); every lookup goes to the server. After a deploy the next open picks up the new files.
- Session length is whatever the API Server's token lifetime is; there's no refresh, by design — when it expires, sign in again.
