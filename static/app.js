// ReleaseRadar UI behaviours. Kept in a separate file (no inline scripts) so
// the Content-Security-Policy can stay at script-src 'self'.
"use strict";

// Confirmation dialogs (e.g. delete). The message lives in a data attribute,
// which survives names containing quotes — an inline onsubmit handler doesn't.
document.querySelectorAll("form[data-confirm]").forEach(function (form) {
  form.addEventListener("submit", function (event) {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});

// Dismissible flash messages.
document.querySelectorAll(".flash").forEach(function (flash) {
  var btn = document.createElement("button");
  btn.type = "button";
  btn.className = "flash-close";
  btn.setAttribute("aria-label", "Dismiss");
  btn.textContent = "×";
  btn.addEventListener("click", function () { flash.remove(); });
  flash.appendChild(btn);
});

// Dashboard watchlist filtering by free text and status.
(function () {
  var text = document.getElementById("filter-text");
  var status = document.getElementById("filter-status");
  var count = document.getElementById("filter-count");
  if (!text || !status) return;

  var rows = Array.prototype.slice.call(
    document.querySelectorAll("table.grid tbody tr[data-name]"));

  function rowMatches(row, needle, wanted) {
    if (needle && row.dataset.name.indexOf(needle) === -1) return false;
    if (!wanted) return true;
    if (wanted === "security") {
      return row.dataset.status === "update_available" &&
             row.dataset.type === "security";
    }
    return row.dataset.status === wanted;
  }

  function applyFilter() {
    var needle = text.value.trim().toLowerCase();
    var wanted = status.value;
    var shown = 0;
    rows.forEach(function (row) {
      var match = rowMatches(row, needle, wanted);
      row.hidden = !match;
      if (match) shown++;
    });
    if (count) {
      count.textContent = (needle || wanted)
        ? "showing " + shown + " of " + rows.length
        : "";
    }
  }

  text.addEventListener("input", applyFilter);
  status.addEventListener("change", applyFilter);
})();
