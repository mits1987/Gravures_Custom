/* KG Desk — hide modules the factory floor doesn't need.

 * Runs after the apps screen renders and hides tiles whose labels are
 * not in the ALLOWED list.  Keeps: Frappe HR (Attendance).
 *
 * Scoped to .layout-main-section only — never touches sidebar links.
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
		/* Only target module tiles in the main content area, NOT sidebar */
		var container = document.querySelector('.layout-main-section');
		if (!container) return;

		var links = container.querySelectorAll('a[href*="/desk/"]');
		if (!links.length) return;

		links.forEach(function (link) {
			var href = (link.getAttribute("href") || "").toLowerCase();
			var text = (link.textContent || "").trim().toLowerCase();

			var keep = ALLOWED_HREFS.some(function (h) {
				return href.indexOf(h) !== -1;
			}) || ALLOWED_LABELS.some(function (l) {
				return text.indexOf(l) !== -1;
			});

			if (keep) return;

			link.style.display = "none";

			var parent = link.parentElement;
			if (parent && parent.children.length <= 2) {
				parent.style.display = "none";
			}
		});
	}

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", function () {
			setTimeout(hideModules, 500);
		});
	} else {
		setTimeout(hideModules, 500);
	}
})();
