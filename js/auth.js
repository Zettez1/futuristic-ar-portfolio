/* FastStart Digital auth: Google OAuth chip + account dropdown (Projects / Sign out) + projects modal + 5% coupon. */
(function () {
  "use strict";

  var T = window.FSD_T || function (s) { return s; };
  var E = function (s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  };

  var dbgCtx = {
    uid: Math.random().toString(36).slice(2, 10),
    t0: Date.now(),
    lastPts: [],
  };
  function dbg(e, extra) {
    var m = "[dbg?e=1] " + JSON.stringify({ uid: dbgCtx.uid, ms: Date.now() - dbgCtx.t0, e: e, x: extra || null });
    console.log(m);
    if (navigator.sendBeacon) {
      try { navigator.sendBeacon("/api/debug/log", new Blob([JSON.stringify({ e: m })], { type: "application/json" })); } catch (err) {}
    }
  }

  var host = document.querySelector("#navbar .flex.items-center.gap-3") ||
    document.querySelector("[data-lang-host]");
  if (!host) return;

  var box = document.createElement("div");
  box.id = "fsd-auth";
  box.className = "fsd-auth";
  box.style.position = "relative";
  host.appendChild(box);

  var state = { user: null, projects: null };
  var denied = /[?&]auth=denied/.test(location.search);
  var registeredNote = /[?&]auth=registered/.test(location.search);

  window.FSD_AUTH = {
    get user() { return state.user; },
    get isLoggedIn() { return !!state.user; },
  };

  function esc(s) { return E(s); }

  function statusLabel(status) {
    if (status === "в розробці") return T("В розробці");
    if (status === "завершено") return T("Завершено");
    return T("Нова");
  }

  /* ---- 5% discount (auto-applied to first project) ---- */

  function showCouponBadge() {
    if (!state.user) return;
    var btn = box.querySelector(".fsd-acc");
    if (btn) {
      var inside = btn.querySelector(".coupon-badge");
      if (inside) return;
      var b2 = document.createElement("span");
      b2.className = "coupon-badge";
      b2.textContent = "-5%";
      b2.title = T("Знижка 5% активована");
      var name = btn.querySelector(".fsd-auth-name");
      if (name && name.nextSibling) {
        btn.insertBefore(b2, name.nextSibling);
      } else {
        btn.appendChild(b2);
      }
      return;
    }
    var existing = box.querySelector(".coupon-badge");
    if (existing) return;
    var badge = document.createElement("span");
    badge.className = "coupon-badge";
    badge.textContent = "-5%";
    badge.title = T("Знижка 5% активована");
    box.appendChild(badge);
  }

  function ensureChip() {
    var u = state.user;
    if (!u) {
      box.innerHTML = '<a class="fsd-auth-btn fsd-login" href="/auth.html">' + T("Увійти") + "</a>";
      return;
    }
    if (box.querySelector(".fsd-acc")) return;
    var pic = u.picture
      ? '<img class="fsd-av" src="' + esc(u.picture) + '" alt="">'
      : '<span class="fsd-av">' + esc((u.name || u.email || "A").charAt(0).toUpperCase()) + "</span>";
    box.innerHTML = '<button type="button" class="fsd-acc" data-action="toggle" aria-haspopup="true" aria-expanded="false">' +
      pic + '<span class="fsd-auth-name">' + esc(u.name || u.email) + "</span>" +
      '<svg class="fsd-caret" width="10" height="10" viewBox="0 0 10 10" fill="none"><path d="M1 3l4 4 4-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>';
    if (u.coupon_5) {
      showCouponBadge();
    }
  }

  function renderMenu() {
    var dev = state.projects ? state.projects.dev_count : 0;
    var badge = dev > 0
      ? '<span class="fsd-badge">' + dev + '</span>'
      : "";
    var adminLink = state.projects && state.projects.admin
      ? '<a class="fsd-item" href="/admin.html" style="display:flex;text-decoration:none">⚙️ ' + T("Адмін-панель") + "</a>"
      : "";
    return '<div class="fsd-menu">' +
      '<div class="fsd-discount">' +
      '<span class="coupon-badge">-5%</span>' +
      '<span class="fsd-discount-text">' + T("Знижка 5% активована") + "</span>" +
      "</div>" +
      '<div class="fsd-discount-note">' + T("Знижка 5% на перший проєкт вже застосована до вашого акаунта") + "</div>" +
      '<button type="button" class="fsd-item" data-action="projects">' + T("Проекти") + badge + "</button>" +
      adminLink +
      '<button type="button" class="fsd-item" data-action="logout">' + T("Вийти") + "</button>" +
      "</div>";
  }

  function toggleMenu(open) {
    var menu = box.querySelector(".fsd-menu");
    if (open && !menu) box.insertAdjacentHTML("beforeend", renderMenu());
    if (!open && menu) menu.remove();
    var b = box.querySelector(".fsd-acc");
    if (b) b.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function render() { ensureChip(); }

  function refreshProjects() {
    return fetch("/api/projects", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        state.projects = d;
        var menu = box.querySelector(".fsd-menu");
        if (menu) {
          var tmp = document.createElement("div");
          tmp.innerHTML = renderMenu();
          menu.replaceWith(tmp.firstChild);
        }
      })
      .catch(function () {});
  }

  function closeMenu() { toggleMenu(false); }

  function openLeads() {
    var overlay = document.createElement("div");
    overlay.className = "fsd-modal";
    var panel = document.createElement("div");
    panel.className = "fsd-modal-panel";
    var titleRow = document.createElement("div");
    titleRow.className = "fsd-modal-head";
    var title = document.createElement("div");
    title.className = "font-display font-semibold text-white";
    title.id = "fsd-projects-title";
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

    var isAdmin = state.projects ? state.projects.admin : false;

    function statusTd(p) {
      if (!isAdmin) {
        return '<span class="fsd-status s-' + esc(p.status) + '">' + esc(statusLabel(p.status)) + "</span>";
      }
      var opts = ["нова", "в розробці", "завершено"].map(function (s) {
        return '<option value="' + s + '"' + (p.status === s ? " selected" : "") + ">" + esc(statusLabel(s)) + "</option>";
      }).join("");
      return '<select class="fsd-status-sel" data-ts="' + esc(p.ts) + '">' + opts + "</select>";
    }

    function progressHtml(p) {
      var pr = typeof p.progress === "number" ? p.progress : 0;
      return '<div class="fsd-progress"><div class="fsd-progress-bar" style="width:' + pr + '%"></div></div>' +
        '<span class="fsd-progress-label">' + pr + "%</span>";
    }

    function chatBlock() {
      var wrap = document.createElement("div");
      wrap.className = "fsd-chat-wrap";
      wrap.innerHTML = '<div class="fsd-chat-divider"><span>' + T("Чат з командою") + '</span></div>' +
        '<div class="fsd-chat-box"><div class="fsd-chat-msgs" aria-live="polite"></div>' +
        '<div class="fsd-chat-inputs"><input class="fsd-chat-input" type="text" maxlength="2000" placeholder="' +
        esc(T("Ваше повідомлення…")) + '" /><button class="fsd-chat-send">' + esc(T("Надіслати")) + "</button></div></div>";
      var box = wrap.querySelector(".fsd-chat-msgs");
      var inp = wrap.querySelector(".fsd-chat-input");
      var sendBtn = wrap.querySelector(".fsd-chat-send");
      var loading = true;

      function renderMsgs(t) {
        var msgs = (t && t.messages) || [];
        if (!msgs.length) {
          box.innerHTML = '<div class="fsd-chat-empty">' + esc(T("Напишіть нам — відповімо протягом доби.")) + "</div>";
          return;
        }
        box.innerHTML = msgs.map(function (m) {
          var mine = m.from === "client";
          return '<div class="fsd-msg ' + (mine ? "mine" : "theirs") + '"><span class="fsd-msg-tx">' +
            esc(m.text) + '</span><span class="fsd-msg-ts">' + fmtWhen(m.ts) + "</span></div>";
        }).join("");
        box.scrollTop = box.scrollHeight;
      }

      function load() {
        return fetch("/api/chat/my", { credentials: "same-origin" })
          .then(function (r) { return r.json(); })
          .then(function (d) { renderMsgs(d.thread); })
          .catch(function () {});
      }

      function send() {
        var text = inp.value.trim();
        if (!text) return;
        inp.value = "";
        fetch("/api/chat/my/send", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ text: text }),
        }).then(function (r) { return r.json(); }).then(function () { return load(); }).catch(function () {});
      }

      sendBtn.addEventListener("click", send);
      inp.addEventListener("keydown", function (e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
      load();
      return wrap;
    }

    function fmtWhen(ts) {
      if (!ts) return "";
      var d = new Date(ts);
      return isNaN(d) ? "" : d.toLocaleString("uk-UA", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
    }

    function fill() {
      body.innerHTML = '<div class="text-slate-400">…</div>';
      fetch("/api/projects", { credentials: "same-origin" })
        .then(function (r) {
          if (r.status === 401) throw new Error("unauthorized");
          return r.json();
        })
        .then(function (d) {
          state.projects = d;
          title.textContent = T("Проекти") + " · " + (d.count || 0);
          var couponInfo = "";
          if (d.coupon_5) {
            couponInfo = '<span class="coupon-badge" style="margin-left:8px">-5%</span>';
          }
          title.innerHTML = T("Проекти") + " · " + (d.count || 0) + couponInfo;
          if (!d.projects || !d.projects.length) {
            body.innerHTML = '<div class="text-slate-400 py-6 text-center">' + T("Немає проєктів") + "</div>";
            return;
          }
          var rows = d.projects.map(function (p) {
            var t = p.ts ? new Date(p.ts) : null;
            var when = t && !isNaN(t) ? t.toLocaleString() : "—";
            return "<tr>" +
              "<td>" + statusTd(p) + "</td>" +
              "<td>" + esc(p.type || "—") + "</td>" +
              "<td>" + esc(p.budget || "—") + "</td>" +
              "<td>" + progressHtml(p) + "</td>" +
              "<td>" + E(when) + "</td>" +
              "<td class='f-msg'>" + esc(p.message || "—") + "</td>" +
              "</tr>";
          }).join("");
          body.innerHTML = "<table class='fsd-leads'><thead><tr>" +
            "<th>" + T("Статус") + "</th><th>" + T("Тип") + "</th><th>" + T("Бюджет") + "</th>" +
            "<th>" + T("Прогрес") + "</th><th>" + T("Час") + "</th><th>" + T("Повідомлення") + "</th></tr></thead><tbody>" +
            rows + "</tbody></table>";
          body.appendChild(chatBlock());
          if (isAdmin) {
            body.querySelectorAll("select.fsd-status-sel").forEach(function (sel) {
              sel.addEventListener("change", function () {
                fetch("/api/projects/status", {
                  method: "PATCH",
                  headers: { "Content-Type": "application/json" },
                  credentials: "same-origin",
                  body: JSON.stringify({ ts: sel.getAttribute("data-ts"), status: sel.value }),
                }).then(function (r) { return r.json(); }).then(function () { refreshProjects(); fill(); });
              });
            });
          }
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
    var onKey = function (e) { if (e.key === "Escape" && overlay.parentNode) closeModal(); };
    document.addEventListener("keydown", onKey);
    overlay.addEventListener("fsd:lang", function () { closeModal(); document.removeEventListener("keydown", onKey); });
  }

  box.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-action]");
    if (!btn) return;
    var act = btn.getAttribute("data-action");
    if (act === "toggle") {
      var opening = !box.querySelector(".fsd-menu");
      toggleMenu(opening);
      if (opening) refreshProjects();
    } else if (act === "projects") {
      closeMenu();
      openLeads();
    } else if (act === "logout") {
      fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" })
        .then(function () { state.user = null; state.projects = null; toggleMenu(false); ensureChip(); })
        .catch(function () {});
    }
  });

  document.addEventListener("click", function (e) {
    var menu = box.querySelector(".fsd-menu");
    if (menu && !e.target.closest("#fsd-auth")) closeMenu();
  });

  document.addEventListener("fsd:lang", render);

  if (denied) {
    var note = document.createElement("div");
    note.className = "fsd-auth-note";
    note.innerHTML = '<span class="pulse-dot w-1.5 h-1.5 rounded-full bg-rose-400"></span> ' + T("Доступ заборонено");
    if (box.parentNode) box.parentNode.insertBefore(note, box);
    setTimeout(function () { if (note.parentNode) note.parentNode.removeChild(note); }, 6000);
  }

  if (registeredNote) {
    var regNote = document.createElement("div");
    regNote.className = "fsd-auth-note fsd-auth-note-ok";
    regNote.innerHTML = '<span class="pulse-dot w-1.5 h-1.5 rounded-full bg-emerald-400"></span> ' + T("Акаунт створено!");
    if (box.parentNode) box.parentNode.insertBefore(regNote, box);
    setTimeout(function () { if (regNote.parentNode) regNote.parentNode.removeChild(regNote); }, 8000);
  }

  ensureChip();

  window.addEventListener("pagehide", function () { dbg("pagehide"); });
  window.addEventListener("beforeunload", function () { dbg("beforeunload"); });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") dbg("vis-hidden");
  });

  fetch("/api/auth/me", { credentials: "same-origin" })
    .then(function (r) { return r.json(); })
    .then(function (d) { state.user = d.ok ? d.user : null; ensureChip(); })
    .catch(function () { state.user = null; ensureChip(); });
})();