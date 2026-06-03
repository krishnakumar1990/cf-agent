"""Predefined AEM environment registry, model schemas, and interactive selector."""

import click

# Default DAM parent paths keyed by model path.
MODEL_DEFAULTS = {
    "/conf/marketplace/settings/dam/cfm/models/marketplace-connector": "/content/dam/marketplace/content-fragment-resources/connector",
    "/conf/marketplace/settings/dam/cfm/models/marketplace-plugin":    "/content/dam/marketplace/content-fragment-resources/plugin",
}

# ── Shared field building blocks ───────────────────────────────────────────────

_SOLUTION_TAGS_VALUES = [
    {"key": "Access Management",         "value": "Access Management"},
    {"key": "Approvals",                 "value": "Approvals"},
    {"key": "Customer Success",          "value": "Customer Success"},
    {"key": "Data Analysis",             "value": "Data Analysis"},
    {"key": "Engineering",               "value": "Engineering"},
    {"key": "Facilities",                "value": "Facilities"},
    {"key": "Finance - Expense Management", "value": "Finance - Expense Management"},
    {"key": "Finance - Other",           "value": "Finance - Other"},
    {"key": "Finance - Payroll",         "value": "Finance - Payroll"},
    {"key": "Finance - Procurement",     "value": "Finance - Procurement"},
    {"key": "General",                   "value": "General"},
    {"key": "HR - Benefits",             "value": "HR - Benefits"},
    {"key": "HR - Employee Records",     "value": "HR - Employee Records"},
    {"key": "HR - Learning & Development", "value": "HR - Learning & Development"},
    {"key": "HR - Onboarding",           "value": "HR - Onboarding"},
    {"key": "HR - Other",                "value": "HR - Other"},
    {"key": "HR - Performance Management", "value": "HR - Performance Management"},
    {"key": "HR - Recruiting & Talent",  "value": "HR - Recruiting & Talent"},
    {"key": "HR - Talent Management",    "value": "HR - Talent Management"},
    {"key": "HR - Time & Absence",       "value": "HR - Time & Absence"},
    {"key": "HR - Workplace Culture",    "value": "HR - Workplace Culture"},
    {"key": "IT",                        "value": "IT"},
    {"key": "Legal",                     "value": "Legal"},
    {"key": "Manager",                   "value": "Manager"},
    {"key": "Marketing",                 "value": "Marketing"},
    {"key": "Product",                   "value": "Product"},
    {"key": "Product Management",        "value": "Product Management"},
    {"key": "Productivity",              "value": "Productivity"},
    {"key": "Project Management",        "value": "Project Management"},
    {"key": "Sales",                     "value": "Sales"},
    {"key": "Support",                   "value": "Support"},
    {"key": "Ticketing",                 "value": "Ticketing"},
    {"key": "Troubleshoot",              "value": "Troubleshoot"},
]

_AVAILABILITY_VALUES = [
    {"key": "IDEA",       "value": "IDEA"},
    {"key": "BUILT IN",   "value": "BUILT_IN"},
    {"key": "VALIDATED",  "value": "VALIDATED"},
    {"key": "INSTALLABLE","value": "INSTALLABLE"},
]

