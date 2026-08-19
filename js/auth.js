/* FastStart Digital auth: Google OAuth chip + account dropdown (Projects / Sign out) + projects modal + 5% coupon. */
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

  /* ---- coupon 5% widget ---- */
  var couponEl = null;
  var couponBox = null;
  var couponClaimed = false;
  var couponDragging = false;
  var hintTimer = null;

  function initCoupon() {
    if (couponClaimed || !state.user) return;
    if (document.getElementById("coupon-box")) return;

    couponBox = document.createElement("div");
    couponBox.id = "coupon-box";
    couponBox.className = "coupon-box";

    var lid = document.createElement("div");
    lid.className = "coupon-box-lid";
    couponBox.appendChild(lid);

    couponEl = document.createElement("div");
    couponEl.id = "coupon-chip";
    couponEl.className = "coupon-chip";
    couponEl.textContent = "5%";
    couponEl.setAttribute("title", T("Перетягніть для знижки 5%"));
    couponBox.appendChild(couponEl);

    var hint = document.createElement("div");
    hint.className = "coupon-hint";
    hint.innerHTML = '<div class="coupon-hint-arrow"></div><div class="coupon-hint-text">' + T("Тягніть вниз") + "</div>";
    couponBox.appendChild(hint);

    host.appendChild(couponBox);

    setTimeout(function () {
      if (!couponBox || couponClaimed) return;
      if (window.anime) {
        anime({
          targets: lid,
          rotateX: [-90, 0],
          opacity: [0, 1],
          duration: 400,
          easing: "easeOutQuad",
        });
        anime({
          targets: couponEl,
          translateY: [20, 0],
          scale: [0.5, 1],
          opacity: [0, 1],
          duration: 500,
          delay: 200,
          easing: "easeOutBack",
        });
      }
      setTimeout(showHint, 2000);
    }, 600);

    var startX, startY, origX, origY;

    function onPointerDown(e) {
      if (couponClaimed) return;
      e.preventDefault();
      hideHint();
      couponDragging = true;

      var boxRect = couponBox.getBoundingClientRect();
      origX = boxRect.left + 3;
      origY = boxRect.top + 3;
      couponEl.style.position = "fixed";
      couponEl.style.left = origX + "px";
      couponEl.style.top = origY + "px";
      couponEl.style.width = "46px";
      couponEl.style.height = "46px";
      couponEl.style.zIndex = "700";

      startX = e.clientX;
      startY = e.clientY;

      if (window.anime) {
        anime({
          targets: lid,
          rotateX: -90,
          opacity: 0,
          duration: 300,
          easing: "easeInQuad",
        });
      }

      couponEl.classList.add("coupon-dragging");
      document.addEventListener("pointermove", onPointerMove);
      document.addEventListener("pointerup", onPointerUp);
    }

    function onPointerMove(e) {
      if (!couponDragging) return;
      e.preventDefault();
      var dx = e.clientX - startX;
      var dy = e.clientY - startY;
      couponEl.style.left = (origX + dx) + "px";
      couponEl.style.top = (origY + dy) + "px";

      var drop = document.getElementById("coupon-drop");
      if (drop) {
        var dr = drop.getBoundingClientRect();
        var cr = couponEl.getBoundingClientRect();
        var overlap = !(cr.right < dr.left || cr.left > dr.right || cr.bottom < dr.top || cr.top > dr.bottom);
        drop.classList.toggle("coupon-drop-hover", overlap);
      }
    }

    function onPointerUp(e) {
      if (!couponDragging) return;
      couponDragging = false;
      couponEl.classList.remove("coupon-dragging");
      document.removeEventListener("pointermove", onPointerMove);
      document.removeEventListener("pointerup", onPointerUp);

      var drop = document.getElementById("coupon-drop");
      if (drop) {
        var dr = drop.getBoundingClientRect();
        var cr = couponEl.getBoundingClientRect();
        var overlap = !(cr.right < dr.left || cr.left > dr.right || cr.bottom < dr.top || cr.top > dr.bottom);
        drop.classList.remove("coupon-drop-hover");
        if (overlap) {
          claimCoupon();
          return;
        }
      }

      couponEl.style.transition = "all 0.3s ease";
      couponEl.style.left = (origX) + "px";
      couponEl.style.top = (origY) + "px";
      setTimeout(function () {
        if (!couponEl) return;
        couponEl.style.transition = "";
        couponEl.style.position = "absolute";
        couponEl.style.left = "3px";
        couponEl.style.top = "3px";
        couponEl.style.zIndex = "1";
        if (window.anime) {
          anime({
            targets: lid,
            rotateX: [90, 0],
            opacity: [0, 1],
            duration: 300,
            easing: "easeOutQuad",
          });
        }
        setTimeout(showHint, 3000);
      }, 350);
    }

    couponEl.addEventListener("pointerdown", onPointerDown);
  }

  function showHint() {
    if (couponClaimed || couponDragging) return;
    var h = couponBox ? couponBox.querySelector(".coupon-hint") : null;
    if (h) h.classList.add("show");
    hintTimer = setTimeout(hideHint, 4000);
  }

  function hideHint() {
    if (hintTimer) { clearTimeout(hintTimer); hintTimer = null; }
    var h = couponBox ? couponBox.querySelector(".coupon-hint") : null;
    if (h) h.classList.remove("show");
  }

  function startHintLoop() {
    if (couponClaimed) return;
    setInterval(function () {
      if (!couponClaimed && !couponDragging) showHint();
    }, 30000);
  }

  function claimCoupon() {
    fetch("/api/coupon/claim", { method: "POST", credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.ok) {
          couponClaimed = true;
          hideHint();
          if (window.anime && couponEl) {
            anime({
              targets: couponEl,
              scale: [1, 1.3, 0],
              rotate: [0, 180],
              opacity: [1, 1, 0],
              duration: 500,
              easing: "easeInBack",
              complete: function () {
                if (couponBox && couponBox.parentNode) couponBox.parentNode.removeChild(couponBox);
                couponEl = null;
                couponBox = null;
              },
            });
          } else if (couponBox && couponBox.parentNode) {
            couponBox.parentNode.removeChild(couponBox);
            couponEl = null;
            couponBox = null;
          }
          var drop = document.getElementById("coupon-drop");
          if (drop) {
            drop.innerHTML = '<div class="coupon-drop-ok">5% ' + T("знижку активовано!") + "</div>";
          }
          showCouponBadge();
        }
      })
      .catch(function () {});
  }

  function showCouponBadge() {
    if (!state.user) return;
    var existing = box.querySelector(".coupon-badge");
    if (existing) return;
    var badge = document.createElement("span");
    badge.className = "coupon-badge";
    badge.textContent = "5%";
    badge.title = T("Знижка 5% активована");
    box.appendChild(badge);
  }

  function ensureChip() {
    var u = state.user;
    if (!u) {
      box.innerHTML = '<a class="fsd-auth-btn fsd-login" href="/auth.html">' + T("Увійти") + "</a>";
      couponClaimed = false;
      if (couponBox && couponBox.parentNode) couponBox.parentNode.removeChild(couponBox);
      couponEl = null;
      couponBox = null;
      hideHint();
      var zone = document.getElementById("coupon-zone");
      if (zone) zone.classList.add("hidden");
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
      couponClaimed = true;
      showCouponBadge();
    } else {
      setTimeout(initCoupon, 300);
      startHintLoop();
    }
  }

  function renderMenu() {
    var dev = state.projects ? state.projects.dev_count : 0;
    var badge = dev > 0
      ? '<span class="fsd-badge">' + dev + '</span>'
      : "";
    return '<div class="fsd-menu">' +
      '<button type="button" class="fsd-item" data-action="projects">' + T("Проекти") + badge + "</button>" +
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
            couponInfo = '<span class="coupon-badge" style="margin-left:8px">5%</span>';
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
              "<td>" + E(when) + "</td>" +
              "<td class='f-msg'>" + esc(p.message || "—") + "</td>" +
              "</tr>";
          }).join("");
          body.innerHTML = "<table class='fsd-leads'><thead><tr>" +
            "<th>" + T("Статус") + "</th><th>" + T("Тип") + "</th><th>" + T("Бюджет") + "</th>" +
            "<th>" + T("Час") + "</th><th>" + T("Повідомлення") + "</th></tr></thead><tbody>" +
            rows + "</tbody></table>";
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

  fetch("/api/auth/me", { credentials: "same-origin" })
    .then(function (r) { return r.json(); })
    .then(function (d) { state.user = d.ok ? d.user : null; ensureChip(); })
    .catch(function () { state.user = null; ensureChip(); });
})();