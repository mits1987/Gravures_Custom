/* KG Desk — hide modules the factory floor doesn't need.

 * Runs after the apps screen renders and hides tiles whose labels are
 * not in the ALLOWED list.  Keeps: Frappe HR (Attendance).
 *
 * This is a UI-only filter — it does not change permissions.
 */

(function () {
	const ALLOWED_HREFS = [
		"frappe-hr",
		"hrms",
		"attendance",
	];

	const ALLOWED_LABELS = [
		"frappe hr",
		"attendance",
		"kreativ attendance",
		"home",
	];

	function hideModules() {
		/* Frappe v16 renders module tiles as <a> links to /desk/... */
		const links = document.querySelectorAll('a[href*="/desk/"]');
		if (!links.length) return;

		links.forEach(function (link) {
			const href = (link.getAttribute("href") || "").toLowerCase();
			const text = (link.textContent || "").trim().toLowerCase();

			/* Keep links whose URL or label matches the allowed list */
			var keep = ALLOWED_HREFS.some(function (h) {
				return href.indexOf(h) !== -1;
			}) || ALLOWED_LABELS.some(function (l) {
				return text.indexOf(l) !== -1;
			});

			if (keep) return;

			/* Hide the link and any sibling text node (module label) */
			link.style.display = "none";

			/* Also hide the parent grid item if present */
			var parent = link.parentElement;
			if (parent && parent.children.length <= 2) {
				parent.style.display = "none";
			}
		});
	}

	/* Run on load */
	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", function () {
			setTimeout(hideModules, 500);
		});
	} else {
		setTimeout(hideModules, 500);
	}

	/* Re-run on SPA navigation */
	frappe.router && frappe.router.on && frappe.router.on("change", function () {
		setTimeout(hideModules, 300);
	});
})();
