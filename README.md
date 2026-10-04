# Simple Notes

A minimal, private markdown notes app for Mac. Write in a clean Notion-style page where headings,
checklists, tables and code render as you type. Organize notes in folders. Everything stays on your
Mac, and the app never connects to the internet.

## Requirements

- A Mac with Apple Silicon (M1, M2, M3, M4 or later). Intel Macs are not supported.
- macOS 13 Ventura or newer.

## Install

**1. Download.** Get **[SimpleNotes.dmg](https://github.com/boredtoolbox/simplenotes/releases/latest/download/SimpleNotes.dmg)**
from the [latest release](https://github.com/boredtoolbox/simplenotes/releases/latest).

**2. Copy it into Applications.** Double-click `SimpleNotes.dmg`. In the window that opens, drag
**SimpleNotes** onto the **Applications** folder. Then eject the disk image (the ⏏ button next to
"Simple Notes" in Finder's sidebar).

**3. Allow it to open (first launch only).** The app isn't from the App Store, so macOS blocks it
the first time:

1. Open **Applications** and double-click **SimpleNotes**. macOS says it can't verify the app.
   Click **Done** (or **Cancel**).
2. Open **System Settings → Privacy & Security** and scroll down to the **Security** section.
3. Next to *"SimpleNotes" was blocked to protect your Mac*, click **Open Anyway**, enter your Mac
   password, and click **Open Anyway** again.

From then on it opens like any other app. You can keep it in the Dock by right-clicking its icon
and choosing **Options → Keep in Dock**.

> **"SimpleNotes is damaged and can't be opened"?** This sometimes appears instead of the warning
> above. It means macOS has flagged the download, not that the app is broken. Open **Terminal**,
> run the command below, then open the app again:
>
> ```
> xattr -dr com.apple.quarantine /Applications/SimpleNotes.app
> ```

## Update

Download the new `SimpleNotes.dmg` and drag the app into Applications again, choosing **Replace**.
Your notes are kept.

## Your notes

Notes are saved automatically as you type, in a single file on your Mac:

```
~/Library/Application Support/noteapp/notes.db
```

The app also makes a daily backup in the `backups` folder next to it and keeps the last 30.
Clicking the path in the app's bottom bar shows the file in Finder.

## Uninstall

Drag **SimpleNotes** from Applications to the Trash. To delete your notes too, also delete the
`~/Library/Application Support/noteapp` folder (in Finder: **Go → Go to Folder…**, then paste the
path).

## Tips

- Type `/` at the start of a line for headings, lists, to-dos, quotes, code blocks, dividers and
  tables. Keep typing to filter, for example `/h2` or `/todo`.
- **⌘N** new note · **⌘⇧N** new folder · **⌘F** search · **⌘B / ⌘I** bold / italic ·
  **⌘K** link · **⌘↩** checkbox · **⌘1** show/hide sidebar · **⌘⇧D** dark mode
- Drag notes and folders in the sidebar to reorder them, or drag a note onto a folder to move it.

## Build from source

Requires Python 3.9+.

```
python3 -m pip install PyQt6          # run directly: python3 noteapp.py
./packaging/build_mac.sh              # builds dist/SimpleNotes.app and dist/SimpleNotes.dmg
```
