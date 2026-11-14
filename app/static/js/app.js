// ---- Navigation -----------------------------------------------------------
// Sidebar groups open and close independently and the choice is remembered (localStorage), so a group you
// opened stays open on every page. The group of the page you are on is always open.
// On small screens the sidebar is a drawer opened by the menu button.
(function () {
  var nav = document.getElementById("primary-navigation");
  var toggle = document.querySelector(".nav-toggle");
  if (nav) {
    var remember = function () {
      try {
        var open = [];
        nav.querySelectorAll("details.nav-group[open]").forEach(function (d) { open.push(d.dataset.group); });
        localStorage.setItem("nav.open", JSON.stringify(open));
      } catch (e) { /* storage blocked: the menu still works, it just forgets */ }
    };
    nav.addEventListener("toggle", function (e) {
      if (e.target.matches && e.target.matches("details.nav-group")) { remember(); }
    }, true);
  }
  if (nav && toggle) {
    var compact = window.matchMedia("(max-width: 960px)");
    function setDrawer(open) {
      nav.hidden = compact.matches ? !open : false;
      toggle.setAttribute("aria-expanded", String(compact.matches ? open : true));
    }
    setDrawer(false);
    compact.addEventListener("change", function () { setDrawer(false); });
    toggle.addEventListener("click", function () { setDrawer(nav.hidden); });
    nav.addEventListener("click", function (e) { if (e.target.closest("a") && compact.matches) { setDrawer(false); } });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && compact.matches) { setDrawer(false); } });
  }
  // "Print" prints just its own document (a page can show two, for example a TS and its AR).
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-print-doc]");
    if (!btn) { return; }
    var doc = btn.closest(".doc");
    document.body.classList.add("print-one");
    if (doc) { doc.classList.add("print-target"); }
    var done = function () {
      document.body.classList.remove("print-one");
      if (doc) { doc.classList.remove("print-target"); }
      window.removeEventListener("afterprint", done);
    };
    window.addEventListener("afterprint", done);
    window.print();
  });
  // The bell and the user menu close when you click elsewhere or press Escape.
  document.addEventListener("click", function (e) {
    document.querySelectorAll("details.menu[open]").forEach(function (m) { if (!m.contains(e.target)) { m.open = false; } });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { document.querySelectorAll("details.menu[open]").forEach(function (m) { m.open = false; }); }
  });
  // Safety net: any data table that is not in a frame gets one, so a wide table scrolls inside
  // its own frame and never makes the whole page scroll sideways.
  document.querySelectorAll("table.data").forEach(function (t) {
    if (t.parentElement && t.parentElement.classList.contains("table-wrap")) { return; }
    var wrap = document.createElement("div");
    wrap.className = "table-wrap";
    t.parentNode.insertBefore(wrap, t);
    wrap.appendChild(t);
  });
})();

// Only adds and removes rows in the item tables of the forms. No business logic here.
document.addEventListener("click", function (e) {
  var add = e.target.closest("[data-add-row]");
  if (add) {
    var tbody = document.querySelector("#" + add.dataset.addRow + " tbody");
    // Forms that already show saved lines keep an empty hidden row to copy.
    var source = tbody.querySelector("[data-row-template]") || tbody.querySelector(".line-row");
    var row = source.cloneNode(true);
    row.removeAttribute("hidden");
    row.removeAttribute("data-row-template");
    row.querySelectorAll("input").forEach(function (i) { if (i.type !== "checkbox") { i.value = ""; } });
    row.querySelectorAll("select").forEach(function (s) { s.selectedIndex = 0; });
    tbody.appendChild(row);
  }
  var rem = e.target.closest("[data-remove-row]");
  if (rem) {
    var tb = rem.closest("tbody");
    if (tb.querySelectorAll(".line-row").length > 1) { rem.closest("tr").remove(); }
  }
});

