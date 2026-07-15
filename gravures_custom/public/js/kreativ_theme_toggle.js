// ─── Kreativ Theme — Adds "Kreativ Brand" to the native Theme Switcher ───
(function () {
    "use strict";

    if (window._kreativThemePatched) return;
    window._kreativThemePatched = true;

    const THEME = {
        name: "kreativ",
        label: "Kreativ Brand",
        info: "Kreativ brand colors — navy sidebar, rounded cards, refined UI",
    };

    function getCurrentTheme() {
        return document.documentElement.getAttribute("data-theme-mode") || "light";
    }

    // ── Strategy 1: Monkey-patch the prototype ──
    function patchPrototype() {
        try {
            if (!frappe.ui || !frappe.ui.ThemeSwitcher) return false;

            var origFetch = frappe.ui.ThemeSwitcher.prototype.fetch_themes;
            frappe.ui.ThemeSwitcher.prototype.fetch_themes = function () {
                return new Promise(function (resolve) {
                    // Check if kreativ is already in themes to avoid duplicates
                    var hasKreativ = (this.themes || []).some(function (t) {
                        return t.name === "kreativ";
                    });
                    if (hasKreativ) {
                        resolve(this.themes);
                        return;
                    }
                    // Call original first to get the base themes
                    origFetch.call(this).then(function (baseThemes) {
                        // Insert kreativ before "automatic"
                        var autoIdx = baseThemes.findIndex(function (t) {
                            return t.name === "automatic";
                        });
                        if (autoIdx > -1) {
                            baseThemes.splice(autoIdx, 0, THEME);
                        } else {
                            baseThemes.push(THEME);
                        }
                        this.themes = baseThemes;
                        resolve(this.themes);
                    }.bind(this));
                }.bind(this));
            };

            // Patch toggle_theme to handle kreativ
            frappe.ui.ThemeSwitcher.prototype.toggle_theme = function (theme) {
                this.current_theme = theme.toLowerCase();
                document.documentElement.setAttribute("data-theme-mode", this.current_theme);

                if (theme.toLowerCase() === "kreativ") {
                    document.documentElement.setAttribute("data-theme", "light");
                } else if (theme.toLowerCase() === "automatic") {
                    var isDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
                    document.documentElement.setAttribute("data-theme", isDark ? "dark" : "light");
                } else {
                    document.documentElement.setAttribute("data-theme", theme.toLowerCase());
                }

                frappe.xcall("frappe.core.doctype.user.user.switch_theme", {
                    theme: theme.charAt(0).toUpperCase() + theme.slice(1),
                }).catch(function () {});

                frappe.show_alert("Theme Changed", 3);
            };

            // Fix set_theme so kreativ uses light variables
            frappe.ui.set_theme = function (theme) {
                var root = document.documentElement;
                var mode = root.getAttribute("data-theme-mode");
                if (!theme) {
                    switch (mode) {
                        case "automatic":
                            theme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
                            break;
                        case "kreativ":
                            theme = "light";
                            break;
                        default:
                            theme = mode;
                    }
                }
                root.setAttribute("data-theme", theme || mode || "light");
            };

            // Handle page load
            if (getCurrentTheme() === "kreativ") {
                document.documentElement.setAttribute("data-theme", "light");
            }

            return true;
        } catch (e) {
            console.warn("Kreativ theme: prototype patch failed", e);
            return false;
        }
    }

    // ── Strategy 2: DOM injection fallback ──
    // Detects the theme switcher dialog and injects the Kreativ option
    function setupDOMInjector() {
        var observer = new MutationObserver(function (mutations) {
            for (var m = 0; m < mutations.length; m++) {
                for (var i = 0; i < mutations[m].addedNodes.length; i++) {
                    var node = mutations[m].addedNodes[i];
                    if (node.nodeType !== 1) continue;
                    var dialog = node.classList && node.classList.contains("frappe-dialog")
                        ? node : node.querySelector && node.querySelector(".frappe-dialog");
                    if (!dialog) continue;
                    // Check if this is the theme switcher dialog
                    var titleEl = dialog.querySelector && dialog.querySelector(".modal-title");
                    if (!titleEl || (titleEl.textContent || "").trim() !== "Switch Theme") continue;

                    // Found the theme switcher dialog - inject the Kreativ option if not present
                    var themeGrid = dialog.querySelector(".theme-grid");
                    if (!themeGrid) continue;

                    var existingKreativ = themeGrid.querySelector('[data-theme="kreativ"]');
                    if (existingKreativ) continue;

                    // Create the kreativ theme card
                    var isSelected = getCurrentTheme() === "kreativ";
                    var kreativHtml = document.createElement("div");
                    kreativHtml.className = isSelected ? "selected" : "";
                    kreativHtml.innerHTML =
                        '<div data-theme="kreativ" data-is-auto-theme="false" title="' +
                        THEME.info +
                        '">' +
                        '<div class="background">' +
                        "<div>" +
                        '<div class="preview-check" data-theme="kreativ">' +
                        frappe.utils.icon("tick", "xs") +
                        "</div></div>" +
                        '<div class="navbar"></div>' +
                        '<div class="p-2">' +
                        '<div class="toolbar"><span class="text"></span><span class="primary"></span></div>' +
                        '<div class="foreground"></div>' +
                        '<div class="foreground"></div>' +
                        "</div></div></div>" +
                        '<div class="mt-3 text-center">' +
                        '<h5 class="theme-title">' + THEME.label + "</h5></div>";

                    // Insert before the "Automatic" card
                    var autoCard = themeGrid.querySelector('[data-theme="automatic"]');
                    if (autoCard && autoCard.parentElement) {
                        autoCard.parentElement.parentElement.insertBefore(
                            kreativHtml,
                            autoCard.parentElement
                        );
                    } else {
                        themeGrid.appendChild(kreativHtml);
                    }

                    // Add click handler
                    kreativHtml.addEventListener("click", function () {
                        if (getCurrentTheme() === "kreativ") return;
                        // Deselect all
                        var allCards = themeGrid.querySelectorAll(".theme-grid > div");
                        allCards.forEach(function (c) { c.classList.remove("selected"); });
                        kreativHtml.classList.add("selected");
                        // Apply theme
                        document.documentElement.setAttribute("data-theme-mode", "kreativ");
                        document.documentElement.setAttribute("data-theme", "light");
                        frappe.xcall("frappe.core.doctype.user.user.switch_theme", {
                            theme: "Kreativ",
                        }).catch(function () {});
                        frappe.show_alert("Theme Changed", 3);
                    });
                }
            }
        });

        observer.observe(document.body, {
            childList: true,
            subtree: true,
        });
    }

    // ── Initialize ──
    function init() {
        if (window.frappe && frappe.ui) {
            patchPrototype();
            setupDOMInjector();
        } else {
            var checkFrappe = setInterval(function () {
                if (window.frappe && frappe.ui) {
                    clearInterval(checkFrappe);
                    patchPrototype();
                    setupDOMInjector();
                }
            }, 100);
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
