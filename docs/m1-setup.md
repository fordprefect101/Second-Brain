# M1 home server: cleanup and setup

The 8 GB MacBook Air M1 stays at home, plugged in, always on. It will hold the notes,
the database, search and the website; the answering model stays on the M4 laptop.
Step 1 makes room on it. Step 2, the setup checklist, is added here next.

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
- *(add any others: browser data, WhatsApp, Photos, game installs…)*

### Space the M1 needs for its new job

About **15–20 GB free**: the notes, the database, the small meaning-search model
(~0.3 GB), and room to breathe. The answering model stays on the M4, so it needs no
space here.

### A note on Terminal permissions

macOS hides some folders (Mail, Messages, Safari) from Terminal unless it has Full Disk
Access. The scan skips what it cannot read rather than failing, so a few folders may be
missing from the results. That is fine for a first pass.

## Step 2: Setup checklist

*Coming next.*