_SHARED_FIELDS = [
    {
        "name": "marketplace_name",
        "label": "Marketplace Title",
        "description": "Must be a non-empty string in Title Case and must not include the system name (e.g., “Look Up an Opportunity”).",
        "required": True, "multiple": False, "type": "text", "maxLength": 255,
        "customValidationRegex": r"^[A-Z0-9][^\s]*(?:\s+[^\s\w]*\s*(?:(?:a|an|the|and|or|but|for|nor|so|yet|at|by|in|of|on|to|up|as|is|if|than|via|per|vs|with|from|into|onto|upon|over|out)\b|[A-Z][^\s]*))*$",
        "customErrorMessage": "Must be in Title Case (e.g. \"Look Up an Opportunity\").",
    },
    {
        "name": "slug",
        "label": "Slug",
        "description": "Unique, kebab-case identifier: system-name + '-' + title-in-kebab-case.",
        "required": True, "multiple": False, "type": "text", "maxLength": 255,
        "customValidationRegex": r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        "customErrorMessage": "Must be lowercase kebab-case only (e.g. my-plugin-name).",
    },
    {
        "name": "description",
        "label": "Description",
        "required": True, "multiple": False, "type": "text", "maxLength": 2000,
        "customValidationRegex": r"^.*\.$",
        "customErrorMessage": "Description must end with a period.",
    },
    {
        "name": "availability",
        "label": "Availability",
        "required": True, "multiple": False, "type": "enumeration",
        "values": _AVAILABILITY_VALUES,
    },
    {
        "name": "solution_tags",
        "label": "Solution Tags",
        "required": True, "multiple": True, "type": "enumeration",
        "values": _SOLUTION_TAGS_VALUES,
    },
]

_VIDEO_FIELD = {
    "name": "video",
    "label": "Video",
    "required": False, "multiple": False, "type": "text", "maxLength": 255,
    "customValidationRegex": r"^https://(www\.)?(vimeo\.com|youtube\.com|youtu\.be|dailymotion\.com|facebook\.com|twitch\.tv|loom\.com)/.+$",
    "customErrorMessage": "Must be a valid video URL (YouTube, Vimeo, Loom, etc.).",
}

_INSTALLATION_UUID_CONNECTOR = {
    "name": "installation_uuid",
    "label": "Installation UUID",
    "required": False, "multiple": False, "type": "text", "maxLength": 255,
    "customValidationRegex": r"^[a-fA-F0-9]{32}$",
    "customErrorMessage": "Enter a valid 32-character hexadecimal UUID (no dashes).",
}

_INSTALLATION_UUID_PLUGIN = {
    "name": "installation_uuid",
    "label": "Installation UUID",
    "required": False, "multiple": False, "type": "text", "maxLength": 255,
    "customValidationRegex": r"^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$",
    "customErrorMessage": "Enter a valid UUID (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx). Required when Availability is INSTALLABLE.",
}

_REVIEW_REQUIRED_FIELD = {
    "name": "reviewRequired",
    "label": "Ready for Review",
    "description": "If selected, the fragment will be sent to review and locked.",
    "required": False, "multiple": True, "type": "enumeration",
    "values": [{"key": "Yes", "value": "true"}],
}

_CONTENT_GUIDE_FIELD = {
    "name": "content_guide",
    "label": "Content Guide",
    "description": "Supports markdown formatting.",
    "required": False, "multiple": False, "type": "long-text",
}

# ── Pre-fetched model schemas ──────────────────────────────────────────────────

