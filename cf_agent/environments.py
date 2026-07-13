"""Predefined AEM environment registry and interactive selector.

Validation rules (field types, required, maxLength, regex, enum values,
content-reference roots, long-text mimeTypes) are intentionally NOT defined in
this file. They live in the AEM Content Fragment Models and are fetched live by
the CLI at runtime — AEM is the single source of truth for all validation. See
cf_agent/agent.py:_model_schema_fields.
"""

import click

# Default DAM parent paths keyed by model path. This is a UX convenience (a
# suggested folder for interactive create), not a validation rule.
MODEL_DEFAULTS = {
    "/conf/marketplace/settings/dam/cfm/models/marketplace-connector": "/content/dam/marketplace/content-fragment-resources/connector",
    "/conf/marketplace/settings/dam/cfm/models/marketplace-plugin":    "/content/dam/marketplace/content-fragment-resources/plugin",
}

# ── Environment list ───────────────────────────────────────────────────────────

ENVIRONMENTS = [
    {"label": "PROD",     "url": "https://author-p193006-e2010455.adobeaemcloud.com/adobe/sites"},
    {"label": "STAGE",    "url": "https://author-p193006-e2010299.adobeaemcloud.com/adobe/sites"},
    {"label": "DEV",      "url": "https://author-p193006-e2010379.adobeaemcloud.com/adobe/sites"},
    {"label": "MW-PROD",  "url": "https://author-p180958-e1901212.adobeaemcloud.com/adobe/sites"},
    {"label": "MW-STAGE", "url": "https://author-p180958-e1901213.adobeaemcloud.com/adobe/sites"},
    {"label": "MW-DEV",   "url": "https://author-p180958-e1901357.adobeaemcloud.com/adobe/sites"},
]


def prompt_environment_selection(current_url: str = "") -> str:
    """Numbered selector over ENVIRONMENTS. Returns the chosen URL."""
    click.echo("\nAvailable AEM environments:")
    for i, env in enumerate(ENVIRONMENTS, 1):
        marker = " (current)" if env["url"] == current_url else ""
        click.echo(f"  {i}. {env['label']:<8}  {env['url']}{marker}")

    while True:
        raw = click.prompt(f"\nSelect environment [1-{len(ENVIRONMENTS)}]", default="1")
        try:
            idx = int(raw)
            if 1 <= idx <= len(ENVIRONMENTS):
                chosen = ENVIRONMENTS[idx - 1]
                click.echo(f"Selected: {chosen['label']}  ({chosen['url']})")
                return chosen["url"]
        except ValueError:
            pass
        click.echo(f"Please enter a number between 1 and {len(ENVIRONMENTS)}.")
