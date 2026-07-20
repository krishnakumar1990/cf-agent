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

**Workflow for images:** upload the image to AEM first (e.g. under the fragment's folder), reference it by its `/content/dam/...` path in the markdown, then create/update the guide. Relative image paths from a raw export (e.g. `![](image.png)`) are **not** uploaded — reference images by their DAM path.

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
| `Referenced asset does not exist in AEM` | The logo / a content-guide image isn't in the DAM — upload it first (`cf-agent asset exists …` to check). |
| `cf-agent: command not found` | The venv isn't active → `source ~/.venvs/cf-agent/bin/activate`. |

### Inspect your session

```bash
cf-agent whoami        # user, org, scopes, token expiry, active environment
cf-agent env current   # which environment you're pointed at
```

`whoami` is the fastest way to debug `403`s — confirm the token's org matches the environment.
