# M1 home server: cleanup and setup

The 8 GB MacBook Air M1 stays at home, plugged in, always on. It will hold the notes,
the database and the website; both models (answers and search) stay on the M4 laptop.
Step 1 makes room on it. Step 2 is the setup checklist.

## Step 1: Find out what is taking space

A scan that only **measures**. It deletes nothing.

```
scripts/disk-scan.sh
```

It takes a minute or two and writes `disk-scan.txt` to the M1's Desktop. Send that file
back (paste its contents into the chat), and every item gets sorted into one of three:

| | What | Examples |
|---|---|---|
| **Safe to delete** | Rebuilt or re-downloaded automatically when needed | The Trash, old installers in Downloads, Homebrew / npm / pip download caches, Xcode build leftovers, old iPhone backups no longer needed |
| **Ask first** | Could be personal | Downloads, Documents, Messages attachments, large videos |
| **Leave alone** | Wanted, or needed for an app to work | **Spotify cache** (keep), and anything else listed below |

**Keep, never delete:**

- Spotify cache
- WhatsApp media
- `~/Voice Generation`

### Space the M1 needs for its new job

About **15–20 GB free**: the notes, the database, and room to breathe. Both models stay
on the M4, so they need no space here.

### A note on Terminal permissions

macOS hides some folders (Mail, Messages, Safari) from Terminal unless it has Full Disk
Access. The scan skips what it cannot read rather than failing, so a few folders may be
missing from the results. That is fine for a first pass.

Done 2026-10-01: 21 GB free became 103 GB. Old projects went to the external drive as zips.
(The Spotify cache was cleared by mistake; it refills as songs play.)

## Step 2: Setup checklist

What runs where:

| | M1 (home server) | M4 (laptop) |
|---|---|---|
| Notes | **The vault.** Only the M1 edits it: the API reads and writes the files | nothing, after the move |
| Database | Postgres in Docker Desktop, holds the captures | nothing, after the move |
| Search | API calls the M4 over Tailscale | `nomic-embed-text` in Ollama |
| Answers | API calls the M4 over Tailscale | `llama3.1:8b` in Ollama |
| Website | API + web app, started at login | |

While the M4 is asleep or away, the M1 keeps working: search falls back to keyword-only
within a few seconds, and asking a question says the model is unreachable.

**Choices:** Docker Desktop (memory limit 2 GB, the M1 has 8), captures moved from the M4,
and the vault moved to the M1 through Google Drive.

**One editor.** After the move, notes change only on the M1, through the website (from any
device over Tailscale) or Obsidian on the M1. Google Drive is the carrier for the move and
then an off-site backup with its own version history. With one writer, Drive never has two
versions of a file to reconcile, so it never makes conflict copies.

Writing to the vault ends the old read-only rule, so writes come with an undo: before the
API changes or removes a note, it saves the previous version under `.local/vault-history/`,
outside the vault. Nothing is ever hard-deleted.

### Moving the vault (M4, once)

1. Install Google Drive for desktop on the M4 and sign in with the account the M1 uses.
2. Quit Obsidian.
3. **Copy** (don't move) the vault folder into `My Drive/Personal Vault/`.
4. Wait until the Drive menu bar icon says everything is up to date.
5. Rename the original on the M4 to `<name> (before move)` and stop opening it. It stays as
   a fallback until the M1 is checked, then can be deleted.
6. On the M4: `scripts/autostart.sh off`, so its copy of the app stops indexing.

`.obsidian/` did not come across through Drive. The API needs it to recognise the vault
root, so an empty one was created on the M1; Obsidian on the M1 starts with default settings.

### On the M1

- [x] **Tailscale:** `tailscale up`, same account as the M4 and phone. The M1 is `macbook-air-5`.
- [x] **Docker Desktop:** `brew install --cask docker`. Settings: Start at login on,
      Resources → Memory 2 GB.
- [x] **Google Drive:** `brew install --cask google-drive`, sign in. Right-click
      `Personal Vault` → **Available offline**.
- [x] **`.env`:** password generated, `OBSIDIAN_VAULT_PATH` =
      `~/Library/CloudStorage/GoogleDrive-<account>/My Drive/Personal Vault`,
      `OLLAMA_URL=http://<m4 tailscale ip>:11434`.
      **Port 5434**, not 5433: another project's Postgres already listens on 5433 here.
- [x] **Python and web:** `uv venv --python 3.14 && uv pip install -r api/requirements.txt`,
      `cd web && npm install`.
- [x] **Database:** `docker compose up -d`. The first start left no `personalos` database
      (cause not found); `docker exec personal-os-db createdb -U personalos personalos` fixed it.
      Then the M4's dump was restored (below). A copy is kept in `.local/backups/`.
- [x] **Always on:** `scripts/autostart.sh on`, `tailscale serve --bg 5173`
      → `https://macbook-air-5.<tailnet>.ts.net`.
- [ ] **Never sleep on the charger:** `sudo pmset -c sleep 0 displaysleep 10`. Keep the lid
      open (a closed lid sleeps the Mac) and the charger in.
- [ ] **Google and GitHub:** connect again from Settings. Their tokens were in the M4's
      Keychain and don't move with the database.

### On the M4

- [x] **Ollama reachable from the M1, over Tailscale only:**
      `tailscale serve --bg --tcp 11434 tcp://localhost:11434`. Ollama itself still listens
      only on localhost, so nothing on the home Wi-Fi can reach it. Models:
      `llama3.1:8b`, `nomic-embed-text`.
- [x] **Move the database:**
      `docker exec personal-os-db pg_dump -U personalos -Fc personalos > ~/Desktop/personalos.dump`,
      copied to the M1 through Drive, then on the M1:
      `docker exec -i personal-os-db pg_restore -U personalos -d personalos --clean --if-exists --no-owner < personalos.dump`.

### Check (2026-10-03)

Health ok; 43 notes read, matching the vault; search finds ADR-002 first for "why postgres
over sqlite"; a question is answered by the M4 in about 30 s; 5 captures restored; the
`ts.net` address serves the site and the API.
