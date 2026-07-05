app_name = "gravures_custom"
app_title = "Gravures Custom"
app_publisher = "Mitesh"
app_description = "GC"
app_email = "info@kreativ.com"
app_license = "license"

override_whitelisted_methods = {
    "frappe.utils.print_format.download_pdf": "gravures_custom.overrides.download_pdf"
}

fixtures = [
    {
        "dt": "Custom Field",
        "filters": [["module", "=", "Gravures Custom"]]
    },
    {
        "dt": "Client Script",
        "filters": [["module", "=", "Gravures Custom"]]
    },
    {
        "dt": "Server Script",
        "filters": [["module", "=", "Gravures Custom"]]
    },
    {
        "dt": "Property Setter",
        "filters": [["module", "=", "Gravures Custom"]]
    },
    {
        "dt": "DocType",
        "filters": [["module", "=", "Gravures Custom"]]
    },
    {
        "dt": "DocType",
        "filters": [["module", "=", "Gravures Custom"]]
    },
]

# doctype_js = {
#    "Delivery Note": "public/js/delivery_note.js"
# }

# doc_events = {
#    "Sales Order": {
#        "onload": "gravures_custom.permissions.log_permission_check"
#    }
#   }


# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "gravures_custom",
# 		"logo": "/assets/gravures_custom/logo.png",
# 		"title": "Gravures Custom",
# 		"route": "/gravures_custom",
# 		"has_permission": "gravures_custom.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/gravures_custom/css/gravures_custom.css"
# app_include_js = "/assets/gravures_custom/js/gravures_custom.js"

# Login page environment marker: injects a "TESTING" banner on non-prod sites.
# Detection runs in gravures_custom.login_marker.update_website_context which
# keys off frappe.local.site (no DB state, restore-safe).
update_website_context = [
	"gravures_custom.login_marker.update_website_context",
]

# include js, css files in header of web template
# web_include_css = "/assets/gravures_custom/css/gravures_custom.css"
# web_include_js = "/assets/gravures_custom/js/gravures_custom.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "gravures_custom/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
doctype_list_js = {"Employee Shift" : "public/js/employee_shift_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "gravures_custom/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "gravures_custom.utils.jinja_methods",
# 	"filters": "gravures_custom.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "gravures_custom.install.before_install"
# after_install = "gravures_custom.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "gravures_custom.uninstall.before_uninstall"
# after_uninstall = "gravures_custom.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "gravures_custom.utils.before_app_install"
# after_app_install = "gravures_custom.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "gravures_custom.utils.before_app_uninstall"
# after_app_uninstall = "gravures_custom.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "gravures_custom.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Employee Checkin": {
		# on_change fires on BOTH insert (new document) and update (existing doc modified).
		# Use on_change so creating a new checkin for an employee will trigger recalc
		# without needing the user to also save again.
		"on_change": "gravures_custom.attendance.hooks.on_checkin_updated"
	},
	"Salary Slip": {
		# on_submit fires when a Salary Slip is finalized.
		# This marks the employee-month's Employee Shift records as locked
		# (creates an Employee Shift Lock so future corrections require unlock).
		"on_submit": "gravures_custom.attendance.hooks.on_salary_slip_submit"
	}
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"gravures_custom.tasks.all"
# 	],
# 	"daily": [
# 		"gravures_custom.tasks.daily"
# 	],
# 	"hourly": [
# 		"gravures_custom.tasks.hourly"
# 	],
# 	"weekly": [
# 		"gravures_custom.tasks.weekly"
# 	],
# 	"monthly": [
# 		"gravures_custom.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "gravures_custom.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "gravures_custom.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "gravures_custom.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["gravures_custom.utils.before_request"]
# after_request = ["gravures_custom.utils.after_request"]

# Job Events
# ----------
# before_job = ["gravures_custom.utils.before_job"]
# after_job = ["gravures_custom.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"gravures_custom.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }


#doc_events = {
#    "Email Queue": {
#        "before_insert": "gravures_custom.overrides.email_queue.after_insert_email"
#    }
#}


