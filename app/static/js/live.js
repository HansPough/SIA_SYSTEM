// Live updates between users.
// The browser asks /api/live every few seconds for the bell, the sidebar badges and a revision
// token. When a colleague saves something, the token changes and this page catches up by itself;
// pages with fields being typed in are never replaced under the user's hands.
(function () {
  "use strict";
  var body = document.body;
  var main = document.getElementById("main-content");
  var stack = document.getElementById("toast-stack");
  var livePage = body.getAttribute("data-live") === "1";
  var rev = body.getAttribute("data-rev") || "";
  var lastAttention = null;
  var refreshing = false;
  var POLL_MS = 9000;

  // ---- small UI helpers ---------------------------------------------------
  function makeToast(message, tone) {
    if (!stack) { return; }
    var el = document.createElement("div");
    el.className = "toast " + (tone || "");
    var text = document.createElement("span");
    text.textContent = message;
    var close = document.createElement("button");
    close.type = "button";
    close.className = "toast-x";
    close.setAttribute("aria-label", "Dismiss");
    close.textContent = "\u00d7";
    el.appendChild(text);
    el.appendChild(close);
    stack.appendChild(el);
    var gone = false;
    function remove() {
      if (gone) { return; }
      gone = true;
      el.classList.add("leaving");
      window.setTimeout(function () { if (el.parentNode) { el.parentNode.removeChild(el); } }, 200);
    }
    close.addEventListener("click", remove);
    window.setTimeout(remove, 7000);
  }

  function hideFlash(el) {
    if (!el) { return; }
    el.classList.add("leaving");
    window.setTimeout(function () { if (el.parentNode) { el.parentNode.removeChild(el); } }, 200);
  }

  function enhanceFlashes() {
    document.querySelectorAll(".flash").forEach(function (el) {
      if (el.getAttribute("data-bound")) { return; }
      el.setAttribute("data-bound", "1");
      el.setAttribute("role", "status");
      if (!el.querySelector("[data-flash-close]")) {
        var x = document.createElement("button");
        x.type = "button";
        x.className = "flash-close";
        x.setAttribute("data-flash-close", "");
        x.setAttribute("aria-label", "Dismiss message");
        x.textContent = "\u00d7";
        el.appendChild(x);
      }
      var wait = el.classList.contains("error") ? 12000 : 8000;
      window.setTimeout(function () { hideFlash(el); }, wait);
    });
  }

  // Remember each dropdown's value from the server, so only a real user change counts as "busy".
  function rememberFormState() {
    if (!main) { return; }
    main.querySelectorAll("select").forEach(function (select) {
      if (typeof select._liveInitial === "undefined") { select._liveInitial = select.value; }
    });
  }

  function wrapTables() {
    document.querySelectorAll("table.data").forEach(function (table) {
      if (table.parentElement && table.parentElement.classList.contains("table-wrap")) { return; }
      var wrap = document.createElement("div");
      wrap.className = "table-wrap";
      table.parentNode.insertBefore(wrap, table);
      wrap.appendChild(table);
    });
  }

  // ---- bell and sidebar ---------------------------------------------------
  function setBell(total) {
    var summary = document.querySelector("#bell summary");
    if (!summary) { return; }
    var button = summary.querySelector(".bell-btn");
    var badge = button && button.querySelector(".bell-count");
    if (total > 0) {
      if (!badge) {
        badge = document.createElement("span");
        badge.className = "bell-count";
        button.appendChild(badge);
      }
      badge.textContent = total > 99 ? "99+" : String(total);
    } else if (badge) {
      badge.parentNode.removeChild(badge);
    }
    summary.setAttribute("aria-label", "Needs your attention: " + total);
  }

  function setPanel(rows) {
    var panel = document.getElementById("bell-panel");
    if (!panel) { return; }
    while (panel.firstChild) { panel.removeChild(panel.firstChild); }
    var title = document.createElement("h3");
    title.textContent = "Needs your attention";
    panel.appendChild(title);
    if (!rows.length) {
      var clear = document.createElement("div");
      clear.className = "all-clear";
      clear.textContent = "All clear. Nothing is waiting for you.";
      panel.appendChild(clear);
      return;
    }
    rows.forEach(function (row) {
      var a = document.createElement("a");
      a.className = "attn-row";
      a.href = row.url;
      var label = document.createElement("span");
      label.textContent = row.label;
      var n = document.createElement("span");
      n.className = "attn-n";
      n.textContent = row.n;
      a.appendChild(label);
      a.appendChild(n);
      panel.appendChild(a);
    });
  }

  function setNav(groups) {
    if (!groups) { return; }
    var boxes = document.querySelectorAll("#primary-navigation details.nav-group");
    groups.forEach(function (group) {
      var box = null;
      for (var i = 0; i < boxes.length; i++) {
        if (boxes[i].getAttribute("data-group") === group.label) { box = boxes[i]; break; }
      }
      if (!box) { return; }
      var summary = box.querySelector("summary");
      var groupBadge = summary && summary.querySelector(".nav-badge");
      if (group.count > 0) {
        if (!groupBadge) {
          groupBadge = document.createElement("span");
          groupBadge.className = "nav-badge";
          var chev = summary.querySelector(".chev");
          summary.insertBefore(groupBadge, chev);
        }
        groupBadge.textContent = group.count;
        groupBadge.title = group.count + " waiting";
      } else if (groupBadge) {
        groupBadge.parentNode.removeChild(groupBadge);
      }
      group.items.forEach(function (item) {
        var links = box.querySelectorAll("a.nav-link");
        for (var j = 0; j < links.length; j++) {
          if (links[j].getAttribute("href") !== item.url) { continue; }
          var badge = links[j].querySelector(".nav-badge");
          if (item.n > 0) {
            if (!badge) {
              badge = document.createElement("span");
              badge.className = "nav-badge";
              links[j].appendChild(badge);
            }
            badge.textContent = item.n;
          } else if (badge) {
            badge.parentNode.removeChild(badge);
          }
        }
      });
    });
  }

  function announceNew(rows) {
    var previous = lastAttention;
    var map = {};
    rows.forEach(function (row) { map[row.label] = row.n; });
    lastAttention = map;
    if (!previous) { return; }
    var fresh = [];
    rows.forEach(function (row) {
      if ((previous[row.label] || 0) < row.n) { fresh.push(row.label + " (" + row.n + ")"); }
    });
    if (fresh.length) {
      makeToast("New for you: " + fresh.join(", "), "warn");
    }
  }

  // ---- catching up with the page -----------------------------------------
  function isBusy() {
    if (!main) { return true; }
    var active = document.activeElement;
    if (active && main.contains(active) && active.matches("input, textarea, select, [contenteditable='true']")) {
      return true;
    }
    if (window.getSelection && String(window.getSelection()).length > 2) { return true; }
    var changed = false;
    main.querySelectorAll("input, textarea").forEach(function (field) {
      if (["hidden", "checkbox", "radio", "submit", "button", "file"].indexOf(field.type) > -1) { return; }
      if (field.value !== field.defaultValue) { changed = true; }
    });
    // A filter or choice picked but not yet submitted must not be reset either.
    main.querySelectorAll("select").forEach(function (select) {
      if (typeof select._liveInitial !== "undefined" && select.value !== select._liveInitial) {
        changed = true;
      }
    });
    return changed;
  }

  function applyPage(html) {
    var doc = new DOMParser().parseFromString(html, "text/html");
    var next = doc.getElementById("main-content");
    if (!next) {
      window.location.reload();
      return;
    }
    var open = [];
    document.querySelectorAll("#main-content details").forEach(function (d, i) { open[i] = d.open; });
    main.innerHTML = next.innerHTML;
    document.querySelectorAll("#main-content details").forEach(function (d, i) {
      if (open[i]) { d.open = true; }
    });
    enhanceFlashes();
    wrapTables();
    rememberFormState();
    var titles = doc.querySelectorAll("title");
    if (titles.length) { document.title = titles[0].textContent; }
  }

  function refreshPage() {
    if (!livePage || refreshing) { return; }
    if (isBusy()) { return; }
    refreshing = true;
    fetch(window.location.href, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { "X-Live-Refresh": "1" }
    }).then(function (response) {
      if (response.redirected && response.url.indexOf("/login") > -1) {
        window.location.reload();
        return null;
      }
      return response.ok ? response.text() : null;
    }).then(function (html) {
      if (!html) { return; }
      applyPage(html);
    }).catch(function () {
      // keep the page that is already on screen
    }).then(function () {
      refreshing = false;
    });
  }

  // ---- polling ------------------------------------------------------------
  function poll() {
    if (document.hidden) { return; }
    fetch("/api/live", {
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Accept": "application/json" }
    }).then(function (response) {
      if (response.status === 401) {
        window.location.reload();
        return null;
      }
      return response.ok ? response.json() : null;
    }).then(function (data) {
      if (!data || !data.ok) { return; }
      setBell(data.total);
      setPanel(data.attention);
      setNav(data.groups);
      announceNew(data.attention);
      if (!rev) {
        rev = data.rev;
        return;
      }
      if (data.rev === rev) { return; }
      rev = data.rev;
      if (livePage) { refreshPage(); }
    }).catch(function () {
      // keep the page that is already on screen
    });
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-flash-close]");
    if (button) { hideFlash(button.closest(".flash")); }
  });
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) { poll(); }
  });

  enhanceFlashes();
  rememberFormState();
  window.setTimeout(poll, 1200);
  window.setInterval(poll, POLL_MS);
})();
