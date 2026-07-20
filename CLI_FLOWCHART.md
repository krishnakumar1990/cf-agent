# cf-agent — Complete Flow

A single flowchart covering every scenario: authentication, command dispatch, **create** (connector/plugin, interactive & one-liner), **update** (by id / slug / interactive picker, review-lock, editable-field scoping), validation, and the final write. Rendered with [Mermaid](https://mermaid.js.org) (native in VSCode + GitHub).

```mermaid
flowchart TD
  %% ============ ENTRY & AUTH ============
  Start(["cf-agent COMMAND"]) --> Auth{"Logged in?<br/>(valid token)"}
  Auth -->|no| Login["cf-agent login<br/>browser OAuth · Adobe IMS"]
  Login --> EnvSel["cf-agent env select<br/>PROD / STAGE / DEV"]
  Auth -->|yes| Dispatch{"Which command?"}
  EnvSel --> Dispatch

  Dispatch -->|"list · get · publish<br/>delete · copy · variations"| Simple["Run on active env"] --> SimpleEnd([Done])
  Dispatch -->|create| C_mode
  Dispatch -->|update| U_id

  %% ============ CREATE ============
  subgraph CREATE["fragments create"]
    direction TB
    C_mode{"-i interactive<br/>or -f one-liner?"}
    C_mode -->|"-i"| C_model["Pick model from list<br/>(folder auto-filled)"]
    C_mode -->|"-f"| C_args["--model-path + -f name=value"]
    C_model --> C_which{"Connector<br/>or Plugin?"}
    C_which -->|Connector| C_conn["Prompt: name=slug · description ·<br/>logo (file name only) · solution_tags ·<br/>product_family · content_guide · video"]
    C_which -->|Plugin| C_plug["Prompt systems FIRST → seed slug 'system-'<br/>name · description · availability ·<br/>solution_tags · purple_chat_link ·<br/>agent_capabilities · video · content_guide<br/>installation_asset_uuid ONLY if INSTALLABLE"]
  end

  C_conn --> V_field
  C_plug --> V_field
  C_args --> V_field

  %% ============ VALIDATION (create = all required) ============
  V_field{"Field rules OK?<br/>required · enum · maxLength · regex ·<br/>Title Case · asset exists ·<br/>content-guide DAM image refs"}
  V_field -->|no| V_err["Show error<br/>(-i: re-prompt · -f: exit)"]
  V_field -->|yes| V_cross{"Cross-field OK?<br/>INSTALLABLE needs uuid ·<br/>VALIDATED/IDEA/BUILT_IN forbid uuid ·<br/>plugin slug starts with a system"}
  V_cross -->|no| V_err
  V_cross -->|yes| V_dup{"Slug unique<br/>in folder?"}
  V_dup -->|no| V_err
  V_dup -->|yes| C_post[["POST /cf/fragments"]]
  C_post --> C_done([Created])

  %% ============ UPDATE ============
  subgraph UPDATE["fragments update"]
    direction TB
    U_id{"Identify fragment"}
    U_id -->|"by id"| U_fetch
    U_id -->|"--slug + --model-path"| U_resolve["Resolve id<br/>(1 targeted call)"] --> U_fetch
    U_id -->|"-i"| U_pick["Pick model → filter by name/slug<br/>exact slug = 1 call, else folder scan<br/>→ choose from numbered list"] --> U_fetch
    U_fetch[["GET fragment + ETag"]]
  end

  U_fetch --> U_lock{"reviewRequired<br/>= true?"}
  U_lock -->|yes| U_locked[["STOP — in review &amp; locked<br/>(fail fast, before any prompt)<br/>release review in AEM to edit"]]
  U_lock -->|no| U_mode{"-i or -f?"}
  U_mode -->|"-i"| U_loop["Edit loop — EDITABLE fields only<br/>current value shown · Enter = keep<br/>same prompts as create · inline validate"]
  U_mode -->|"-f"| U_args["-f name=value edits"]
  U_loop --> U_scope
  U_args --> U_scope{"Field allowed?"}
  U_scope -->|"slug · systems · availability"| U_imm[["ERROR: immutable —<br/>cannot change after creation"]]
  U_scope -->|"not in editable set"| U_noedit[["ERROR: not editable on update"]]
  U_scope -->|"editable"| U_val{"Validate + cross-field<br/>on MERGED state OK?"}
  U_val -->|no| U_verr["Error (-i: re-prompt)"]
  U_val -->|yes| U_patch[["PATCH /cf/fragments/:id<br/>If-Match: ETag"]]
  U_patch --> U_done([Updated])
```

## Editable fields on update (the `Field allowed?` gate)

| Model | Editable | Locked (immutable) |
|---|---|---|
| **Connector** | `logo`, `content_guide` | `slug` |
| **Plugin** | `marketplace_name`, `description`, `purple_chat_link`, `solution_tags`, `installation_asset_uuid`, `content_guide` | `slug`, `systems`, `availability` |

Everything not listed as editable is rejected on update.

## Legend

| Shape | Meaning |
|---|---|
| `([ ])` | Start / end |
| `{ }` | Decision (a gate) |
| `[[ ]]` | AEM API call, or a hard stop / error |
| `[ ]` | Step / prompt |

**Key behaviors encoded:** live-model validation · plugin systems-first slug seeding · conditional `installation_asset_uuid` · logo filename auto-prefix · duplicate-slug guard · UUID-less fragment selection (id / slug / interactive filter) · review-lock fail-fast · journey-scoped editable fields · merged-state cross-field check on update.
