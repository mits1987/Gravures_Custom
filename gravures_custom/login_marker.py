"""
Site environment marker for the login page — static banner HTML.

Goal
----
When users access a non-production site (e.g. kreativ316, krunal_test,
ad-hoc dev clones) we show a loud, unmistakable "TESTING" banner fixed
at the top of the page so they don't accidentally push experimental
changes to customers.

Why this approach
-----------------
The banner is built via string concatenation in Python, then injected
into `head_html` (`{{ head_html or "" }}` in head.html). Login.html
does NOT override the `head` block so this renders correctly.

The template string uses NO Jinja double-braces at all — all
substitutions use Python .replace() markers (COLOR_REPLACE etc.)
to avoid Jinja interpretation.

The <script> tag includes data-cfasync="false" which tells
Cloudflare Rocket Loader to skip it.

Why it survives backup/restore
------------------------------
Detection keys off frappe.conf.get("environment") (set in site_config.json).
No DB row, no Single DocType, no System Settings flag.
Nothing to overwrite on restore.

Tweaking
--------
Set `environment: "production"` in site_config.json for prod sites.
Any site without this (or with environment != "production") gets the red banner.
"""

import frappe


# Base colour for the banner.
COLOR = "#dc3545"


def make_banner(site, label, color):
    """Build banner HTML using pure string ops — no Jinja-brace pitfalls."""

    css = (
        '<style id="gc-env-marker-style">'
        + ".gc-env-marker{"
        + "position:fixed;top:0;left:0;right:0;z-index:99999;"
        + "background:" + color + ";"
        + "color:#fff;"
        + "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;"
        + "font-weight:700;font-size:14px;letter-spacing:.08em;text-align:center;"
        + "padding:8px 12px;"
        + "box-shadow:0 2px 6px rgba(0,0,0,.25);"
        + "animation:gc-env-pulse 1.6s ease-in-out infinite;"
        + "}"
        + ".gc-env-marker__badge{"
        + "background:#fff;color:" + color + ";"
        + "padding:2px 8px;border-radius:4px;margin:0 6px;font-weight:800;"
        + "}"
        + ".gc-env-marker__site{"
        + "font-weight:500;font-size:12px;opacity:.85;margin-left:8px;"
        + "}"
        + "body.has-gc-env-marker .page-card{margin-top:56px;}"
        + "@keyframes gc-env-pulse{"
        + "0%,100%{background:" + color + ";}"
        + "50%{background:#b02a37;}"
        + "}"
        + "</style>"
    )

    div = (
        '<div class="gc-env-marker" role="alert">'
        + '&#x26A0;&#xFE0F;'
        + ' <span class="gc-env-marker__badge">' + label + '</span>'
        + ' Site: ' + site + ' &#x2014; changes here are NOT production data.'
        + '</div>'
    )

    js = (
        '<script data-cfasync="false">'
        + '(function(){'
        + 'var d=document;'
        + "if(d.querySelector('.gc-env-marker')&&d.getElementById('gc-env-marker-style'))return;"
        + "var s=d.createElement('style');s.id='gc-env-marker-style';s.textContent='"
        + ".gc-env-marker{position:fixed;top:0;left:0;right:0;z-index:99999;"
        + "background:" + color + ";color:#fff;"
        + "font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Roboto,sans-serif;"
        + "font-weight:700;font-size:14px;letter-spacing:.08em;text-align:center;"
        + "padding:8px 12px;box-shadow:0 2px 6px rgba(0,0,0,.25);"
        + "animation:gc-env-pulse 1.6s ease-in-out infinite;}"
        + ".gc-env-marker__badge{background:#fff;color:" + color + ";"
        + "padding:2px 8px;border-radius:4px;margin:0 6px;font-weight:800;}"
        + ".gc-env-marker__site{font-weight:500;font-size:12px;opacity:.85;margin-left:8px;}"
        + "body.has-gc-env-marker .page-card{margin-top:56px;}"
        + "@keyframes gc-env-pulse{0%,100%{background:" + color + ";}50%{background:#b02a37;}}"
        + "';"
        + "if(!d.getElementById('gc-env-marker-style'))d.head.appendChild(s);"
        + 'var b=d.createElement("div");b.className="gc-env-marker";b.setAttribute("role","alert");'
        + 'b.innerHTML="&#x26A0;&#xFE0F; <span class=\\"gc-env-marker__badge\\">' + label + '</span>'
        + ' Site: ' + site + ' &#x2014; changes here are NOT production data.";'
        + 'd.body.appendChild(b);d.body.classList.add("has-gc-env-marker");'
        + "if(d.title&&d.title.indexOf('" + label + "')!==0){"
        + "d.title='" + label + ' &#x2013; ' + "'+d.title;}"
        + "console.warn('%c[GC ENV] %cYou are on site "+site
        + ". Do not push this data to customers.',"
        + "'background:#dc3545;color:#fff;padding:2px 6px;border-radius:3px;font-weight:bold',"
        + "'color:#dc3545;font-weight:bold');"
        + '})();'
        + '</script>'
    )

    return css + div + js


def update_website_context(context):
    """Frappe hook: runs for every web request, including /login.

    For non-prod sites we inject the full banner HTML as a raw string
    into the `head_html` context variable. On prod sites we do nothing.
    """
    try:
        site = frappe.local.site
    except Exception:
        return context

    # Production check: site_config.json -> environment: "production"
    if frappe.conf.get("environment") == "production":
        return context

    banner = make_banner(site=site, label="TESTING", color=COLOR)

    existing = context.get("head_html") or ""
    if "gc-env-marker" not in existing:
        context["head_html"] = existing + banner

    return context