// Adds a "Send to WhatsApp" button to the Print Preview toolbar.
// Hooks PrintView via DOM polling and route-change events.

(function () {
    "use strict";


        const WA_SVG = '<svg viewBox="0 0 448 512" width="14" height="14" fill="currentColor"><path d="M380.9 97.1C339 55.1 283.2 32 223.9 32c-122.4 0-222 99.6-222 222 0 39.1 10.2 77.3 29.6 111L0 480l117.7-30.9c32.4 17.7 68.9 27 106.1 27h.1c122.3 0 224.1-99.6 224.1-222 0-59.3-25.2-115-67.1-157zm-157 341.6c-33.2 0-65.7-8.9-94-25.7l-6.7-4-69.8 18.3L72 359.2l-4.4-7c-18.5-29.4-28.2-63.3-28.2-98.2 0-101.7 82.8-184.5 184.6-184.5 49.3 0 95.6 19.2 130.4 54.1 34.8 34.9 56.2 81.2 56.1 130.5 0 101.8-84.9 184.6-186.6 184.6zm101.2-138.2c-5.5-2.8-32.8-16.2-37.9-18-5.1-1.9-8.8-2.8-12.5 2.8-3.7 5.6-14.3 18-17.6 21.8-3.2 3.7-6.5 4.2-12 1.4-32.6-16.3-54-29.1-75.5-66-5.7-9.8 5.7-9.1 16.3-30.3 1.8-3.7.9-6.9-.5-9.7-1.4-2.8-12.5-30.1-17.1-41.2-4.5-10.8-9.1-9.3-12.5-9.5-3.2-.2-6.9-.2-10.6-.2-3.7 0-9.7 1.4-14.8 6.9-5.1 5.6-19.4 19-19.4 46.3 0 27.3 19.9 53.7 22.6 57.4 2.8 3.7 39.1 59.7 94.8 83.8 35.2 15.2 49 16.5 66.6 13.9 10.7-1.6 32.8-13.4 37.4-26.4 4.6-13 4.6-24.1 3.2-26.4-1.3-2.5-5-3.9-10.5-6.6z"/></svg>';

        function sendToWhatsApp(btn) {
            try {
                const route = (window.frappe && frappe.get_route) ? frappe.get_route() : [];
                let doctype = null, name = null;
                if (route && route[0] === "print" && route[1] && route[2]) {
                    doctype = route[1];
                    name = route.slice(2).join("/");
                }
                if (!doctype || !name) {
                    frappe.msgprint(__("Cannot determine document from URL."));
                    return;
                }
                let print_format = null, _lang = null;
                try {
                    // Prefer the visible sidebar input; fall back to hidden one
                    const $pf = $("input[data-fieldname='print_format']:visible").first();
                    if (!$pf.length) {
                        const $pfHidden = $("input[data-fieldname='print_format']").first();
                        if ($pfHidden.length) print_format = $pfHidden.val() || null;
                    } else {
                        print_format = $pf.val() || null;
                    }
                } catch (e) {}
                try {
                    const $l = $("input[data-fieldname='language']:visible").first();
                    if (!$l.length) {
                        const $lHidden = $("input[data-fieldname='language']").first();
                        if ($lHidden.length) _lang = $lHidden.val() || null;
                    } else {
                        _lang = $l.val() || null;
                    }
                } catch (e) {}

                const $btn = $(btn).prop("disabled", true);
                const oldHtml = $btn.html();
                $btn.text("Sending…");
                frappe.call({
                    method: "gravures_custom.overrides.send_print_pdf_whatsapp",
                    args: { doctype: doctype, name: name, print_format: print_format || undefined, _lang: _lang || undefined },
                    callback: function (r) {
                        $(".btn-whatsapp-gc").prop("disabled", false).html(oldHtml);
                        if (r.message && r.message.success) {
                            frappe.show_alert({message: "Sent PDF to WhatsApp!", indicator: "green"});
                        } else if (r._server_messages) {
                            frappe.msgprint(r._server_messages.join("<br>"));
                        } else {
                            frappe.msgprint("Unexpected response from server.");
                        }
                    },
                    error: function (err) {
                        $(".btn-whatsapp-gc").prop("disabled", false).html(oldHtml);
                        frappe.msgprint("Failed: " + (err._message || "Unknown error."));
                    }
                });
            } catch (ex) {
                console.error("[GC] sendToWhatsApp error:", ex);
            }
        }

        function hideTryNewMessage() {
            try {
                // Remove the "Try the new Print Designer" message
                const $msg = $(".inner-page-message, a[href*='print-designer']").filter(function () {
                    return $(this).text().indexOf("Try the new") >= 0 || ($(this).attr("href") || "").indexOf("print-designer") >= 0;
                });
                $msg.remove();
                $msg.parent().remove();
            } catch (e) {
                // ignore
            }
        }

        function injectIntoToolbar() {
            try {
                const $ca = $(".page-actions .custom-actions, .custom-actions");
                if (!$ca || !$ca.length) return false;
                if ($ca.find(".btn-whatsapp-gc").length) return true;

                const $btn = $('<button class="btn btn-secondary btn-sm ellipsis btn-whatsapp-gc" title="Send PDF to WhatsApp" style="background-color:#25D366;border-color:#25D366;color:white;">' + WA_SVG + ' <span class="whatsapp-label">WhatsApp</span></button>');
                $btn.on("click", function (e) {
                    e.preventDefault();
                    if (!window.frappe || !frappe.call) {
                        alert("Frappe not yet loaded, please try again.");
                        return;
                    }
                    sendToWhatsApp(this);
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
                            // Hide the "Try the new Print Designer" message
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
            } catch (e) {
                // ignore
            }
        }

        startInjector();
    })();
