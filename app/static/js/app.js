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
    var allowEmpty = tb.closest("table") && tb.closest("table").hasAttribute("data-allow-empty");
    if (allowEmpty || tb.querySelectorAll(".line-row").length > 1) {
      rem.closest("tr").remove();
      var empty = tb.querySelector("[data-empty-row]");
      if (empty) { empty.hidden = !!tb.querySelector(".line-row"); }
    }
  }
});

// ---- Search + Add rows (stock count and stock adjustments) ----------------
// The full item list is too long to show, so the user searches for the item
// they counted or adjusted and adds only that row.
(function () {
  var matches = {};
  function items() {
    var node = document.getElementById("item-data");
    if (!node) { return []; }
    try { return JSON.parse(node.textContent || "[]"); } catch (e) { return []; }
  }
  function label(item) { return item.item_code + " \u2014 " + item.item_name; }
  function findItem(value, rows) {
    var q = String(value || "").trim().toLowerCase();
    if (!q) { return null; }
    var exact = rows.filter(function (i) { return label(i).toLowerCase() === q; });
    if (exact.length === 1) { return exact[0]; }
    var part = rows.filter(function (i) {
      return (i.item_code + " " + i.item_name).toLowerCase().indexOf(q) > -1;
    });
    if (part.length === 1) { return part[0]; }
    if (part.length > 1) { return { ambiguous: true }; }
    return null;
  }
  function hideResults(kind) {
    var box = document.getElementById(kind + "-results");
    if (box) { box.hidden = true; box.innerHTML = ""; }
    matches[kind] = [];
  }
  function hideAllResults() {
    Object.keys(matches).forEach(hideResults);
    document.querySelectorAll(".picker-results").forEach(function (box) { box.hidden = true; });
  }
  function renderResults(kind, value) {
    var box = document.getElementById(kind + "-results");
    if (!box) { return; }
    var q = String(value || "").trim().toLowerCase();
    if (!q) { hideResults(kind); return; }
    var rows = items().filter(function (i) {
      return (i.item_code + " " + i.item_name).toLowerCase().indexOf(q) > -1;
    }).slice(0, 8);
    matches[kind] = rows;
    box.innerHTML = "";
    if (!rows.length) {
      var none = document.createElement("div");
      none.className = "picker-none";
      none.textContent = "No item matches.";
      box.appendChild(none);
    } else {
      rows.forEach(function (item) {
        var option = document.createElement("button");
        option.type = "button";
        option.className = "picker-item";
        option.setAttribute("data-pick-item", kind);
        option.setAttribute("data-id", item.id);
        var code = document.createElement("span");
        code.className = "picker-code";
        code.textContent = item.item_code;
        var name = document.createElement("span");
        name.textContent = item.item_name;
        option.appendChild(code);
        option.appendChild(name);
        box.appendChild(option);
      });
    }
    box.hidden = false;
  }
  function addRow(kind, item, hint, opts) {
    opts = opts || {};
    var tbody = document.getElementById(kind + "-lines");
    var template = document.getElementById(kind + "-row-template");
    if (!tbody || !template) { return false; }
    var existing = null;
    tbody.querySelectorAll('input[name="item_id[]"]').forEach(function (input) {
      if (input.value === String(item.id)) { existing = input.closest("tr"); }
    });
    if (existing) {
      // Already on the sheet: point the user at the existing line instead of adding a second one.
      if (!opts.quiet) {
        existing.classList.add("picked");
        if (existing.scrollIntoView) { existing.scrollIntoView({block: "nearest"}); }
        var box = existing.querySelector('input[name="qty_actual[]"]');
        if (box) { box.focus(); box.select(); }
        setTimeout(function () { existing.classList.remove("picked"); }, 1400);
        if (hint) { hint.textContent = "Already on the sheet \u2014 count it there."; }
      }
      return false;
    }
    var row = template.content.firstElementChild.cloneNode(true);
    row.querySelector("[data-name]").textContent = item.item_name;
    row.querySelector("[data-code]").textContent = item.item_code;
    row.querySelector('input[name="item_id[]"]').value = item.id;
    var onhand = row.querySelector("[data-onhand]");
    if (onhand) { onhand.textContent = item.on_hand; }
    var system = row.querySelector("[data-system]");
    if (system) { system.textContent = item.on_hand; }
    var hiddenSystem = row.querySelector('input[name="qty_system[]"]');
    if (hiddenSystem) { hiddenSystem.value = item.on_hand; }
    var actual = row.querySelector('input[name="qty_actual[]"]');
    if (actual) { actual.value = item.on_hand; }
    var empty = tbody.querySelector("[data-empty-row]");
    if (empty) { empty.hidden = true; }
    tbody.appendChild(row);
    if (hint && !opts.quiet) { hint.textContent = ""; }
    if (kind === "adjust" && typeof updateDiff === "function") { updateDiff(row); }
    if (actual && !opts.quiet) { actual.focus(); actual.select(); }
    return true;
  }
  function addItem(kind, item) {
    var input = document.getElementById(kind + "-search");
    var hint = document.getElementById(kind + "-hint");
    if (!item || !input || !hint) { return; }
    var added = addRow(kind, item, hint);
    input.value = "";
    hideResults(kind);
    // A duplicate keeps the existing row focused; a new row returns to the search box.
    if (added) { input.focus(); }
  }
  document.addEventListener("input", function (e) {
    var input = e.target.closest("[data-item-search]");
    if (!input) { return; }
    renderResults(input.getAttribute("data-item-search"), input.value);
  });
  document.addEventListener("click", function (e) {
    var pick = e.target.closest("[data-pick-item]");
    if (pick) {
      var pickKind = pick.getAttribute("data-pick-item");
      var picked = items().filter(function (i) { return String(i.id) === pick.getAttribute("data-id"); })[0];
      addItem(pickKind, picked);
      return;
    }
    var button = e.target.closest("[data-add-item]");
    if (button) {
      var kind = button.getAttribute("data-add-item");
      var input = document.getElementById(kind + "-search");
      var hint = document.getElementById(kind + "-hint");
      var found = findItem(input.value, items());
      if (!found && matches[kind] && matches[kind].length === 1) { found = matches[kind][0]; }
      if (!found) { hint.textContent = "Pick an item from the list."; return; }
      if (found.ambiguous) { hint.textContent = "Several items match. Pick one from the list."; return; }
      addItem(kind, found);
      return;
    }
    if (!e.target.closest(".picker")) { hideAllResults(); }
  });
  document.addEventListener("keydown", function (e) {
    var input = e.target.closest("[data-item-search]");
    if (!input) { return; }
    var kind = input.getAttribute("data-item-search");
    if (e.key === "Escape") { hideResults(kind); return; }
    if (e.key !== "Enter") { return; }
    e.preventDefault();
    var found = matches[kind] && matches[kind].length ? matches[kind][0] : findItem(input.value, items());
    if (found) { addItem(kind, found); }
  });
  // Monthly audit: add every item that matches the chosen filters (category and/or shelf),
  // including items whose system stock is 0.
  document.addEventListener("click", function (e) {
    var button = e.target.closest("[data-load-apply]");
    if (!button) { return; }
    var kind = button.getAttribute("data-load-apply");
    var hint = document.getElementById(kind + "-load-hint");
    var filters = {};
    document.querySelectorAll('[data-load-filter][data-load-kind="' + kind + '"]').forEach(function (select) {
      if (select.value) { filters[select.getAttribute("data-load-filter")] = select.value.toLowerCase(); }
    });
    var rows = items().filter(function (item) {
      return Object.keys(filters).every(function (key) {
        return String(item[key] == null ? "" : item[key]).toLowerCase() === filters[key];
      });
    });
    var added = 0, skipped = 0;
    rows.forEach(function (item) {
      if (addRow(kind, item, hint, {quiet: true})) { added += 1; } else { skipped += 1; }
    });
    if (added) {
      // Quiet add: one focus at the top of the sheet, not a scroll to the last row.
      var first = document.querySelector("#" + kind + "-lines input[name=\"qty_actual[]\"]");
      if (first) { first.focus(); first.select(); }
    }
    if (hint) {
      if (!rows.length) {
        hint.textContent = "No item matches those filters.";
      } else if (!added) {
        hint.textContent = skipped === 1 ? "Nothing to add \u2014 that item is already on the sheet."
          : "Nothing to add \u2014 all " + skipped + " matching items are already on the sheet.";
      } else {
        hint.textContent = "Added " + added + " item" + (added === 1 ? "" : "s") +
          (skipped ? "; " + skipped + " already on the sheet." : ".");
      }
    }
  });
  // Enter in a counted quantity starts the next item at the search box instead of
  // submitting the whole sheet.
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" || !e.target.closest) { return; }
    var qty = e.target.closest('input[name="qty_actual[]"]');
    if (!qty) { return; }
    var tbody = qty.closest("tbody");
    var match = tbody && tbody.id ? tbody.id.match(/^([a-z]+)-lines$/) : null;
    var search = match ? document.getElementById(match[1] + "-search") : null;
    if (!search) { return; }
    e.preventDefault();
    search.focus();
    search.select();
  });
})();

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
    var flag = row.querySelector(".agreed-flag");
    var agreed = byItem[row.getAttribute("data-item-id")];
    if (input) {
      input.value = agreed || "";
      if (agreed) { input.setAttribute("data-from-agreed", "1"); }
      else { input.removeAttribute("data-from-agreed"); }
    }
    if (flag) { flag.style.display = agreed ? "" : "none"; }
  });
});
// A price the user typed is their own answer, so the agreed-price badge goes away.
document.addEventListener("input", function (e) {
  if (!e.target.matches("input[data-from-agreed]")) { return; }
  var row = e.target.closest("tr");
  var flag = row && row.querySelector(".agreed-flag");
  if (flag) { flag.style.display = "none"; }
  e.target.removeAttribute("data-from-agreed");
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