// Display helpers for the item tables (no business rules here, the server checks everything).
document.addEventListener("change", function (e) {
  var stockSel = e.target.closest("select[data-fill-stock]");
  if (stockSel) {
    var stockRow = stockSel.closest("tr"), opt = stockSel.options[stockSel.selectedIndex];
    var pick = { "on_hand[]": "onhand", "rop[]": "rop", "suggested[]": "suggested" };
    Object.keys(pick).forEach(function (name) {
      var input = stockRow.querySelector("input[name='" + name + "']");
      if (input) { input.value = opt && opt.dataset[pick[name]] !== undefined ? opt.dataset[pick[name]] : ""; }
    });
  }
  var sel = e.target.closest("select[data-fill-onhand]");
  if (sel) {
    var row = sel.closest("tr"), opt = sel.options[sel.selectedIndex];
    var shown = row.querySelector("input[name='on_hand[]'], input[name='qty_system[]']");
    if (shown) { shown.value = opt && opt.dataset.onhand !== undefined ? opt.dataset.onhand : ""; }
    updateDiff(row);
  }
});
document.addEventListener("input", function (e) {
  if (e.target.matches("[data-diff]")) { updateDiff(e.target.closest("tr")); }
});
// Quotation form: when the supplier changes, reload the unit-price boxes from
// that supplier's agreed prices. The server already fills the selected
// supplier's prices on page load, so this only runs when the choice changes.
document.addEventListener("change", function (e) {
  var sel = e.target.closest("#supplier_id");
  if (!sel) { return; }
  var node = document.getElementById("supplier-prices");
  if (!node) { return; }
  var prices;
  try { prices = JSON.parse(node.textContent || "{}"); } catch (err) { return; }
  var byItem = prices[sel.value] || {};
  document.querySelectorAll("#quote-lines tr[data-item-id]").forEach(function (row) {
    var input = row.querySelector("input[name='unit_price[]']");
    if (input) { input.value = byItem[row.getAttribute("data-item-id")] || ""; }
  });
});
// Supplier receiving: accepted and rejected fill each other from delivered.
// The server applies the same rule when one box is left blank.
function fillReceipt(row, source) {
  var dEl = row.querySelector("input[name='qty_delivered[]'], input[name='qty_expected[]']");
  var aEl = row.querySelector("input[name='qty_accepted[]']");
  var rEl = row.querySelector("input[name='qty_rejected[]']");
  if (!dEl || !aEl || !rEl) { return; }
  var d = Math.max(0, Number(dEl.value) || 0);
  if (source === "accepted") {
    var a = Math.min(Math.max(0, Number(aEl.value) || 0), d);
    aEl.value = String(a);
    rEl.value = String(d - a);
  } else if (source === "rejected") {
    var r = Math.min(Math.max(0, Number(rEl.value) || 0), d);
    rEl.value = String(r);
    aEl.value = String(d - r);
  } else {
    // Delivered changed: keep accepted if it still fits, otherwise accept all.
    var kept = aEl.value === "" ? d : Math.min(Math.max(0, Number(aEl.value) || 0), d);
    aEl.value = String(kept);
    rEl.value = String(d - kept);
  }
}
document.addEventListener("input", function (e) {
  var row = e.target.closest("tr[data-receipt]");
  if (!row) { return; }
  // Delivered is handled on change: while it is being cleared and retyped,
  // recalculating on every keystroke would wipe the accepted quantity.
  if (e.target.name === "qty_accepted[]") { fillReceipt(row, "accepted"); }
  else if (e.target.name === "qty_rejected[]") { fillReceipt(row, "rejected"); }
});
document.addEventListener("change", function (e) {
  var row = e.target.closest("tr[data-receipt]");
  if (row && e.target.name === "qty_delivered[]") { fillReceipt(row, "delivered"); }
});
document.querySelectorAll("tr[data-receipt]").forEach(function (row) {
  var a = row.querySelector("input[name='qty_accepted[]']");
  var r = row.querySelector("input[name='qty_rejected[]']");
  if (!a || !r) { return; }
  if (a.value === "" && r.value === "") { fillReceipt(row, "delivered"); }
  else if (a.value !== "" && r.value === "") { fillReceipt(row, "accepted"); }
  else if (a.value === "" && r.value !== "") { fillReceipt(row, "rejected"); }
});
function updateDiff(row) {
  var sys = row.querySelector("input[name='qty_system[]']"), act = row.querySelector("input[data-diff]"),
      diff = row.querySelector("input[name='qty_diff[]']");
  if (sys && act && diff) { diff.value = (sys.value !== "" && act.value !== "") ? (Number(act.value) - Number(sys.value)) : ""; }
}
