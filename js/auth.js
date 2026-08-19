/* FastStart Digital admin auth: Google OAuth chip in navbar + leads modal. */
(function () {
  "use strict";

  var T = window.FSD_T || function (s) { return s; };
  var E = function (s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  };

  var host = document.querySelector("#navbar .flex.items-center.gap-3") ||
    document.querySelector("[data-lang-host]");
  if (!host) return;

  var box = document.createElement("div");
  box.id = "fsd-auth";
  box.className = "fsd-auth";
  host.appendChild(box);

  var state = { user: null };
  var denied = /[?&]auth=denied/.test(location.search);

  function renderUser(u) {
    var pic = u.picture
      ? '<img class="fsd-av" src="' + E(u.picture) + '" alt="">'
      : '<span class="fsd-av">' + E((u.name || u.email || "A").charAt(0).toUpperCase()) + "</span>";
    box.innerHTML = pic +
      '<span class="fsd-auth-name">' + E(u.name || u.email) + "</span>" +
      '<button type="button" class="fsd-auth-btn" data-action="leads">' + T("Заявки") + "</button>" +
      '<button type="button" class="fsd-auth-btn" data-action="logout">' + T("Вийти") + "</button>";
  }

  function render() {
    if (state.user) {
      renderUser(state.user);
    } else {
      box.innerHTML = '<a class="fsd-auth-btn fsd-login" href="/api/auth/google">' + T("Увійти") + "</a>";
    }
  }

  if (denied) {
    var note = document.createElement("div");
    note.className = "fsd-auth-note";
    note.innerHTML = '<span class="pulse-dot w-1.5 h-1.5 rounded-full bg-rose-400"></span> ' + T("Доступ заборонено");
    host.insertBefore(note, box);
    setTimeout(function () { if (note.parentNode) note.parentNode.removeChild(note); }, 6000);
  }

  function openLeads() {
    var overlay = document.createElement("div");
    overlay.className = "fsd-modal";
    var panel = document.createElement("div");
    panel.className = "fsd-modal-panel";
    var titleRow = document.createElement("div");
    titleRow.className = "fsd-modal-head";
    var title = document.createElement("div");
    title.className = "font-display font-semibold text-white";
    title.id = "fsd-leads-title";
    title.textContent = T("Заявки");
    var closeBtns = document.createElement("div");
    closeBtns.className = "flex items-center gap-2";
    var refresh = document.createElement("button");
    refresh.type = "button";
    refresh.className = "fsd-auth-btn";
    refresh.textContent = T("Оновити");
    var close = document.createElement("button");
    close.type = "button";
    close.className = "fsd-auth-btn";
    close.textContent = T("Закрити");
    closeBtns.appendChild(refresh);
    closeBtns.appendChild(close);
    titleRow.appendChild(title);
    titleRow.appendChild(closeBtns);
    var body = document.createElement("div");
    body.className = "fsd-modal-body";
    panel.appendChild(titleRow);
    panel.appendChild(body);
    overlay.appendChild(panel);
    document.body.appendChild(overlay);

    function fill() {
      body.innerHTML = '<div class="text-slate-400">…</div>';
      fetch("/api/leads")
        .then(function (r) {
          if (r.status === 401) throw new Error("unauthorized");
          return r.json();
        })
        .then(function (d) {
          title.textContent = T("Заявки") + " · " + (d.count || 0);
          if (!d.leads || !d.leads.length) {
            body.innerHTML = '<div class="text-slate-400 py-6 text-center">' + T("Немає заявок") + "</div>";
            return;
          }
          var rows = d.leads.map(function (l) {
            var t = l.ts ? new Date(l.ts) : null;
            var when = t && !isNaN(t) ? t.toLocaleString() : "—";
            return "<tr>" +
              "<td class='fsd-cell f-t'>" + E(when) + "</td>" +
              "<td>" + E(l.name || "—") + "</td>" +
              "<td>" + E(l.contact || "—") + "</td>" +
              "<td>" + E(l.channel || "—") + "</td>" +
              "<td>" + E(l.type || "—") + "</td>" +
              "<td>" + E(l.budget || "—") + "</td>" +
              "<td class='f-msg'>" + E(l.message || "—") + "</td>" +
              "<td>" + E(l.source || "—") + "</td>" +
              "</tr>";
          }).join("");
          body.innerHTML = "<table class='fsd-leads'><thead><tr>" +
            "<th>" + T("Час") + "</th><th>" + T("Ім'я") + "</th><th>" + T("Контакт") + "</th>" +
            "<th>" + T("Канал") + "</th><th>" + T("Тип") + "</th><th>" + T("Бюджет") + "</th>" +
            "<th>" + T("Повідомлення") + "</th><th>" + T("Джерело") + "</th></tr></thead><tbody>" +
            rows + "</tbody></table>";
        })
        .catch(function () {
          body.innerHTML = '<div class="text-rose-400 py-6 text-center">' + T("Помилка завантаження") + "</div>";
        });
    }
    fill();
    refresh.addEventListener("click", fill);
    function closeModal() { if (overlay.parentNode) overlay.parentNode.removeChild(overlay); }
    close.addEventListener("click", closeModal);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) closeModal(); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && overlay.parentNode) closeModal();
    });
    panel.addEventListener("fsd:lang", function () {});
  }

  box.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-action]");
    if (!btn) return;
    if (btn.getAttribute("data-action") === "logout") {
      fetch("/api/auth/logout", { method: "POST" })
        .then(function () { state.user = null; render(); })
        .catch(function () {});
    } else if (btn.getAttribute("data-action") === "leads") {
      openLeads();
    }
  });

  document.addEventListener("fsd:lang", render);

  fetch("/api/auth/me", { credentials: "same-origin" })
    .then(function (r) { return r.json(); })
    .then(function (d) { state.user = d.ok ? d.user : null; render(); })
    .catch(function () { state.user = null; render(); });
})();