# cf-agent

A command-line tool for managing **Adobe AEM Content Fragments** for the Moveworks Marketplace — connectors and plugins.

Create, edit, publish, and inspect fragments across **PROD / STAGE / DEV** with an interactive guided mode or scriptable one-liners. Every rule (field types, allowed values, length limits, required fields) is read **live from the AEM model**, so the CLI always matches what AEM enforces.

---

## Features

- **Interactive guided mode** (`-i`) for creating and editing — pick from lists, see field descriptions, validate as you type.
- **Scriptable one-liners** (`-f name=value`) for automation.
- **Edit without a UUID** — find and select a fragment by model + name filter, or by slug.
- **Live validation** against the model: required fields, enums, max-length, regex, kebab-case slugs, duplicate-slug detection, and cross-field rules.
- **Content guides from markdown files**, with automatic checking that every referenced AEM image exists.
- **Upload assets straight from your machine** (`asset upload`) — one file, or every image in a folder, into the DAM without opening AEM.
- **Missing images offered inline** — when a guide references an image that isn't in AEM yet, upload it without leaving the form.
- **Per-fragment image folders** — a fragment's guide images are filed under `images/<slug>/` instead of one shared folder.
- **Drive it from Claude** (`cf-agent mcp`) — ask for fragments in plain language, with the same validation the CLI enforces.
- **Smart defaults** — logo/asset folders auto-prefixed, plugin slugs seeded with the system name, model folders chosen automatically.
- **Guardrails** — immutable fields and review-locked fragments are caught up front, not after you fill in a form.
- **Command history** — ↑/↓ recall previous entries in interactive prompts.

---

## Table of Contents

