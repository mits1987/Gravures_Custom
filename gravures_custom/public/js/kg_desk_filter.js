/* KG Desk — hide modules the factory floor doesn't need.

 * Runs after the apps screen renders and hides tiles whose labels are
 * not in the ALLOWED list.  Keeps: Home (not a tile, it's the page
 * itself), Frappe HR (Attendance), and Logout (user menu).
 *
 * This is a UI-only filter — it does not change permissions.  An
 * employee who types /app/accounting in the URL bar still gets there.
 * The point is to keep the home screen clean for people who only ever
 * need Attendance.
 */

(function () {
	const ALLOWED = [
		"frappe hr",
		"attendance",
		"kreativ attendance",
	];

	function hideModules() {
		const tiles = document.querySelectorAll(
			".app-icon-item, .app-item, [data-route*='/app/']"
		);
		if (!tiles.length) return;

		tiles.forEach(function (tile) {
			// Match against visible text label
			const label = (
				tile.querySelector(".app-label, .app-name, .ellipsis") ||
				tile
			)
				.textContent.trim()
				.toLowerCase();

			// Always keep if in allowed list
			if (ALLOWED.some(function (a) { return label.indexOf(a) !== -1; })) {
				return;
			}

			// Hide everything else
			tile.style.display = "none";
		});
	}

	// Run on initial load and after Frappe SPA navigation
	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", function () {
			setTimeout(hideModules, 300);
		});
	} else {
		setTimeout(hideModules, 300);
	}

	// Re-run on route change (Frappe SPA)
	document.addEventListener("click", function () {
		setTimeout(hideModules, 500);
	});
})();
