# LF Docs — web version of LF Order Finder

`https://tools.northernfruit.com/lfdocs/`

Type an order number, get the Laserfiche order folder and its documents, open either in the **Laserfiche web client**. Installable as a Chrome/Edge app so it runs in its own small window. Each user signs in with their own Laserfiche account; the server never stores passwords.

## How it works

- **Lookup** — exact `\Sales\Orders\<number>` first; if that folder doesn't exist, folder names under `\Sales\Orders` are matched as `<number>*` (so `80670` → `80670-0`). One match opens; several are listed. Wildcards can be typed (`8067*`, `80670-?`).
- **Opening** — folders open `browse.aspx?db=<repo>#?id=…`, documents open `docview.aspx?db=<repo>&id=…`, both in a new tab of the normal browser. Clicking a folder row (not its name) browses into it inside the app.
- **Sign-in** — the app calls the API Server's `/Token` endpoint with the user's Laserfiche/LFDS/Windows credentials and keeps the returned bearer token in an encrypted, HttpOnly cookie for the token's lifetime. On expiry the app simply asks the user to sign in again. If the site is behind `oauth2-proxy`, the M365 email's local part is pre-filled as the user name.
- **Expandable** — every entry in `LOOKUPS` becomes a tab. One entry = no tab bar, just the search box.

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
| `LOOKUPS` | `orders\|Order\|\Sales\Orders` | `id\|Label\|\Root\Path`, `;`-separated |

To add invoices later, for example: `LOOKUPS=orders|Order|\Sales\Orders;invoices|Invoice|\AP\Invoices` and restart the container — an "Invoices" tab appears.

## Installing it as an app

Open `https://tools.northernfruit.com/lfdocs/` in Chrome or Edge, then:

- **Chrome**: address-bar install icon, or ⋮ → *Cast, save, and share* → *Install page as app…*
- **Edge**: ⋯ → *Apps* → *Install this site as an app*

It opens in its own window without tabs or address bar, pins to the taskbar, and remembers whatever size you leave it at — a narrow window with just the search box and list works fine. Keyboard: `Enter` finds, `/` jumps to the search box, `Esc` clears it.

To push it to everyone without clicks, use the browser policy (GPO or Intune) `WebAppInstallForceList`, e.g. for Edge:

```json
[{ "url": "https://tools.northernfruit.com/lfdocs/", "default_launch_container": "window", "create_desktop_shortcut": true }]
```

## Files

```
app/main.py         FastAPI app: session cookie, /api/config, /api/login, /api/logout, /api/lookup/{kind}, /api/folder/{id}
app/lf.py           Repository API client (token, ByPath, folder children, SimpleSearches)
static/             index.html, app.js, style.css, manifest.webmanifest, sw.js, icons/
Dockerfile, docker-compose.yml, requirements.txt, .env.example, nginx-lfdocs.conf
```

## Notes

- Simple search returns at most 100 folders per pattern; if a pattern is that broad the list is truncated — type more digits.
- The service worker only caches the app shell (HTML/JS/CSS/icons); every lookup goes to the server. After a deploy the next open picks up the new files.
- Session length is whatever the API Server's token lifetime is; there's no refresh, by design — when it expires, sign in again.
