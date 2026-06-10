# cf-agent

CLI for managing Adobe AEM Content Fragments — Moveworks Marketplace.

Supports interactive guided mode and one-liner commands for creating, reading, updating, deleting, and publishing Content Fragments across PROD, STAGE, and DEV environments.

---

## Table of Contents

- [Installation](#installation)
- [Authentication](#authentication)
- [Environment Management](#environment-management)
- [Fragments](#fragments)
  - [list](#list)
  - [get](#get)
  - [search](#search)
  - [create](#create)
  - [update](#update)
  - [delete](#delete)
  - [publish](#publish)
  - [copy](#copy)
  - [variations](#variations)
- [Models](#models)
- [Diagnostics](#diagnostics)
- [Upgrading](#upgrading)

---

## Installation

Requires Python 3.10 or later.

```bash
pip install "git+https://github.com/krishnakumar1990/cf-agent.git"
```

Verify the installation:

```bash
cf-agent --help
```

---

## Authentication

Authentication uses Adobe IMS OAuth (browser-based login). Credentials are stored in `~/.cf-agent/config` and tokens in `~/.cf-agent/tokens`.

### First-time login

Use the shared preset file to pre-fill team configuration:

```bash
cf-agent login --preset shared.env
```

You will be prompted for:
- **Adobe Client ID** — from your Adobe Developer Console OAuth app
- **Adobe Client Secret** — from your Adobe Developer Console OAuth app

The browser will open for Adobe IMS login. After completing the login, you will be prompted to select an AEM environment (PROD, STAGE, or DEV).

### Login without a preset

```bash
cf-agent login
```

You will be prompted for all values including Adobe scopes and redirect URI.

### Logout

Clears stored OAuth tokens. Does not remove credentials.

```bash
cf-agent logout
```

### Check current identity

Decodes the active access token and shows the authenticated user, IMS org, scopes, and selected environment.

```bash
cf-agent whoami
```

Example output:
```
User:        john.doe@company.com
IMS org:     ims-na1
Client ID:   99dd32f5fb3844c9af0e838c84b81e44
Token:       expires in 84231s
Scopes:      openid,AdobeID,aem.fragments.management,aem.folders
Environment: https://author-p<PROGRAM_ID>-e<ENV_ID>.adobeaemcloud.com/adobe/sites
```

---

## Environment Management

One access token works across all environments. The active environment determines where all fragment operations are applied.

### List available environments

```bash
cf-agent env list
```

Example output:
```
  1. PROD      https://author-p<PROGRAM_ID>-e<ENV_ID_PROD>.adobeaemcloud.com/adobe/sites
  2. STAGE     https://author-p<PROGRAM_ID>-e<ENV_ID_STAGE>.adobeaemcloud.com/adobe/sites (current)
  3. DEV       https://author-p<PROGRAM_ID>-e<ENV_ID_DEV>.adobeaemcloud.com/adobe/sites
  4. MW-PROD   https://author-p<MW_PROGRAM_ID>-e<ENV_ID_MW_PROD>.adobeaemcloud.com/adobe/sites
  5. MW-STAGE  https://author-p<MW_PROGRAM_ID>-e<ENV_ID_MW_STAGE>.adobeaemcloud.com/adobe/sites
  6. MW-DEV    https://author-p<MW_PROGRAM_ID>-e<ENV_ID_MW_DEV>.adobeaemcloud.com/adobe/sites
```

### Switch environment interactively

```bash
cf-agent env select
```

### Show current environment

```bash
cf-agent env current
```

### Set environment manually

```bash
cf-agent env use https://author-p<PROGRAM_ID>-e<ENV_ID>.adobeaemcloud.com/adobe/sites
```

---

## Fragments

All fragment commands operate against the currently selected environment.

---

### list

List content fragments, optionally filtered by folder path.

```bash
cf-agent fragments list [OPTIONS]
```

| Option | Description |
|---|---|
| `--path` | Filter by DAM folder path |
| `--limit` | Max results (default: 10) |
| `--cursor` | Pagination cursor from previous response |
| `--references` | Include references: `DIRECT` or `TRANSITIVE` |
| `--json` | Output raw JSON |

**Examples:**

```bash
# List 10 fragments
cf-agent fragments list

# List fragments in a specific folder
cf-agent fragments list --path /content/dam/marketplace/content-fragment-resources/connector

# Paginate
cf-agent fragments list --limit 25
cf-agent fragments list --limit 25 --cursor <cursor-from-previous-output>
```

---

### get

Get a single content fragment by ID.

```bash
cf-agent fragments get <ID> [--json]
```

**Example:**

```bash
cf-agent fragments get c84afcfd-1950-42d7-9842-9667d81e7e2a
```

Example output:
```
ID:     c84afcfd-1950-42d7-9842-9667d81e7e2a
Title:  Krishna Demo
Path:   /content/dam/marketplace/content-fragment-resources/connector/krishna-demo
Model:  /conf/marketplace/settings/dam/cfm/models/marketplace-connector
ETag:   "abc123"

Fields:
  marketplace_name: ['Krishna Demo']
  slug: ['krishna-demo']
  description: ['Krishna demo.']
```

---

### search

Full-text search across content fragments.

```bash
cf-agent fragments search <QUERY> [OPTIONS]
```

| Option | Description |
|---|---|
| `--path` | Scope search to a folder path |
| `--limit` | Max results (default: 10) |
| `--json` | Output raw JSON |

**Examples:**

```bash
cf-agent fragments search "workday"

cf-agent fragments search "PTO" --path /content/dam/marketplace/content-fragment-resources/plugin
```

---

### create

Create a new content fragment.

#### Interactive mode (recommended for first use)

Guides you step by step — picks model from a list, shows field descriptions, validates each field before moving on, and supports reading Content Guide from a markdown file.

```bash
cf-agent fragments create -i
```

#### One-liner mode — Connector

```bash
cf-agent fragments create \
  --parent-path "/content/dam/marketplace/content-fragment-resources/connector" \
  --model-path "/conf/marketplace/settings/dam/cfm/models/marketplace-connector" \
  --name "workday-benefits-connector" \
  --title "Workday Benefits Connector" \
  -f marketplace_name="Workday Benefits Connector" \
  -f slug="workday-benefits-connector" \
  -f description="Connects to Workday to retrieve employee benefits information." \
  -f availability="VALIDATED" \
  -f solution_tags="HR - Benefits,HR - Employee Records" \
  -f logo="workday.svg" \
  -f product_family="google-cloud" \
  -f video="https://youtu.be/CCEYG_K9Pzg" \
  -f content_guide=~/Desktop/connector-guide.md \
  -f reviewRequired="true"
```

#### One-liner mode — Plugin

```bash
cf-agent fragments create \
  --parent-path "/content/dam/marketplace/content-fragment-resources/plugin" \
  --model-path "/conf/marketplace/settings/dam/cfm/models/marketplace-plugin" \
  --name "workday-view-pto-balance" \
  --title "View PTO Balance" \
  -f marketplace_name="View PTO Balance" \
  -f slug="workday-view-pto-balance" \
  -f description="Allows employees to check their current PTO balance directly from the Moveworks AI Assistant." \
  -f availability="VALIDATED" \
  -f solution_tags="HR - Time & Absence,HR - Employee Records" \
  -f purple_chat_link="https://marketplace.moveworks.com/purple-chat?conversation=%7B%22messages%22%3A%5B%5D%7D" \
  -f systems="workday" \
  -f agent_capabilities="Ambient Agent" \
  -f video="https://youtu.be/CCEYG_K9Pzg" \
  -f content_guide=~/Desktop/plugin-guide.md \
  -f reviewRequired="true"
```

#### `-f` flag reference

| Field | Model | Type | Notes |
|---|---|---|---|
| `marketplace_name` | Both | text | Title Case, max 255 chars |
| `slug` | Both | text | Kebab-case, e.g. `my-plugin-name` |
| `description` | Both | text | Must end with a period, max 2000 chars |
| `availability` | Both | enumeration | `IDEA`, `BUILT_IN`, `VALIDATED`, `INSTALLABLE` |
| `solution_tags` | Both | enumeration (multi) | Comma-separate multiple values |
| `logo` | Connector | content-reference | Filename only, e.g. `workday.svg` |
| `product_family` | Connector | enumeration | e.g. `google-cloud`, `microsoft-graph` |
| `purple_chat_link` | Plugin | text | Must be a `marketplace.moveworks.com/purple-chat?conversation=...` URL |
| `systems` | Plugin | enumeration (multi) | e.g. `workday`, `servicenow,jira` |
| `agent_capabilities` | Plugin | enumeration (multi) | e.g. `Ambient Agent` |
| `content_guide` | Both | long-text | File path to a `.md` file, e.g. `~/Desktop/guide.md` |
| `video` | Both | text | YouTube, Vimeo, or Loom URL |
| `installation_uuid` | Both | text | Connector: 32 hex chars · Plugin: full UUID format |
| `reviewRequired` | Both | enumeration | `true` to send for review and lock |

**Multi-value fields** — comma-separate values in a single `-f` flag:
```bash
-f solution_tags="HR - Benefits,HR - Other"
-f systems="workday,servicenow"
```

**Content guide from file** — provide a file path instead of inline text:
```bash
-f content_guide=~/Desktop/my-guide.md
```

---

### update

Update a content fragment by ID. Automatically fetches the ETag — no manual ETag handling needed.

```bash
cf-agent fragments update <ID> [OPTIONS]
```

| Option | Description |
|---|---|
| `--title` | New fragment title |
| `-f NAME=VALUE` | Update a field value (repeatable) |
| `--patch` | Raw JSON Patch array for advanced use |
| `--json` | Output raw JSON |

**Examples:**

```bash
# Update title only
cf-agent fragments update c84afcfd-1950-42d7-9842-9667d81e7e2a \
  --title "New Title"

# Update one or more fields
cf-agent fragments update c84afcfd-1950-42d7-9842-9667d81e7e2a \
  -f description="Updated description." \
  -f availability="INSTALLABLE"

# Update title and fields together
cf-agent fragments update c84afcfd-1950-42d7-9842-9667d81e7e2a \
  --title "New Title" \
  -f slug="new-slug" \
  -f solution_tags="HR - Benefits,IT"

# Update content guide from a file
cf-agent fragments update c84afcfd-1950-42d7-9842-9667d81e7e2a \
  -f content_guide=~/Desktop/updated-guide.md
```

---

### delete

Delete a content fragment. Automatically fetches the ETag.

```bash
cf-agent fragments delete <ID> [--yes]
```

| Option | Description |
|---|---|
| `--yes` | Skip the confirmation prompt |

**Examples:**

```bash
# With confirmation prompt
cf-agent fragments delete c84afcfd-1950-42d7-9842-9667d81e7e2a

# Skip prompt (useful in scripts)
cf-agent fragments delete c84afcfd-1950-42d7-9842-9667d81e7e2a --yes
```

---

### publish

Publish one or more content fragments.

```bash
cf-agent fragments publish <ID> [<ID> ...]
```

**Examples:**

```bash
# Publish one
cf-agent fragments publish c84afcfd-1950-42d7-9842-9667d81e7e2a

# Publish multiple
cf-agent fragments publish \
  c84afcfd-1950-42d7-9842-9667d81e7e2a \
  6e313b3d-f1c4-4896-bebd-ca75495cbd23
```

---

### copy

Copy a content fragment to a new folder.

```bash
cf-agent fragments copy <ID> --destination <PATH> [--deep]
```

| Option | Description |
|---|---|
| `--destination` | Destination DAM folder path (required) |
| `--deep` | Deep copy — includes all referenced fragments |

**Examples:**

```bash
cf-agent fragments copy c84afcfd-1950-42d7-9842-9667d81e7e2a \
  --destination /content/dam/marketplace/content-fragment-resources/archive

cf-agent fragments copy c84afcfd-1950-42d7-9842-9667d81e7e2a \
  --destination /content/dam/marketplace/content-fragment-resources/archive \
  --deep
```

---

### variations

List all variations of a content fragment.

```bash
cf-agent fragments variations <ID> [--json]
```

**Example:**

```bash
cf-agent fragments variations c84afcfd-1950-42d7-9842-9667d81e7e2a
```

---

## Models

### list

List available Content Fragment Models.

```bash
cf-agent models list [--path <PATH>] [--limit <N>] [--json]
```

**Examples:**

```bash
cf-agent models list

cf-agent models list --path /conf/marketplace/settings/dam/cfm/models
```

---

## Diagnostics

### whoami

Inspect the active access token and environment selection.

```bash
cf-agent whoami
```

Useful for debugging 403 errors — checks whether the token's IMS org matches the AEM environment.

---

## Upgrading

Pull the latest version from GitHub:

```bash
pip install --upgrade "git+https://github.com/krishnakumar1990/cf-agent.git"
```
