// Adds a "Send to WhatsApp" button to the Print Preview toolbar.
// When clicked, opens a contact picker modal to select recipient,
// then generates and sends the PDF via WhatsApp.

(function () {
    "use strict";

    const WA_ICON = '<svg viewBox="0 0 448 512" width="16" height="16" fill="white" style="vertical-align: top; margin: 2px 0 0 0;"><path d="M380.9 97.1C339 55.1 283.2 32 223.9 32c-122.4 0-222 99.6-222 222 0 39.1 10.2 77.3 29.6 111L0 480l117.7-30.9c32.4 17.7 68.9 27 106.1 27h.1c122.3 0 224.1-99.6 224.1-222 0-59.3-25.2-115-67.1-157zm-157 341.6c-33.2 0-65.7-8.9-94-25.7l-6.7-4-69.8 18.3L72 359.2l-4.4-7c-18.5-29.4-28.2-63.3-28.2-98.2 0-101.7 82.8-184.5 184.6-184.5 49.3 0 95.6 19.2 130.4 54.1 34.8 34.9 56.2 81.2 56.1 130.5 0 101.8-84.9 184.6-186.6 184.6zm101.2-138.2c-5.5-2.8-32.8-16.2-37.9-18-5.1-1.9-8.8-2.8-12.5 2.8-3.7 5.6-14.3 18-17.6 21.8-3.2 3.7-6.5 4.2-12 1.4-32.6-16.3-54-29.1-75.5-66-5.7-9.8 5.7-9.1 16.3-30.3 1.8-3.7.9-6.9-.5-9.7-1.4-2.8-12.5-30.1-17.1-41.2-4.5-10.8-9.1-9.3-12.5-9.5-3.2-.2-6.9-.2-10.6-.2-3.7 0-9.7 1.4-14.8 6.9-5.1 5.6-19.4 19-19.4 46.3 0 27.3 19.9 53.7 22.6 57.4 2.8 3.7 39.1 59.7 94.8 83.8 35.2 15.2 49 16.5 66.6 13.9 10.7-1.6 32.8-13.4 37.4-26.4 4.6-13 4.6-24.1 3.2-26.4-1.3-2.5-5-3.9-10.5-6.6z"/></svg>';

    /* -----------------------------------------------------------
       Contact Picker Modal
    ----------------------------------------------------------- */
    function showContactPicker(doctype, name, print_format, _lang) {
        const d = new frappe.ui.Dialog({
            title: "Send PDF to WhatsApp",
            size: "large",
            fields: [
                {
                    fieldname: "search_input",
                    fieldtype: "Data",
                    label: "Search contacts or groups",
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
        d.$wrapper.find(".modal-dialog").css("max-width", "600px");

        // Show loading state
        d.fields_dict.picker_html.$wrapper.html(
            '<div style="text-align:center;padding:30px;color:#888;">Loading chats...</div>'
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

    function renderPickerList(d, query, doctype, name, print_format, _lang) {
        const data = d._picker_data || { chats: [], groups: [] };
        const q = (query || "").toLowerCase();
        let html = "";

        // Filter chats
        const chats = (data.chats || []).filter(function (c) {
            if (!q) return true;
            return (
                (c.name || "").toLowerCase().indexOf(q) >= 0 ||
                (c.id || "").indexOf(q) >= 0 ||
                (c.lastMessage || "").toLowerCase().indexOf(q) >= 0
            );
        });

        // Filter groups
        const groups = (data.groups || []).filter(function (g) {
            if (!q) return true;
            return (
                (g.name || "").toLowerCase().indexOf(q) >= 0 ||
                (g.id || "").indexOf(q) >= 0
            );
        });

        if (chats.length === 0 && groups.length === 0) {
            html = '<div style="text-align:center;padding:30px;color:#888;">No results found.</div>';
        } else {
            // Recent Chats section
            if (chats.length > 0) {
                html += '<div style="font-weight:600;padding:6px 12px;color:#555;font-size:12px;text-transform:uppercase;">Recent Chats</div>';
                chats.forEach(function (c) {
                    const initials = getInitials(c.name);
                    const time = c.timestamp ? formatTimestamp(c.timestamp) : "";
                    const msgPreview = c.lastMessage
                        ? '<div style="font-size:11px;color:#999;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:350px;">' + frappe.utils.escape_html(c.lastMessage) + "</div>"
                        : "";
                    html +=
                        '<div class="wa-picker-item" data-chat-id="' +
                        frappe.utils.escape_html(c.id) +
                        '" style="display:flex;align-items:center;padding:8px 12px;cursor:pointer;border-bottom:1px solid #f0f0f0;transition:background 0.15s;">' +
                        '<div style="width:36px;height:36px;border-radius:50%;background:#25D366;color:white;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:600;flex-shrink:0;">' +
                        initials +
                        "</div>" +
                        '<div style="margin-left:10px;flex:1;min-width:0;">' +
                        '<div style="font-weight:500;font-size:13px;">' +
                        frappe.utils.escape_html(c.name) +
                        "</div>" +
                        msgPreview +
                        "</div>" +
                        (time ? '<div style="font-size:10px;color:#aaa;flex-shrink:0;margin-left:8px;">' + time + "</div>" : "") +
                        "</div>";
                });
            }

            // Groups section
            if (groups.length > 0) {
                html += '<div style="font-weight:600;padding:6px 12px;color:#555;font-size:12px;text-transform:uppercase;margin-top:6px;">Groups</div>';
                groups.forEach(function (g) {
                    const initials = getInitials(g.name);
                    html +=
                        '<div class="wa-picker-item" data-chat-id="' +
                        frappe.utils.escape_html(g.id) +
                        '" style="display:flex;align-items:center;padding:8px 12px;cursor:pointer;border-bottom:1px solid #f0f0f0;transition:background 0.15s;">' +
                        '<div style="width:36px;height:36px;border-radius:50%;background:#128C7E;color:white;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:600;flex-shrink:0;">' +
                        initials +
                        "</div>" +
                        '<div style="margin-left:10px;flex:1;min-width:0;">' +
                        '<div style="font-weight:500;font-size:13px;">' +
                        frappe.utils.escape_html(g.name) +
                        "</div>" +
                        "</div>" +
                        "</div>";
                });
            }
        }

        d.fields_dict.picker_html.$wrapper.html(html);

        // Hover effects
        d.fields_dict.picker_html.$wrapper
            .find(".wa-picker-item")
            .on("mouseenter", function () {
                $(this).css("background", "#f5f5f5");
            })
            .on("mouseleave", function () {
                $(this).css("background", "");
            })
            .on("click", function () {
                const chatId = $(this).data("chat-id");
                const chatName = $(this).find("div[style*='font-weight:500']").first().text();
                d.hide();
                sendWithSelectedChat(doctype, name, print_format, _lang, chatId, chatName);
            });
    }

    function filterPickerList(d) {
        const query = d.fields_dict.search_input.get_value() || "";
        renderPickerList(d, query);
    }

    function getInitials(name) {
        if (!name) return "?";
        const parts = name.trim().split(/\s+/);
        if (parts.length >= 2) {
            return (parts[0][0] + parts[1][0]).toUpperCase();
        }
        return name.substring(0, 2).toUpperCase();
    }

    function formatTimestamp(ts) {
        try {
            const d = new Date(ts * 1000);
            const now = new Date();
            const diffMs = now - d;
            const diffDays = Math.floor(diffMs / 86400000);
            if (diffDays === 0) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
            if (diffDays === 1) return "Yesterday";
            if (diffDays < 7) return d.toLocaleDateString([], { weekday: "short" });
            return d.toLocaleDateString([], { month: "short", day: "numeric" });
        } catch (e) {
            return "";
        }
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
        const route = (window.frappe && frappe.get_route) ? frappe.get_route() : [];
        if (route && route[0] === "print" && route[1] && route[2]) {
            return {
                doctype: route[1],
                name: route.slice(2).join("/"),
            };
        }
        return null;
    }

    function getPrintFormat() {
        try {
            const $pf = $("input[data-fieldname='print_format']:visible").first();
            if ($pf.length) return $pf.val() || null;
            const $pfHidden = $("input[data-fieldname='print_format']").first();
            return $pfHidden.length ? $pfHidden.val() || null : null;
        } catch (e) {
            return null;
        }
    }

    function getLanguage() {
        try {
            const $l = $("input[data-fieldname='language']:visible").first();
            if ($l.length) return $l.val() || null;
            const $lHidden = $("input[data-fieldname='language']").first();
            return $lHidden.length ? $lHidden.val() || null : null;
        } catch (e) {
            return null;
        }
    }

    function hideTryNewMessage() {
        try {
            const $msg = $(".inner-page-message, a[href*='print-designer']").filter(function () {
                return $(this).text().indexOf("Try the new") >= 0 || ($(this).attr("href") || "").indexOf("print-designer") >= 0;
            });
            $msg.remove();
            $msg.parent().remove();
        } catch (e) {}
    }

    function injectIntoToolbar() {
        try {
            const $ca = $(".page-actions .custom-actions, .custom-actions");
            if (!$ca || !$ca.length) return false;
            if ($ca.find(".btn-whatsapp-gc").length) return true;

            const $btn = $(
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
                const doc = getDocumentInfo();
                if (!doc) {
                    frappe.msgprint(__("Cannot determine document from URL."));
                    return;
                }
                showContactPicker(doc.doctype, doc.name, getPrintFormat(), getLanguage());
            });
            const $refresh = $ca.find("button:contains('Refresh')").last();
            if ($refresh.length) {
                $refresh.after($btn);
            } else {
                $ca.prepend($btn);
            }
            return true;
        } catch (e) {
            return false;
        }
    }

    function startInjector() {
        try {
            let tries = 0;
            const poll = () => {
                if (injectIntoToolbar()) return;
                if (++tries < 200) setTimeout(poll, 100);
            };
            if (document.readyState === "loading") {
                document.addEventListener("DOMContentLoaded", poll);
            } else {
                poll();
            }

            const waitForRouter = () => {
                if (window.frappe && frappe.router && typeof frappe.router.on === "function") {
                    frappe.router.on("change", () => {
                        setTimeout(injectIntoToolbar, 100);
                        setTimeout(injectIntoToolbar, 500);
                        setTimeout(injectIntoToolbar, 1500);
                        setTimeout(injectIntoToolbar, 3000);
                        setTimeout(injectIntoToolbar, 5000);
                        setTimeout(hideTryNewMessage, 200);
                        setTimeout(hideTryNewMessage, 600);
                        setTimeout(hideTryNewMessage, 1200);
                    });
                    setTimeout(injectIntoToolbar, 100);
                    setTimeout(hideTryNewMessage, 200);
                } else {
                    setTimeout(waitForRouter, 100);
                }
            };
            waitForRouter();
        } catch (e) {}
    }

    startInjector();
})();