- [Installation](#installation)
- [Authentication](#authentication)
- [Environments](#environments)
- [Field reference](#field-reference)
- [Creating a fragment](#creating-a-fragment)
- [Updating a fragment](#updating-a-fragment)
- [Content guides & images](#content-guides--images)
- [Other fragment commands](#other-fragment-commands)
- [Models & assets](#models--assets)
- [Uploading assets](#uploading-assets)
- [Using cf-agent from Claude](#using-cf-agent-from-claude)
- [Troubleshooting / Debug](#troubleshooting--debug)

---

## Installation

Requires **Python 3.10+**. Install into a virtual environment to keep it isolated.

```bash
# 1. Create & activate a virtual environment
python3 -m venv ~/.venvs/cf-agent
source ~/.venvs/cf-agent/bin/activate

# 2. Install cf-agent
pip install "git+https://github.com/krishnakumar1990/cf-agent.git"

# 3. Verify
cf-agent --help
```

**New terminal later?** Re-activate first: `source ~/.venvs/cf-agent/bin/activate`
To auto-activate in every shell: `echo 'source ~/.venvs/cf-agent/bin/activate' >> ~/.zshrc`

> To upgrade, uninstall, or reinstall, see [Troubleshooting / Debug](#troubleshooting--debug).

---

## Authentication

Login uses Adobe IMS OAuth (browser-based). Credentials are stored in `~/.cf-agent/config`, tokens in `~/.cf-agent/tokens`.

```bash
cf-agent login          # prompts for Adobe Client ID + Secret, opens browser, then pick an environment
cf-agent whoami         # show the authenticated user, org, scopes, and active environment
cf-agent logout         # clear stored tokens
```

If your team shares a preset with pre-filled config:

```bash
cf-agent login --preset shared.env
```

Tokens expire; if you see **"Not logged in"** or a **401**, run `cf-agent login` again.

---

## Environments

One login works across all environments. The **active** environment is where every fragment operation runs.

```bash
cf-agent env list       # show PROD / STAGE / DEV and which is current
cf-agent env select     # switch interactively
cf-agent env current    # show the active environment
```

---

## Field reference

Field names, types, and rules are read from the live model. These are the fields for each type today.

### Connector (`marketplace-connector`)

| Field | Required | Rules |
|---|---|---|
| `marketplace_name` | ✅ | Title Case; max 255 |
| `slug` | ✅ | lowercase kebab-case; contains the system name; unique; **immutable** |
| `description` | ✅ | ends with `.`; max **400** |
| `logo` | ✅ | an **SVG hosted in AEM**; enter the file name only (auto-prefixed to the logos folder) |
| `solution_tags` | ✅ | pick from the list **or** type a new Title Case tag; multiple allowed |
| `product_family` | ✅ | from the model list (e.g. `Google Cloud`, `Microsoft Graph`) |
| `content_guide` | — | markdown guide; supply a file path |
| `video` | — | YouTube / Vimeo / Loom URL |

### Plugin (`marketplace-plugin`)

| Field | Required | Rules |
|---|---|---|
| `marketplace_name` | ✅ | Title Case; must **not** include the system name; max 255 |
| `slug` | ✅ | lowercase kebab-case; **must start with a system** (e.g. `workday-view-pto`); unique; **immutable** |
| `description` | ✅ | ends with `.`; max **400** |
| `availability` | ✅ | from the model (`VALIDATED`, `INSTALLABLE`, `IDEA`, `BUILT_IN`); **immutable** after create |
| `installation_asset_uuid` | ⛔/✅ | **required when `availability = INSTALLABLE`**, forbidden otherwise; lowercase UUID |
| `solution_tags` | ✅ | pick from the list **or** type a new Title Case tag; multiple allowed |
| `purple_chat_link` | ✅ | starts with `https://marketplace.moveworks.com/purple-chat?conversation`; no `mock_id`; max 30000 |
| `systems` | ✅ | from the model list; multiple allowed; **immutable** |
| `agent_capabilities` | — | from the model list (e.g. `Ambient Agent`) |
| `content_guide` | — | markdown guide; supply a file path |
| `video` | — | YouTube / Vimeo / Loom URL |

> **Field names come from the live model** — the interactive prompts always show the exact current name. `installation_asset_uuid` was formerly `installation_uuid`; the CLI accepts both during the transition.
>
> **`reviewRequired`** — setting this to `true` sends the fragment to review, which **locks it** from further edits until an approver releases it. Leave it off until the content is final.

---

## Creating a fragment

### Interactive (recommended)

```bash
cf-agent fragments create -i
```

Walks you through it: pick the model, then it fills the folder automatically and prompts each field with its rules. For plugins it asks for `systems` first and pre-seeds the slug (`workday-…`); `installation_asset_uuid` is only asked when availability is `INSTALLABLE`. Each value is validated as you enter it.

### One-liner — Connector

```bash
cf-agent fragments create \
  --parent-path "/content/dam/marketplace/content-fragment-resources/connector" \
  --model-path  "/conf/marketplace/settings/dam/cfm/models/marketplace-connector" \
  --name  "google-drive-connector" \
  --title "Google Drive Connector" \
  -f marketplace_name="Google Drive Connector" \
  -f slug="google-drive-connector" \
  -f description="Connects to Google Drive to search and retrieve files." \
  -f logo="google-drive.svg" \
  -f solution_tags="IT,Productivity" \
  -f product_family="Google Workspace" \
  -f content_guide=~/Desktop/connector-guide.md
```

### One-liner — Plugin

```bash
cf-agent fragments create \
  --parent-path "/content/dam/marketplace/content-fragment-resources/plugin" \
  --model-path  "/conf/marketplace/settings/dam/cfm/models/marketplace-plugin" \
  --name  "workday-view-pto-balance" \
  --title "View PTO Balance" \
  -f marketplace_name="View PTO Balance" \
  -f slug="workday-view-pto-balance" \
  -f description="Check your current PTO balance from the Moveworks AI Assistant." \
  -f availability="VALIDATED" \
  -f solution_tags="HR - Time & Absence,HR - Employee Records" \
  -f purple_chat_link="https://marketplace.moveworks.com/purple-chat?conversation=%7B%22messages%22%3A%5B%5D%7D" \
  -f systems="workday" \
  -f agent_capabilities="Ambient Agent" \
  -f content_guide=~/Desktop/plugin-guide.md
```

For an **INSTALLABLE** plugin, use `-f availability="INSTALLABLE"` and add `-f installation_asset_uuid="34cff60f-f3c8-48f9-b1c9-7658ead0d994"`.

> Multi-value fields are comma-separated inside one flag: `-f solution_tags="HR - Benefits,IT"`.

---

## Updating a fragment

You can identify the fragment **three ways** — no UUID required.

### 1. Interactive (recommended)

```bash
cf-agent fragments update -i
```

- Pick the model (connector / plugin).
- Type a filter (name or slug) — or Enter to list all.
- Choose from the numbered results.
- Edit each **editable** field; press **Enter to skip** (keep the current value). Enums show their pick-list, exactly like create.

### 2. By slug (scriptable)

```bash
cf-agent fragments update --slug google-drive-connector \
  --model-path "/conf/marketplace/settings/dam/cfm/models/marketplace-connector" \
  -f content_guide=~/Desktop/updated-guide.md
```

### 3. By id

```bash
cf-agent fragments update <id> -f description="Updated description."
```

### Which fields are editable

Only these fields can be changed on update — everything else is locked.

| Model | Editable fields |
|---|---|
| **Connector** | `logo`, `content_guide` |
| **Plugin** | `marketplace_name`, `description`, `purple_chat_link`, `solution_tags`, `installation_asset_uuid`, `content_guide` |

**Locked (cannot be changed after creation):** `slug`, `systems`, `availability`. Attempting to change one — or to edit any non-listed field — is rejected with a clear message.

> **Review-locked fragments:** if a fragment was sent to review (`reviewRequired = true`), the CLI tells you **immediately on selection** that it's locked and can't be edited until the review is completed or cancelled in AEM — so you don't fill in a form only to be refused at the end.

---

## Content guides & images

`content_guide` is a markdown guide supplied as a **file path** (both create and update):

```bash
-f content_guide=~/Desktop/my-guide.md
```

When you provide the file, the CLI reads it and **verifies every AEM image it references exists**. It checks `/content/dam/...` paths in markdown images `![](…)`, links `[](…)`, and HTML `<img src="…">` (full AEM URLs too). If any referenced image is missing, the operation is blocked and the missing paths are listed.

**Missing images are offered inline.** In interactive mode, if the guide references an image that isn't in the DAM yet, the CLI asks for the local file and uploads it without you leaving the form:

```
  The markdown references AEM asset(s) not found in the DAM:
    - /content/dam/marketplace/images/shot.png

  Missing: /content/dam/marketplace/images/shot.png
  Will upload to /content/dam/marketplace/images/workday/shot.png
  Upload a local file to create shot.png? [y/N]: y
  Local file path: ~/Desktop/shot.png
  ✓ Uploaded: /content/dam/marketplace/images/workday/shot.png
```

When creating a fragment, images are filed under the fragment's own slug folder and the guide is repointed to match — so you can keep writing whatever path suits you and the stored guide always points at the file that was actually created. Uploading several? Do them in one go first with `cf-agent asset upload <folder> --slug <slug>`, then the guide validates cleanly.

This needs AWS credentials ([one-time setup](#one-time-setup)). Without them the CLI explains the setup instead of offering. You can always upload separately or by hand in AEM and reference the `/content/dam/...` path yourself.

Relative image paths from a raw export (e.g. `![](image.png)`) are **not** picked up — reference images by DAM path.

---

## Other fragment commands

```bash
# List (optionally by folder)
cf-agent fragments list --path /content/dam/marketplace/content-fragment-resources/connector --limit 25

# Get one by id
cf-agent fragments get <id>

# Dry-run validate a payload against the model — no write
cf-agent fragments validate --model-path "$CONN_M" -f description="A connector." --partial

# Publish one or more
cf-agent fragments publish <id> [<id> ...]

# Delete (‑‑yes to skip the prompt)
cf-agent fragments delete <id> --yes

# Copy to another folder (‑‑deep to include references)
cf-agent fragments copy <id> --destination /content/dam/.../archive [--deep]

# List variations
cf-agent fragments variations <id>
```

> `cf-agent fragments search` exists but the underlying AEM search endpoint is not available on all environments — prefer `fragments list` or the `update -i` filter to find fragments.

---

## Models & assets

```bash
# List Content Fragment Models (connector, plugin, …)
cf-agent models list

# Check whether an asset exists in the DAM (bare name resolved against a known folder)
cf-agent asset exists workday.svg --logo
cf-agent asset exists /content/dam/marketplace/logos/workday.svg
```

`asset exists` returns exit code `0` if present, `1` if not — usable in scripts. It requires the `aem.assets.author` scope.

---

## Uploading assets

`asset upload` puts a local file into the AEM DAM, so logos and content-guide images no longer have to be uploaded by hand in AEM.

### One-time setup

Store your AWS credentials once — they are used to stage the file (see [How it works](#how-it-works) below). Ask the team for a key; it will be sent to you securely.

```bash
cf-agent asset credentials set
```

You are prompted for both values. The secret is hidden as you paste it, so it never lands in your shell history.

```bash
cf-agent asset credentials show    # confirm they're stored (never prints the secret)
cf-agent asset credentials clear   # remove them
```

Credentials are kept in your **OS keychain** — macOS Keychain, Windows Credential Manager, or the Linux Secret Service — encrypted at rest, never in a plaintext file and never in the repo. Nothing else needs configuring: the staging bucket, region and prefix ship with the CLI.

### Upload one file

```bash
# Into the marketplace logos folder
cf-agent asset upload ./workday.svg --logo

# Into a fragment's own image folder — /content/dam/marketplace/images/<slug>/
cf-agent asset upload ./screenshot.png --slug workday

# Into the shared images folder
cf-agent asset upload ./screenshot.png --image

# Into any DAM folder
cf-agent asset upload ./diagram.png --root /content/dam/marketplace/screenshots

# Rename on the way in
cf-agent asset upload ./Untitled_204.png --slug workday --name workflow-diagram.png
```

On success it prints the DAM path and `assetId`:

```
✓ Uploaded: /content/dam/marketplace/logos/workday.svg
  assetId: urn:aaid:aem:d3fc8f49-4c8f-49ee-b01f-3f00999201b0
```

Use that `/content/dam/...` path directly as a `logo` value or in content-guide markdown. Add `--json` for scripting.

### Upload a whole folder

Point at a folder instead of a file and every image inside it is uploaded. Guide screenshots usually come out of an export tool together, so this is the quickest way to seed a fragment's images:

```bash
cf-agent asset upload ~/Desktop/workday-shots --slug workday
```

```
Uploading 3 image(s) to /content/dam/marketplace/images/workday
  · shot-1.png
  · shot-2.png
  · shot-3.png

✓ 3 uploaded to /content/dam/marketplace/images/workday
```

- **Top level only** — nested folders are not walked.
- **Images only** — `.png .jpg .jpeg .gif .svg .webp .bmp .tif .tiff`. A stray `notes.txt` or `.DS_Store` is ignored.
- **Already-uploaded files are skipped**, so re-running after a partial upload sends only what's missing. Use `--overwrite` to replace them.
- **One bad file doesn't stop the batch** — it's reported and the rest continue; the command exits non-zero if anything failed.

`--name` applies to a single file only, since one name can't cover many files.

### Where images live

Each fragment's content-guide images belong in its own folder, named for the slug:

```
/content/dam/marketplace/images/<slug>/
```

The folder is created automatically the first time you upload into it. Logos stay in the shared `logos` folder.

> Fragments created before this convention have their images in the flat `images` folder. Those still work — nothing was moved.

### How it works

AEM's Assets API cannot accept a binary directly from your user token — it can only *pull* an asset from a URL. So the CLI uploads the file to an S3 staging bucket, hands AEM a short-lived pre-signed URL to fetch it from, waits for the import to finish, then deletes the staged copy. This is why AWS credentials are needed at all; the staged object is temporary and removed automatically.

> **Credential precedence:** `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` environment variables win if set (useful in CI), then the OS keychain, then any `~/.aws` profile. Set nothing and the keychain is used.

---

## Using cf-agent from Claude

`cf-agent mcp` runs an MCP server, so Claude Desktop (or any MCP client) can carry out these operations for you — listing fragments, checking fields, creating and updating content, uploading assets — from a plain-language request.

```bash
pip install "cf-agent[mcp]"
```

Then add it to your Claude Desktop config — **Settings → Developer → Edit Config**, or edit the file directly:

| | |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

```json
{
  "mcpServers": {
    "cf-agent": {
      "command": "/Users/you/.venvs/cf-agent/bin/cf-agent",
      "args": ["mcp"]
    }
  }
}
```

Use the **absolute path** — run `which cf-agent` (macOS) or `where cf-agent` (Windows) to get it. Claude Desktop doesn't inherit your shell `PATH` or activated venv. On Windows, double the backslashes in JSON. Then quit Claude Desktop completely (⌘Q, or Quit from the Windows tray) and reopen.

**What it can do:** read operations (`list_fragments`, `get_fragment`, `search_fragments`, `list_models`, `get_model_schema`, `list_variations`, `asset_exists`) and write operations (`create_fragment`, `update_fragment`, `copy_fragment`, `upload_asset`).

**What it can't:** deleting and publishing are deliberately excluded — both are permanent or externally visible, so they stay a deliberate action at the command line.

Everything written is validated against the live AEM model first, exactly as the CLI does, so an agent can't create a fragment the CLI would have rejected.

> Your Adobe sign-in lasts about a day. When it lapses every request reports an expired token — run `cf-agent login` in a terminal; Claude Desktop can't open the browser sign-in for you.

---

## Troubleshooting / Debug

### Update the CLI to the latest version

```bash
source ~/.venvs/cf-agent/bin/activate
pip install --upgrade "git+https://github.com/krishnakumar1990/cf-agent.git"
cf-agent --help        # confirm it still runs
```

### Uninstall & reinstall

```bash
source ~/.venvs/cf-agent/bin/activate

# uninstall
pip uninstall cf-agent

# reinstall (fresh copy)
pip install "git+https://github.com/krishnakumar1990/cf-agent.git"
```

For a completely clean slate, delete and recreate the virtual environment:

```bash
deactivate 2>/dev/null
rm -rf ~/.venvs/cf-agent
python3 -m venv ~/.venvs/cf-agent
source ~/.venvs/cf-agent/bin/activate
pip install "git+https://github.com/krishnakumar1990/cf-agent.git"
```

> Uninstalling does **not** remove your login. Config and tokens live in `~/.cf-agent/` — delete that folder to fully reset (`rm -rf ~/.cf-agent`), then `cf-agent login` again.

### Common errors

| Message | Cause & fix |
|---|---|
| `Not logged in` / `401` | Token expired → `cf-agent login`. |
| `403 Forbidden — You are not allowed to modify this fragment` | The fragment is **review-locked** (`reviewRequired = true`) or your Adobe ID lacks write access on this environment. Release the review in AEM, or ask an admin for access. |
| `… is in review and locked` | Sent to review — complete/cancel the review in AEM, then retry. |
| `Field 'X' is not editable on update` | Only the editable fields above can change on update. |
| `Field 'X' cannot be changed after creation` | `slug` / `systems` / `availability` are immutable. |
| `Slug '…' is already in use` | Choose a unique slug. |
| `Referenced asset does not exist in AEM` | The logo / a content-guide image isn't in the DAM — upload it with `cf-agent asset upload … --logo` / `--image`, or check with `cf-agent asset exists …`. |
| `cf-agent: command not found` | The venv isn't active → `source ~/.venvs/cf-agent/bin/activate`. |
| `Asset upload requires boto3` / `requires 'keyring'` | Your install predates 1.1.0, when these became base dependencies → `pip install --upgrade "git+https://github.com/krishnakumar1990/cf-agent.git"`. |
| `Could not stage file to S3: access denied` | The AWS key lacks permission on the staging bucket, or none is set → `cf-agent asset credentials show`. |
| `The staged file isn't readable via its pre-signed URL` | The AWS key can write but not read the staging bucket — it needs `s3:GetObject` too. Ask the team for a corrected key. |
| `No image files found directly in …` | The folder has no recognised images at its top level — nested folders aren't walked. |
| `--name applies to a single file, not a folder` | Drop `--name`; one name can't cover a batch. |
| `Cannot resolve folder …` | The destination DAM folder doesn't exist. `--slug` creates it for you; `--root` does not. |
| No cf-agent tools in Claude Desktop | Quit the app fully (⌘Q, or Quit from the Windows tray) and reopen. Check the config is valid JSON and the command path is absolute. |

### Inspect your session

```bash
cf-agent whoami        # user, org, scopes, token expiry, active environment
cf-agent env current   # which environment you're pointed at
```

`whoami` is the fastest way to debug `403`s — confirm the token's org matches the environment.