MODEL_SCHEMAS = {
    "/conf/marketplace/settings/dam/cfm/models/marketplace-connector": [
        *_SHARED_FIELDS,
        {
            "name": "logo",
            "label": "Logo",
            "required": True, "multiple": False, "type": "content-reference",
            "root": "/content/dam/marketplace/logos",
        },
        _CONTENT_GUIDE_FIELD,
        {
            "name": "product_family",
            "label": "Product Family",
            "required": False, "multiple": False, "type": "enumeration",
            "values": [
                {"key": "Google Cloud",        "value": "google-cloud"},
                {"key": "Google Workspace",    "value": "google-workspace"},
                {"key": "Microsoft Graph",     "value": "microsoft-graph"},
                {"key": "Microsoft Azure",     "value": "azure"},
                {"key": "Atlassian Cloud",     "value": "atlassian-cloud"},
                {"key": "Freshworks",          "value": "freshworks"},
                {"key": "SAP",                 "value": "sap"},
                {"key": "Oracle Fusion Cloud", "value": "oracle-fusion-cloud"},
                {"key": "SailPoint",           "value": "sailpoint"},
                {"key": "BMC Helix",           "value": "bmc-helix"},
                {"key": "Azure Function App",  "value": "azure-function-app"},
            ],
        },
        _VIDEO_FIELD,
        _INSTALLATION_UUID_CONNECTOR,
        _REVIEW_REQUIRED_FIELD,
    ],

    "/conf/marketplace/settings/dam/cfm/models/marketplace-plugin": [
        *_SHARED_FIELDS,
        {
            "name": "purple_chat_link",
            "label": "Purple Chat Link",
            "required": True, "multiple": False, "type": "text", "maxLength": 12000,
            "customValidationRegex": r"^https://marketplace\.moveworks\.com/purple-chat\?conversation=.*$",
            "customErrorMessage": "Must be a valid Purple Chat URL: https://marketplace.moveworks.com/purple-chat?conversation=...",
        },
        {
            "name": "systems",
            "label": "Systems",
            "required": True, "multiple": True, "type": "enumeration",
            "values": [
                {"key": "8x8", "value": "8x8"}, {"key": "adp", "value": "adp"},
                {"key": "asana", "value": "asana"}, {"key": "atlan", "value": "atlan"},
                {"key": "awardco", "value": "awardco"}, {"key": "axero", "value": "axero"},
                {"key": "beyondtrust", "value": "beyondtrust"}, {"key": "bmc-helix", "value": "bmc-helix"},
                {"key": "bmc-helix-digitalworkplace", "value": "bmc-helix-digitalworkplace"},
                {"key": "box", "value": "box"}, {"key": "bravo", "value": "bravo"},
                {"key": "brightspot", "value": "brightspot"}, {"key": "cherwell", "value": "cherwell"},
                {"key": "clari", "value": "clari"}, {"key": "comaround", "value": "comaround"},
                {"key": "confluence-cloud", "value": "confluence-cloud"}, {"key": "confluence-dc", "value": "confluence-dc"},
                {"key": "coupa", "value": "coupa"}, {"key": "databricks", "value": "databricks"},
                {"key": "datadog", "value": "datadog"}, {"key": "dayforce", "value": "dayforce"},
                {"key": "docusign", "value": "docusign"}, {"key": "dovetail", "value": "dovetail"},
                {"key": "dropbox", "value": "dropbox"}, {"key": "duo", "value": "duo"},
                {"key": "freshdesk", "value": "freshdesk"}, {"key": "freshservice", "value": "freshservice"},
                {"key": "gainsight", "value": "gainsight"}, {"key": "github", "value": "github"},
                {"key": "glia", "value": "glia"}, {"key": "gong", "value": "gong"},
                {"key": "google-calendar", "value": "google-calendar"}, {"key": "google-drive", "value": "google-drive"},
                {"key": "greenhouse", "value": "greenhouse"}, {"key": "guru", "value": "guru"},
                {"key": "hibob", "value": "hibob"}, {"key": "highspot", "value": "highspot"},
                {"key": "hubspot", "value": "hubspot"}, {"key": "igloo", "value": "igloo"},
                {"key": "interact", "value": "interact"}, {"key": "ivanti-service-desk", "value": "ivanti-service-desk"},
                {"key": "jamf", "value": "jamf"}, {"key": "jira", "value": "jira"},
                {"key": "jive", "value": "jive"}, {"key": "jll-technologies", "value": "jll-technologies"},
                {"key": "lattice", "value": "lattice"}, {"key": "launchdarkly", "value": "launchdarkly"},
                {"key": "linear", "value": "linear"}, {"key": "lumapps", "value": "lumapps"},
                {"key": "manage-engine", "value": "manage-engine"}, {"key": "marketo", "value": "marketo"},
                {"key": "microsoft-entra", "value": "microsoft-entra"}, {"key": "microsoft-graph", "value": "microsoft-graph"},
                {"key": "microsoft-intune", "value": "microsoft-intune"}, {"key": "microsoft-power-automate", "value": "microsoft-power-automate"},
                {"key": "microsoft-sharepoint", "value": "microsoft-sharepoint"}, {"key": "mindtickle", "value": "mindtickle"},
                {"key": "moveworks", "value": "moveworks"}, {"key": "netsuite", "value": "netsuite"},
                {"key": "nexthink", "value": "nexthink"}, {"key": "notion", "value": "notion"},
                {"key": "officespace", "value": "officespace"}, {"key": "okta", "value": "okta"},
                {"key": "oneidentity", "value": "oneidentity"}, {"key": "oracle-erp", "value": "oracle-erp"},
                {"key": "oracle-hcm", "value": "oracle-hcm"}, {"key": "outlook", "value": "outlook"},
                {"key": "pagerduty", "value": "pagerduty"}, {"key": "palo-alto-networks", "value": "palo-alto-networks"},
                {"key": "pendo", "value": "pendo"}, {"key": "perplexity", "value": "perplexity"},
                {"key": "pingid", "value": "pingid"}, {"key": "polygon-io", "value": "polygon-io"},
                {"key": "promapp", "value": "promapp"}, {"key": "quip", "value": "quip"},
                {"key": "rightanswers", "value": "rightanswers"}, {"key": "sailpoint-iiq", "value": "sailpoint-iiq"},
                {"key": "sailpoint-inow", "value": "sailpoint-inow"}, {"key": "salesforce", "value": "salesforce"},
                {"key": "sap-ariba", "value": "sap-ariba"}, {"key": "sap-concur", "value": "sap-concur"},
                {"key": "sap-success-factors", "value": "sap-success-factors"}, {"key": "sap-work-zone", "value": "sap-work-zone"},
                {"key": "screensteps", "value": "screensteps"}, {"key": "search-unify", "value": "search-unify"},
                {"key": "servicenow", "value": "servicenow"}, {"key": "simpplr", "value": "simpplr"},
                {"key": "slack", "value": "slack"}, {"key": "slite", "value": "slite"},
                {"key": "smartsheet", "value": "smartsheet"}, {"key": "snowflake", "value": "snowflake"},
                {"key": "solarwinds", "value": "solarwinds"}, {"key": "spaceiq", "value": "spaceiq"},
                {"key": "squiz-intranet", "value": "squiz-intranet"}, {"key": "stackoverflow", "value": "stackoverflow"},
                {"key": "staffbase", "value": "staffbase"}, {"key": "stripe", "value": "stripe"},
                {"key": "sysaid", "value": "sysaid"}, {"key": "tableau", "value": "tableau"},
                {"key": "twelve-data", "value": "twelve-data"}, {"key": "ukg", "value": "ukg"},
                {"key": "unily", "value": "unily"}, {"key": "vayusphere", "value": "vayusphere"},
                {"key": "wiki-js", "value": "wiki-js"}, {"key": "wolken", "value": "wolken"},
                {"key": "wordpress", "value": "wordpress"}, {"key": "workday", "value": "workday"},
                {"key": "zendesk", "value": "zendesk"}, {"key": "zerocater", "value": "zerocater"},
                {"key": "zoom", "value": "zoom"},
            ],
        },
        _CONTENT_GUIDE_FIELD,
        {
            "name": "agent_capabilities",
            "label": "Agent Capabilities",
            "required": False, "multiple": True, "type": "enumeration",
            "values": [
                {"key": "Ambient Agent",               "value": "Ambient Agent"},
                {"key": "User Consent Authentication", "value": "User Consent Authentication"},
                {"key": "Structured Data Analyzer",    "value": "Structured Data Analyzer"},
            ],
        },
        _VIDEO_FIELD,
        _INSTALLATION_UUID_PLUGIN,
        _REVIEW_REQUIRED_FIELD,
    ],
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
