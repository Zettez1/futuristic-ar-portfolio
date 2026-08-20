/* FastStart Digital — private admin panel (hidden from clients). */
(function () {
  "use strict";

  var USER = null;
  var THREADS = [];
  var CUR_THREAD = null;
  var POLL_HANDLE = null;

  var $ = function (id) { return document.getElementById(id); };

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function api(path, opts) {
    opts = opts || {};
    opts.credentials = "same-origin";
    opts.headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    return fetch(path, opts).then(function (r) {
      return r.json().then(function (d) {
        if (!r.ok) throw new Error(d.error || ("HTTP " + r.status));
        return d;
      });
    });
  }

  function fmtTs(ts) {
    if (!ts) return "—";
    var d = new Date(typeof ts === "string" && ts.indexOf("Z") === -1 && ts.indexOf("+") === -1
      ? ts + "Z" : ts);
    if (isNaN(d.getTime())) return ts;
    var now = new Date();
    var diff = (now - d) / 1000;
    if (diff < 60) return "щойно";
    if (diff < 3600) return Math.floor(diff / 60) + " хв тому";
    if (diff < 86400) return Math.floor(diff / 3600) + " год тому";
    return d.toLocaleDateString("uk-UA", { day: "numeric", month: "short" }) + " " +
      d.toLocaleTimeString("uk-UA", { hour: "2-digit", minute: "2-digit" });
  }

  function fileSize(n) {
    if (n == null) return "";
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }

  function msgHtml(m) {
    var mine = m.from === "admin";
    var body = "";
    if (m.file) {
      var url = "/api/chat/file?id=" + encodeURIComponent(m.file.id);
      var isImg = /^image\//.test(m.file.mime || "");
      body += '<a class="adm-file" href="' + url + '" target="_blank" rel="noopener">' +
        (isImg
          ? '<img class="adm-file-thumb" src="' + url + '" alt="' + esc(m.file.name) + '">'
          : '<span class="adm-file-ico">📄</span>') +
        '<span class="adm-file-name">' + esc(m.file.name) + '</span>' +
        '<span class="adm-file-size">' + fileSize(m.file.size) + "</span></a>";
    }
    if (m.text) body += '<div class="adm-msg-text">' + esc(m.text) + "</div>";
    return '<div class="adm-msg ' + (mine ? "adm-msg-admin" : "adm-msg-client") + '">' + body +
      '<div class="adm-msg-ts">' + fmtTs(m.ts) + "</div></div>";
  }

  /* ---------------- tabs ---------------- */
  function switchTab(name) {
    document.querySelectorAll(".adm-tab").forEach(function (b) {
      b.classList.toggle("adm-tab-active", b.dataset.tab === name);
    });
    document.querySelectorAll(".adm-tab-panel").forEach(function (p) {
      p.classList.toggle("hidden", p.id !== "tab-" + name);
    });
    if (name === "overview") loadOverview();
    if (name === "leads") loadLeads();
    if (name === "chat") loadThreads();
    if (name === "activity") loadActivity();
  }

  /* ---------------- overview ---------------- */
  function loadOverview() {
    api("/api/admin/stats").then(function (s) {
      var cards = [
        { l: "Заявок усього", v: s.leads_total, c: "stats-blue" },
        { l: "Нові", v: s.leads_new, c: "stats-green" },
        { l: "У розробці", v: s.leads_dev, c: "stats-amber" },
        { l: "Завершено", v: s.leads_done, c: "stats-purple" },
        { l: "Клієнтів (акаунтів)", v: s.users_total, c: "stats-blue" },
        { l: "Діалогів", v: s.threads, c: "stats-cyan" },
        { l: "Непрочитані", v: s.unread_admin, c: "stats-red" },
        { l: "Відвідувачів/24год", v: s.visitors_24h, c: "stats-green" },
        { l: "Переглядів/24год", v: s.pageviews_24h, c: "stats-gray" },
        { l: "BTrade-бот", v: s.bot_online ? "онлайн" : "офлайн", c: s.bot_online ? "stats-green" : "stats-gray" },
      ];
      $("adm-stats").innerHTML = cards.map(function (c) {
        return '<div class="adm-stat ' + c.c + '"><div class="adm-stat-v">' + esc(c.v) +
          '</div><div class="adm-stat-l">' + esc(c.l) + '</div></div>';
      }).join("");
      var t = $("adm-leads-preview");
      t.innerHTML = s.leads_total
        ? '<thead><tr><th>Клієнт</th><th>Контакт</th><th>Тип</th><th>Бюджет</th><th>Статус</th><th>Дата</th></tr></thead><tbody>' +
          s.leads_preview.map(leadRow).join("") + "</tbody>"
        : '<tbody><tr><td class="adm-empty">Поки що заявок немає</td></tr></tbody>';
    }).catch(function (e) { flash(e.message); });
  }

  function leadRow(l) {
    var st = esc(l.status || "нова");
    return '<tr><td>' + esc(l.name || "—") + '</td><td>' + esc(l.contact || "—") +
      '</td><td>' + esc(l.type || "—") + '</td><td>' + esc(l.budget || "—") + '</td>' +
      '<td><span class="adm-status adm-status-' + statusClass(l.status) + '">' + st + '</span>' +
      (typeof l.progress === "number" ? '<div class="adm-progress"><div class="adm-progress-bar" style="width:' + l.progress + '%"></div></div>' : '') +
      '</td><td>' + fmtTs(l.ts) + '</td></tr>';
  }

  function statusClass(s) {
    s = (s || "нова").toLowerCase();
    if (s.indexOf("завершено") > -1) return "done";
    if (s.indexOf("розроб") > -1) return "dev";
    return "new";
  }

  /* ---------------- leads ---------------- */
  function loadLeads() {
    var filter = $("adm-filter-status").value;
    api("/api/admin/leads").then(function (d) {
      var rows = d.leads.filter(function (l) {
        return !filter || (l.status || "") === filter;
      });
      var t = $("adm-leads");
      if (!rows.length) {
        t.innerHTML = '<tbody><tr><td class="adm-empty">Нічого не знайдено</td></tr></tbody>';
        return;
      }
      t.innerHTML =
        '<thead><tr><th>Дата</th><th>Клієнт</th><th>Контакт</th><th>Тип</th><th>Бюджет</th><th>Статус</th><th>Прогрес</th><th>Джерело</th><th></th></tr></thead><tbody>' +
        rows.map(function (l) {
          return '<tr>' +
            '<td class="adm-cell-nowrap">' + fmtTs(l.ts) + '</td>' +
            '<td>' + esc(l.name || "—") + (l.email ? '<div class="adm-sub">' + esc(l.email) + "</div>" : "") + '</td>' +
            '<td>' + esc(l.contact || "—") + '</td>' +
            '<td>' + esc(l.type || "—") + '</td>' +
            '<td>' + esc(l.budget || "—") + '</td>' +
            '<td><select class="adm-select adm-status-sel" data-ts="' + esc(l.ts) + '">' +
            ["нова", "в розробці", "завершено"].map(function (s) {
              return '<option' + (s === (l.status || "нова") ? " selected" : "") + ">" + s + "</option>";
            }).join("") + '</select>' +
            (l.note ? '<div class="adm-sub">' + esc(l.note) + "</div>" : "") + '</td>' +
            '<td><div class="adm-progress-w"><input type="range" min="0" max="100" step="5" value="' +
            (typeof l.progress === "number" ? l.progress : 0) + '" class="adm-range" data-ts="' + esc(l.ts) +
            '"><span class="adm-range-v" data-ts="' + esc(l.ts) + '">' +
            (typeof l.progress === "number" ? l.progress : 0) + '%</span></div></td>' +
            '<td>' + esc(l.source || "—") + '</td>' +
            '<td><button class="adm-btn adm-btn-xs" data-open-chat="' + esc(l.ts) + '" data-email="' + esc(l.email || "") + '">Чат</button></td>' +
            "</tr>";
        }).join("") + "</tbody>";
      t.querySelectorAll(".adm-status-sel").forEach(function (sel) {
        sel.addEventListener("change", function () {
          api("/api/admin/project", {
            method: "PATCH",
            body: JSON.stringify({ ts: sel.dataset.ts, status: sel.value })
          }).then(function () { flash("Статус оновлено"); }).catch(function (e) { flash(e.message); });
        });
      });
      t.querySelectorAll(".adm-range").forEach(function (r) {
        r.addEventListener("input", function () {
          var v = t.querySelector('.adm-range-v[data-ts="' + r.dataset.ts + '"]');
          if (v) v.textContent = r.value + "%";
        });
        r.addEventListener("change", function () {
          api("/api/admin/project", {
            method: "PATCH",
            body: JSON.stringify({ ts: r.dataset.ts, progress: parseInt(r.value, 10) })
          }).then(function () { flash("Прогрес збережено"); }).catch(function (e) { flash(e.message); });
        });
      });
      t.querySelectorAll("[data-open-chat]").forEach(function (b) {
        b.addEventListener("click", function () {
          api("/api/admin/chat/open", {
            method: "POST",
            body: JSON.stringify({ email: b.dataset.email, lead_ts: b.dataset.openChat })
          }).then(function (d) {
            openThread(d.thread.id);
            switchTab("chat");
          }).catch(function (e) { flash(e.message); });
        });
      });
    }).catch(function (e) { flash(e.message); });
  }

  /* ---------------- chat ---------------- */
  function loadThreads() {
    api("/api/admin/chats").then(function (d) {
      THREADS = d.threads;
      var unread = d.threads.reduce(function (n, t) { return n + (t.unread_admin || 0); }, 0);
      var badge = $("adm-chat-badge");
      badge.textContent = unread;
      badge.classList.toggle("hidden", !unread);
      var el = $("adm-threads");
      if (!d.threads.length) {
        el.innerHTML = '<div class="adm-chat-empty">Діалогів ще немає.<br>Клієнт може почати чат зі сторінки «Мої проєкти» або напишіть першим через кнопку «Чат» поруч із заявкою.</div>';
        return;
      }
      el.innerHTML = d.threads.map(function (t) {
        var who = t.name || t.email || t.contact || "клієнт";
        var cls = t.unread_admin ? "adm-chat-item adm-chat-item-unread" : "adm-chat-item";
        return '<div class="' + cls + '" data-thread="' + esc(t.id) + '">' +
          '<div class="adm-chat-item-name">' + esc(who) + (t.unread_admin ? '<span class="adm-badge">' + t.unread_admin + "</span>" : "") + '</div>' +
          '<div class="adm-chat-item-sub">' + (t.lead_type ? esc(t.lead_type) + " · " : "") + fmtTs(t.updated) + '</div>' +
          '<div class="adm-chat-item-last">' + (t.last_from === "client" ? "👤 " : "🛠 ") + esc(t.last_message || "—") + "</div>" +
          "</div>";
      }).join("");
      el.querySelectorAll("[data-thread]").forEach(function (item) {
        item.addEventListener("click", function () {
          document.querySelectorAll(".adm-chat-item").forEach(function (i) { i.classList.remove("adm-chat-item-active"); });
          item.classList.add("adm-chat-item-active");
          openThread(item.dataset.thread);
        });
      });
    }).catch(function (e) { flash(e.message); });
  }

  function openThread(id) {
    CUR_THREAD = id;
    api("/api/admin/chat?id=" + encodeURIComponent(id)).then(function (d) {
      var t = d.thread;
      $("adm-chat-empty").classList.add("hidden");
      $("adm-chat-head").classList.remove("hidden");
      $("adm-chat-msgs").classList.remove("hidden");
      $("adm-chat-compose").classList.remove("hidden");
      var who = t.name || t.email || t.contact || "клієнт";
      $("adm-chat-head").innerHTML =
        '<span class="adm-chat-head-name">' + esc(who) + '</span>' +
        (t.email ? '<span class="adm-chat-head-sub">' + esc(t.email) +
          (t.contact ? " · " + esc(t.contact) : "") + "</span>" : "");
      var msgs = t.messages || [];
      $("adm-chat-msgs").innerHTML = msgs.length
        ? msgs.map(msgHtml).join("")
        : '<div class="adm-chat-empty">Повідомлень поки немає — напишіть клієнту першим.</div>';
      $("adm-chat-msgs").scrollTop = $("adm-chat-msgs").scrollHeight;
      loadThreads();
    }).catch(function (e) { flash(e.message); });
  }

  $("adm-chat-send").addEventListener("click", sendChat);
  $("adm-chat-input").addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); }
  });
  var ADM_MAX_FILE = 10 * 1024 * 1024;
  var ADM_PENDING = null;
  $("adm-chat-file").addEventListener("change", function () {
    var f = this.files && this.files[0];
    this.value = "";
    if (!f) return;
    var pend = $("adm-pending");
    if (f.size > ADM_MAX_FILE) {
      pend.textContent = "Файл завеликий (макс 10 МБ).";
      pend.classList.remove("hidden");
      setTimeout(function () { if (ADM_PENDING) { pend.textContent = "📎 " + ADM_PENDING.name + " · " + fileSize(ADM_PENDING.size) + " ✕"; } else { pend.classList.add("hidden"); pend.textContent = ""; } }, 4000);
      return;
    }
    var rd = new FileReader();
    rd.onload = function () {
      ADM_PENDING = { name: f.name || "file", mime: f.type || "application/octet-stream", size: f.size, data: String(rd.result).split(",")[1] || "" };
      pend.textContent = "📎 " + ADM_PENDING.name + " · " + fileSize(ADM_PENDING.size) + " ✕";
      pend.classList.remove("hidden");
    };
    rd.readAsDataURL(f);
  });
  $("adm-pending").addEventListener("click", function () {
    ADM_PENDING = null;
    this.classList.add("hidden");
    this.textContent = "";
  });

  function sendChat() {
    var inp = $("adm-chat-input");
    var text = inp.value.trim();
    if ((!text && !ADM_PENDING) || !CUR_THREAD) return;
    var payload = { thread_id: CUR_THREAD, text: text };
    if (ADM_PENDING) {
      payload.file = ADM_PENDING;
      ADM_PENDING = null;
      var pend = $("adm-pending");
      pend.classList.add("hidden");
      pend.textContent = "";
    }
    inp.value = "";
    api("/api/admin/chat/send", {
      method: "POST",
      body: JSON.stringify(payload)
    }).then(function () { openThread(CUR_THREAD); }).catch(function (e) { flash(e.message); });
  }

  /* ---------------- activity ---------------- */
  function loadActivity() {
    api("/api/admin/activity").then(function (d) {
      var t = $("adm-activity");
      t.innerHTML =
        '<thead><tr><th>Час</th><th>IP</th><th>Клієнт</th><th>Сторінка</th><th>UA</th><th>мс</th></tr></thead><tbody>' +
        d.activity.map(function (a) {
          return '<tr><td class="adm-cell-nowrap">' + fmtTs(a.ts * 1000) + "</td>" +
            '<td>' + esc(a.ip) + "</td>" +
            '<td>' + (a.email ? esc(a.email) : '<span class="adm-sub">анонім</span>') + "</td>" +
            '<td>' + esc(a.path) + "</td>" +
            '<td class="adm-cell-ua">' + esc(a.ua) + "</td>" +
            '<td>' + esc(a.ms != null ? a.ms : "") + "</td></tr>";
        }).join("") + "</tbody>";
    }).catch(function (e) { flash(e.message); });
  }

  /* ---------------- flash ---------------- */
  var flashTimer = 0;
  function flash(msg) {
    var el = $("adm-flash");
    if (!el) {
      el = document.createElement("div");
      el.id = "adm-flash";
      el.className = "adm-flash";
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.add("adm-flash-show");
    clearTimeout(flashTimer);
    flashTimer = setTimeout(function () { el.classList.remove("adm-flash-show"); }, 2600);
  }

  /* ---------------- boot ---------------- */
  function boot() {
    document.querySelectorAll(".adm-tab").forEach(function (b) {
      b.addEventListener("click", function () { switchTab(b.dataset.tab); });
    });
    $("adm-filter-status").addEventListener("change", loadLeads);
    $("adm-logout").addEventListener("click", function () {
      api("/api/auth/logout", { method: "POST", body: "{}" })
        .then(function () { location.href = "/"; }).catch(function () { location.href = "/"; });
    });

    api("/api/auth/me").then(function (d) {
      if (!d.ok || !d.user) { showDenied(); return; }
      USER = d.user;
      $("adm-user").textContent = "👤 " + (USER.name || USER.email);
      $("adm-app").classList.remove("hidden");
      switchTab("overview");
      POLL_HANDLE = setInterval(function () {
        api("/api/admin/chats").then(function (d) {
          var unread = d.threads.reduce(function (n, t) { return n + (t.unread_admin || 0); }, 0);
          var badge = $("adm-chat-badge");
          badge.textContent = unread;
          badge.classList.toggle("hidden", !unread);
        }).catch(function () {});
      }, 15000);
    }).catch(function (e) {
      showDenied();
    });
  }

  function showDenied() {
    $("adm-denied").classList.remove("hidden");
    $("adm-app").classList.add("hidden");
    $("adm-user").textContent = "";
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();