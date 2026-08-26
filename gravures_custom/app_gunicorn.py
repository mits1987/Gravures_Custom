# Gunicorn wrapper for Frappe with static file middleware
# This module is used by gunicorn in production (--preload mode)
# to ensure static files are served from sites/assets/

from frappe.app import application, application_with_statics

# Apply static file middleware at import time for gunicorn --preload
application = application_with_statics()