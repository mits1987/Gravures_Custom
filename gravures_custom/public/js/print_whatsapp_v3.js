// Adds a "Send to WhatsApp" button to the Print Preview toolbar.
// Opens a clean contact picker modal to select recipient,
// then generates and sends the PDF via WhatsApp.

(function () {
    "use strict";

    const WA_ICON = '<svg viewBox="0 0 448 512" width="16" height="16" fill="white" style="vertical-align: top; margin: 2px 0 0 0;"><path d="M380.9 97.1C339 55.1 283.2 32 223.9 32c-122.4 0-222 99.6-222 222 0 39.1 10.2 77.3 29.6 111L0 480l117.7-30.9c32.4 17.7 68.9 27 106.1 27h.1c122.3 0 224.1-99.6 224.1-222 0-59.3-25.2-115-67.1-157zm-157 341.6c-33.2 0-65.7-8.9-94-25.7l-6.7-4-69.8 18.3L72 359.2l-4.4-7c-18.5-29.4-28.2-63.3-28.2-98.2 0-101.7 82.8-184.5 184.6-184.5 49.3 0 95.6 19.2 130.4 54.1 34.8 34.9 56.2 81.2 56.1 130.5 0 101.8-84.9 184.6-186.6 184.6zm101.2-138.2c-5.5-2.8-32.8-16.2-37.9-18-5.1-1.9-8.8-2.8-12.5 2.8-3.7 5.6-14.3 18-17.6 21.8-3.2 3.7-6.5 4.2-12 1.4-32.6-16.3-54-29.1-75.5-66-5.7-9.8 5.7-9.1 16.3-30.3 1.8-3.7.9-6.9-.5-9.7-1.4-2.8-12.5-30.1-17.1-41.2-4.5-10.8-9.1-9.3-12.5-9.5-3.2-.2-6.9-.2-10.6-.2-3.7 0-9.7 1.4-14.8 6.9-5.1 5.6-19.4 19-19.4 46.3 0 27.3 19.9 53.7 22.6 57.4 2.8 3.7 39.1 59.7 94.8 83.8 35.2 15.2 49 16.5 66.6 13.9 10.7-1.6 32.8-13.4 37.4-26.4 4.6-13 4.6-24.1 3.2-26.4-1.3-2.5-5-3.9-10.5-6.6z"/></svg>';

    /* -----------------------------------------------------------
       Contact Picker Modal
    ----------------------------------------------------------- */
    function showContactPicker(doctype, name, print_format, _lang) {
        var d = new frappe.ui.Dialog({
            title: "Send PDF to WhatsApp",
            size: "large",
            fields: [
                {
                    fieldname: "manual_send_html",
                    fieldtype: "HTML",
                    options: getManualSendHtml(),
                },
                {
                    fieldtype: "Section Break",
                },
                {
                    fieldname: "search_input",
                    fieldtype: "Data",
                    label: "",
                    placeholder: "Search contacts or groups...",
                    onchange: function () {
                        filterPickerList(d);
                    },
                },
                {
                    fieldname: "picker_html",
                    fieldtype: "HTML",
                },
            ],
            primary_action_label: "Cancel",
            primary_action: function () {
                d.hide();
            },
        });
        d.show();

        // Style
        d.$wrapper.find(".modal-dialog").css({
            "max-width": "480px"
        });
        d.$wrapper.find(".modal-content").css({
            "border-radius": "12px",
            border: "none",
            "box-shadow": "0 8px 32px rgba(0,0,0,0.15)"
        });
        d.$wrapper.find(".modal-header").css({
            "border-bottom": "1px solid #f0f0f0",
            padding: "16px 20px"
        });
        d.$wrapper.find(".modal-header .modal-title").css({
            "font-size": "15px",
            "font-weight": "600"
        });
        d.$wrapper.find(".modal-body").css({
            padding: "16px 20px"
        });
        d.$wrapper.find(".modal-footer").css({
            "border-top": "1px solid #f0f0f0",
            padding: "12px 20px"
        });

        // Style search field
        setTimeout(function () {
            var $searchInput = d.$wrapper.find('[data-fieldname="search_input"] input');
            $searchInput.css({
                "border-radius": "8px",
                padding: "10px 14px",
                "font-size": "13px",
                background: "#f5f6f8",
                border: "1px solid #e5e7eb",
                width: "100%",
                outline: "none",
                "box-sizing": "border-box"
            });
            $searchInput.on("focus", function () {
                $(this).css({ background: "#fff", "border-color": "#25D366" });
            });
            $searchInput.on("blur", function () {
                $(this).css({ background: "#f5f6f8", "border-color": "#e5e7eb" });
            });
        }, 50);

        // Wire up the manual send
        setTimeout(function () {
            var $row = d.$wrapper.find('[data-fieldname="manual_send_html"]');
            var $input = $row.find(".wa-manual-input");
            var $btn = $row.find(".wa-manual-btn");

            function doSend() {
                var num = $input.val() || "";
                var clean = num.replace(/[^0-9]/g, "");
                if (!clean || clean.length < 10) {
                    frappe.msgprint("Enter a valid phone with country code (e.g. 919106526195)");
                    $input.focus();
                    return;
                }
                var chatId = clean + "@c.us";
                d.hide();
                sendWithSelectedChat(doctype, name, print_format, _lang, chatId, clean);
            }

            $btn.on("click", doSend);
            $input.on("keydown", function (e) {
                if (e.which === 13) { doSend(); }
            });
            $input.on("focus", function () {
                $(this).css({
                    "border-color": "#25D366",
                    "box-shadow": "0 0 0 3px rgba(37,211,102,0.1)"
                });
            });
            $input.on("blur", function () {
                $(this).css({
                    "border-color": "#d0d5dd",
                    "box-shadow": "none"
                });
            });
            // Focus input
            setTimeout(function () { $input.focus(); }, 200);
        }, 50);

        // Show loading state
        d.fields_dict.picker_html.$wrapper.html(
            '<div style="text-align:center;padding:30px;color:#999;font-size:13px;">Loading chats...</div>'
        );

        // Fetch chats from backend
        frappe.call({
            method: "gravures_custom.overrides.get_whatsapp_chats",
            callback: function (r) {
                if (!r.message) {
                    d.fields_dict.picker_html.$wrapper.html(
                        '<div style="text-align:center;padding:30px;color:#e74c3c;">Failed to load chats.</div>'
                    );
                    return;
                }
                d._picker_data = r.message;
                renderPickerList(d, "", doctype, name, print_format, _lang);
            },
            error: function () {
                d.fields_dict.picker_html.$wrapper.html(
                    '<div style="text-align:center;padding:30px;color:#e74c3c;">Error loading chats.</div>'
                );
            },
        });
    }

    function getManualSendHtml() {
        return (
            '<div style="margin: 0 0 4px 0;">' +
                '<label style="display:block;font-size:12px;color:#555;font-weight:500;margin-bottom:6px;">Phone Number</label>' +
                '<div style="display:flex;gap:8px;align-items:stretch;flex-wrap:nowrap;">' +
                    '<input type="tel" inputmode="numeric" pattern="[0-9]*" placeholder="e.g. 919106526195" class="wa-manual-input" style="' +
                        'flex:1;min-width:0;padding:10px 14px;font-size:14px;border:1px solid #d0d5dd;' +
                        'border-radius:8px;outline:none;transition:all 0.2s;background:#fff;color:#1a1a1a;' +
                        'font-family:inherit;-webkit-appearance:none;"' +
                        ' autocomplete="tel" />' +
                    '<button class="wa-manual-btn" style="' +
                        'padding:10px 20px;background:#25D366;color:white;border:none;border-radius:8px;' +
                        'font-size:14px;font-weight:600;cursor:pointer;transition:all 0.15s;' +
                        'white-space:nowrap;flex-shrink:0;line-height:1;-webkit-appearance:none;"' +
                        '>Send</button>' +
                '</div>' +
                '<p style="margin:4px 0 0;font-size:11px;color:#999;">Include country code, no + or spaces</p>' +
            '</div>'
        );
    }

    /* -----------------------------------------------------------
       Contact list rendering
    ----------------------------------------------------------- */
    function renderPickerList(d, query, doctype, name, print_format, _lang) {
        var data = d._picker_data || { chats: [], groups: [] };
        var q = (query || "").toLowerCase();
        var html = "";

        // Filter
        var chats = (data.chats || []).filter(function (c) {
            if (!q) return true;
            return (
                (c.name || "").toLowerCase().indexOf(q) >= 0 ||
                (c.id || "").indexOf(q) >= 0
            );
        });
        var groups = (data.groups || []).filter(function (g) {
            if (!q) return true;
            return (
                (g.name || "").toLowerCase().indexOf(q) >= 0 ||
                (g.id || "").indexOf(q) >= 0
            );
        });

        if (chats.length === 0 && groups.length === 0) {
            html = '<div style="max-height:340px;overflow-y:auto;"><div style="text-align:center;padding:40px 20px;color:#999;font-size:13px;">No contacts found.</div></div>';
        } else {
            html = '<div style="max-height:340px;overflow-y:auto;">';

            if (chats.length > 0) {
                html += '<div style="padding:8px 0 2px;color:#999;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;">Chats</div>';
                chats.forEach(function (c) {
                    var initials = getInitials(c.name);
                    html +=
                        '<div class="wa-picker-item" data-chat-id="' +
                        frappe.utils.escape_html(c.id) +
                        '" style="display:flex;align-items:center;gap:10px;padding:8px 10px;cursor:pointer;border-radius:8px;transition:background 0.12s;">' +
                        '<div style="width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg,#25D366,#128C7E);color:white;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600;flex-shrink:0;">' +
                        initials +
                        "</div>" +
                        '<div style="flex:1;min-width:0;">' +
                        '<div style="font-weight:500;font-size:13px;color:#1a1a1a;">' +
                        frappe.utils.escape_html(c.name) +
                        "</div>" +
                        "</div>" +
                        "</div>";
                });
            }

            if (groups.length > 0) {
                html += '<div style="padding:8px 0 2px;color:#999;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;">Groups</div>';
                groups.forEach(function (g) {
                    var initials = getInitials(g.name);
                    html +=
                        '<div class="wa-picker-item" data-chat-id="' +
                        frappe.utils.escape_html(g.id) +
                        '" style="display:flex;align-items:center;gap:10px;padding:8px 10px;cursor:pointer;border-radius:8px;transition:background 0.12s;">' +
                        '<div style="width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg,#128C7E,#075E54);color:white;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600;flex-shrink:0;">' +
                        initials +
                        "</div>" +
                        '<div style="flex:1;min-width:0;">' +
                        '<div style="font-weight:500;font-size:13px;color:#1a1a1a;">' +
                        frappe.utils.escape_html(g.name) +
                        "</div>" +
                        "</div>" +
                        "</div>";
                });
            }
            html += "</div>";
        }

        d.fields_dict.picker_html.$wrapper.html(html);

        // Hover + click
        d.fields_dict.picker_html.$wrapper
            .find(".wa-picker-item")
            .on("mouseenter", function () {
                $(this).css("background", "#f0faf4");
            })
            .on("mouseleave", function () {
                $(this).css("background", "");
            })
            .on("click", function () {
                var chatId = $(this).data("chat-id");
                var chatName = $(this).find("div[style*='font-weight:500']").first().text();
                d.hide();
                sendWithSelectedChat(doctype, name, print_format, _lang, chatId, chatName);
            });
    }

    /* -----------------------------------------------------------
       Helpers
    ----------------------------------------------------------- */
    function filterPickerList(d) {
        renderPickerList(d, d.fields_dict.search_input.get_value() || "");
    }

    function getInitials(name) {
        if (!name) return "?";
        var parts = name.trim().split(/\s+/);
        if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
        return name.substring(0, 2).toUpperCase();
    }

    /* -----------------------------------------------------------
       Send PDF to selected chat
    ----------------------------------------------------------- */
    function sendWithSelectedChat(doctype, name, print_format, _lang, chatId, chatName) {
        frappe.show_alert({ message: "Sending PDF to " + chatName + "...", indicator: "blue" });

        frappe.call({
            method: "gravures_custom.overrides.send_print_pdf_whatsapp",
            args: {
                doctype: doctype,
                name: name,
                print_format: print_format || undefined,
                chat_id: chatId,
            },
            callback: function (r) {
                if (r.message && r.message.success) {
                    frappe.show_alert({ message: "Sent PDF to " + chatName + "!", indicator: "green" });
                } else if (r._server_messages) {
                    frappe.msgprint(r._server_messages.join("<br>"));
                } else {
                    frappe.msgprint("Unexpected response from server.");
                }
            },
            error: function (err) {
                frappe.msgprint("Failed: " + (err._message || "Unknown error."));
            },
        });
    }

    /* -----------------------------------------------------------
       Button injection into Print Preview toolbar
    ----------------------------------------------------------- */
    function getDocumentInfo() {
        var route = (window.frappe && frappe.get_route) ? frappe.get_route() : [];
        if (route && route[0] === "print" && route[1] && route[2]) {
            return { doctype: route[1], name: route.slice(2).join("/") };
        }
        return null;
    }

    function getPrintFormat() {
        try {
            var $pf = $("input[data-fieldname='print_format']:visible").first();
            if ($pf.length) return $pf.val() || null;
            var $pfHidden = $("input[data-fieldname='print_format']").first();
            return $pfHidden.length ? $pfHidden.val() || null : null;
        } catch (e) { return null; }
    }

    function getLanguage() {
        try {
            var $l = $("input[data-fieldname='language']:visible").first();
            if ($l.length) return $l.val() || null;
            var $lHidden = $("input[data-fieldname='language']").first();
            return $lHidden.length ? $lHidden.val() || null : null;
        } catch (e) { return null; }
    }

    function hideTryNewMessage() {
        try {
            $(".inner-page-message, a[href*='print-designer']").filter(function () {
                return $(this).text().indexOf("Try the new") >= 0 || ($(this).attr("href") || "").indexOf("print-designer") >= 0;
            }).remove().parent().remove();
        } catch (e) {}
    }

    function injectIntoToolbar() {
        try {
            // Guard: only inject on print preview pages
            if (!isPrintRoute()) return false;
            var $ca = $(".page-actions .custom-actions, .custom-actions");
            if (!$ca || !$ca.length) return false;
            if ($ca.find(".btn-whatsapp-gc").length) return true;

            var $btn = $(
                '<button class="btn btn-sm btn-whatsapp-gc" title="Send PDF to WhatsApp" style="background-color:#25D366;border-color:#25D366;color:white;padding:4px 8px;">' +
                    WA_ICON +
                    "</button>"
            );
            $btn.on("click", function (e) {
                e.preventDefault();
                if (!window.frappe || !frappe.call) {
                    alert("Frappe not yet loaded, please try again.");
                    return;
                }
                var doc = getDocumentInfo();
                if (!doc) {
                    frappe.msgprint(__("Cannot determine document from URL."));
                    return;
                }
                showContactPicker(doc.doctype, doc.name, getPrintFormat(), getLanguage());
            });
            var $refresh = $ca.find("button:contains('Refresh')").last();
            if ($refresh.length) { $refresh.after($btn); }
            else { $ca.prepend($btn); }
            return true;
        } catch (e) { return false; }
    }

    /* -----------------------------------------------------------
       Injection engine — print-page only, persistent interval

       Strategy: keep a single persistent timer that checks every
       600ms whether we're on a print page and whether our button
       is present. If not, inject.  The interval never stops, so
       it catches first navigation AND refresh equally.

       The route guard (`route[0] === "print"`) prevents injection
       on non-print pages (form, list, etc.).
    ----------------------------------------------------------- */

    function isPrintRoute() {
        try {
            var route = frappe.get_route ? frappe.get_route() : [];
            return route[0] === "print";
        } catch (e) { return false; }
    }

    function startInjector() {
        try {
            var injectTimer = null;

            function tryInject() {
                if (!isPrintRoute()) return;
                injectIntoToolbar();
            }

            // Wait for Frappe to be ready, then start the
            // persistent timer and register route handler.
            function onFrappeReady() {
                if (!window.frappe || !frappe.router || !frappe.get_route) {
                    setTimeout(onFrappeReady, 100);
                    return;
                }

                // Persistent check — never stops, catches all scenarios.
                injectTimer = setInterval(tryInject, 600);

                // Route change — try a few times with delays.
                frappe.router.on("change", function () {
                    var delays = [500, 1000, 2000, 4000];
                    for (var i = 0; i < delays.length; i++) {
                        (function (d) { setTimeout(tryInject, d); })(delays[i]);
                    }
                });

                // Try once immediately for good measure.
                setTimeout(tryInject, 400);

                setTimeout(hideTryNewMessage, 200);
                setTimeout(hideTryNewMessage, 600);
                setTimeout(hideTryNewMessage, 1200);
            }

            onFrappeReady();
        } catch (e) {}
    }

    startInjector();
})();
