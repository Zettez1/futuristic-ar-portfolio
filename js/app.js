(function () {
  "use strict";

  /* ---------- navbar ---------- */
  var navbar = document.getElementById("navbar");
  function onScroll() {
    if (navbar) navbar.classList.toggle("header-scrolled", window.scrollY > 30);
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  /* ---------- scroll reveal ---------- */
  var revealObserver = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        entry.target.classList.add("in-view");
        revealObserver.unobserve(entry.target);
      }
    });
  }, { threshold: 0.12 });
  document.querySelectorAll(".reveal").forEach(function (el) { revealObserver.observe(el); });

  /* ---------- animated counters ---------- */
  var counterObserver = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      var el = entry.target;
      counterObserver.unobserve(el);
      var target = parseInt(el.getAttribute("data-count"), 10);
      var suffix = el.getAttribute("data-suffix") || "";
      var start = performance.now();
      var dur = 1600;
      function tick(now) {
        var p = Math.min((now - start) / dur, 1);
        var eased = 1 - Math.pow(1 - p, 3);
        el.textContent = Math.round(target * eased) + suffix;
        if (p < 1) requestAnimationFrame(tick);
      }
      requestAnimationFrame(tick);
    });
  }, { threshold: 0.5 });
  document.querySelectorAll(".stat-num[data-count]").forEach(function (el) {
    counterObserver.observe(el);
  });

  /* ---------- AR gallery (business models, lazy model-viewer) ---------- */
  var AR_ITEMS = [
    {
      title: "Кросівок 1:1",
      desc: "AR-перегляд товару для інтернет-магазину",
      glb: "/models/sneaker.glb?v=3",
      usdz: "/models/sneaker.usdz?v=3",
      note: "кросівки · e-commerce",
      scale: "1 1 1"
    },
    {
      title: "Блюдо для кафе",
      desc: "Страва доповнює сервірування столу",
      glb: "/models/avocado.glb?v=3",
      usdz: "/models/avocado.usdz?v=3",
      note: "ресторани · доставка їжі",
      scale: "1 1 1"
    },
    {
      title: "Диван для салону",
      desc: "Меблі в реальному масштабі перед покупкою",
      glb: "/models/sofa.glb?v=3",
      usdz: "/models/sofa.usdz?v=3",
      note: "меблі · інтер'єрні студії",
      scale: "1 1 1"
    },
    {
      title: "Пляшка води",
      desc: "Продукт у реальному розмірі на полиці",
      glb: "/models/bottle.glb?v=3",
      usdz: "/models/bottle.usdz?v=3",
      note: "напої · FMCG-магазини",
      scale: "1 1 1"
    }
  ];

  var grid = document.getElementById("ar-grid");
  var mvReady = function () {
    return window.customElements && customElements.get("model-viewer");
  };

  /* AR without any app installs: WebXR (Chrome/Edge Android), Quick Look (iPhone) */
  function supportsWebXR() {
    return !!(window.navigator && navigator.xr && navigator.xr.isSessionSupported);
  }

  /* Scanner models are often fully metallic (metallic=1) and look gray/black
     in AR with neutral lighting. Flatten them to matte so colors show. */
  function flattenMaterials(mv) {
    try {
      var mats = mv.model && mv.model.materials;
      if (!mats) return;
      for (var i = 0; i < mats.length; i++) {
        var p = mats[i].pbrMetallicRoughness;
        if (p) {
          p.setMetallicFactor(0.15);
          p.setRoughnessFactor(0.85);
        }
      }
    } catch (e) { /* best effort */ }
  }

  function handleArClick(item, mv) {
    var hint = document.getElementById("ar-hint");
    var showHint = function (msg) {
      var box = hint ? hint.querySelector(".ar-hint-box") : null;
      if (box && msg) box.innerHTML = msg;
      if (hint) {
        hint.classList.remove("hidden");
        hint.scrollIntoView({ behavior: "smooth", block: "center" });
        setTimeout(function () { hint.classList.add("hidden"); }, 8000);
      }
    };
    if (mv) {
      try {
        if (mv.canActivateAR) { mv.activateAR(); return; }
      } catch (e) { /* fall through to platform paths */ }
      /* iOS fallback: <a rel="ar"> opens AR Quick Look in-place (needs correct usdz MIME).
         Chrome/Edge/Firefox on iOS can't Quick Look — they would download the file,
         so guide the user to Safari instead. */
      if (/iPad|iPhone|iPod/.test(navigator.userAgent) && item.usdz) {
        if (/CriOS|EdgiOS|FxiOS/.test(navigator.userAgent)) {
          showHint('<svg class="w-5 h-5 text-cyan-400 shrink-0" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M13.5 6H5a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2V9.5m-8 7L21 3m0 0h-6m6 0v6"/></svg><span>AR-режим на iPhone працює в Safari: відкрийте цю сторінку у Safari та натисніть «Відкрити в AR» ще раз. Нічого встановлювати не потрібно.</span>');
          return;
        }
        var a = document.createElement("a");
        a.href = location.origin + item.usdz;
        a.rel = "ar";
        a.style.display = "none";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        return;
      }
    }
    if (supportsWebXR()) {
      showHint('<svg class="w-5 h-5 text-violet-400 shrink-0" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v4m0 4h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"/></svg><span>Цей браузер підтримує AR-простір: натисніть «Дивитись у AR» вдруге на смартфоні. Нічого встановлювати не потрібно — режим працює прямо в Chrome/Safari.</span>');
      return;
    }
    showHint('<svg class="w-5 h-5 text-violet-400 shrink-0" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v4m0 4h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"/></svg><span>AR-режим працює зі смартфона і не потребує встановлення застосунків: відкрийте цей сайт на телефоні в Chrome або Safari та натисніть «Дивитись у AR».</span>');
  }

  function injectArButtons(items) {
    grid.querySelectorAll(".ar-card").forEach(function (card, i) {
      var item = items[i];
      if (!item) return;
      var btn = card.querySelector(".ar-cta-btn");
      var mv = card.querySelector("model-viewer");
      if (!btn) return;
      btn.addEventListener("click", function () {
        handleArClick(item, mv);
      });
      if (mv) {
        mv.addEventListener("load", function () { flattenMaterials(mv); });
        mv.addEventListener("error", function () {
          var holder = card.querySelector(".ar-model-holder");
          if (holder) {
            holder.innerHTML = '<div class="ar-model-fallback">' +
              '<div class="font-display text-white text-sm">' + item.title + '</div>' +
              '<div class="text-xs text-slate-400 mt-1">3D-модель у цьому форматі недоступна. Ми підготуємо вашу власну — замовте Web3D-розробку.</div>' +
              '</div>';
          }
        });
      }
    });
  }

  function renderARGrid() {
    if (!grid || grid.childElementCount) return;
    grid.innerHTML = AR_ITEMS.map(function (item, i) {
      var states = ["", "d1", "d2", "d3"];
      var ios = item.usdz ? ' ios-src="' + item.usdz + '"' : "";
      var formats = item.usdz ? ".glb / .usdz" : ".glb";
return (
        '<div class="ar-card reveal ' + states[i % states.length] + '">' +
        '<div class="ar-model-holder" style="position:relative">' +
        '<model-viewer src="' + item.glb + '"' + ios + ' ar ' +
        'ar-modes="webxr scene-viewer quick-look" camera-controls auto-rotate ' +
        'scale="' + (item.scale || "1 1 1") + '" ' +
        'shadow-intensity="1.1" environment-image="neutral" tone-mapping="aces" ' +
        'style="width:100%;height:260px" alt="' + item.title + '">' +
        '<button slot="ar-button" class="ar-btn">' +
        '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
        '<path d="M12 2l8.5 4.9v9.8L12 21.6l-8.5-4.9V6.9L12 2zm0 0v9.8m8.5-4.9L12 11.8 3.5 6.9"/></svg> ' +
        'Дивитись у AR' +
        '</button>' +
        '</model-viewer>' +
        '</div>' +
        '<button type="button" class="ar-cta-btn">' +
        '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
        '<path d="M12 2l8.5 4.9v9.8L12 21.6l-8.5-4.9V6.9L12 2zm0 0v9.8m8.5-4.9L12 11.8 3.5 6.9"/></svg> ' +
        'Дивитись у AR' +
        '</button>' +
        '<div class="ar-info">' +
        '<div class="font-display font-semibold text-white">' + item.title + '</div>' +
        '<div class="text-xs text-slate-400 mt-0.5">' + item.desc + '</div>' +
        '<div class="ar-note mt-2">' + item.note + ' · ' + formats + '</div>' +
        '</div>' +
        '</div>'
      );
    }).join("");

    var mvObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.load && e.target.load(); mvObserver.unobserve(e.target); }
      });
    }, { rootMargin: "300px" });
    grid.querySelectorAll("model-viewer").forEach(function (el) { mvObserver.observe(el); });
    grid.querySelectorAll(".reveal").forEach(function (el) { revealObserver.observe(el); });
    injectArButtons(AR_ITEMS);
  }

  /* lazy-load model-viewer (~150KB) only when AR section is near viewport */
  var MV_URL = "https://unpkg.com/@google/model-viewer@3.5.0/dist/model-viewer.min.js";
  var arSection = document.getElementById("ar");
  var mvLoaded = false;
  function ensureModelViewer() {
    if (mvReady()) {
      renderARGrid();
      return;
    }
    if (mvLoaded) return;
    mvLoaded = true;
    var s = document.createElement("script");
    s.type = "module";
    s.src = MV_URL;
    s.onload = function () {
      if (customElements && customElements.whenDefined) {
        customElements.whenDefined("model-viewer").then(renderARGrid);
      } else {
        renderARGrid();
      }
    };
    s.onerror = function () {
      if (grid) grid.innerHTML = '<p class="text-sm text-slate-500">3D-модуль не завантажився. Спробуйте пізніше або напишіть нам у Telegram.</p>';
    };
    document.head.appendChild(s);
  }
  if (arSection) {
    var arObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { ensureModelViewer(); arObserver.unobserve(e.target); }
      });
    }, { rootMargin: "400px" });
    arObserver.observe(arSection);
  }
  if (mvReady()) renderARGrid();

  /* ---------- lead form ---------- */
  var leadForm = document.getElementById("lead-form");
  var leadSuccess = document.getElementById("lead-success");
  var leadError = document.getElementById("lead-error");
  if (leadForm) {
    function showError(msg) {
      if (!leadError) return;
      leadError.textContent = msg;
      leadError.classList.remove("hidden");
    }
    leadForm.addEventListener("submit", function (e) {
      e.preventDefault();
      leadError && leadError.classList.add("hidden");
      var fd = new FormData(leadForm);
      var payload = {
        name: fd.get("name"),
        contact: fd.get("contact"),
        type: fd.get("type"),
        budget: fd.get("budget"),
        message: fd.get("message"),
        source: "lead-form",
        page: location.pathname,
        ts: new Date().toISOString()
      };
      var btn = leadForm.querySelector("button[type=submit]");
      var original = btn.textContent;
      btn.textContent = "Відправлення…";

      fetch("/api/lead", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          if (res.ok) {
            leadSuccess.classList.remove("hidden");
            leadForm.querySelectorAll("input,textarea").forEach(function (i) { i.value = ""; });
          } else {
            showError("Не вдалося відправити. Напишіть нам у Telegram: t.me/faststart_digital");
          }
        })
        .catch(function () {
          showError("Не вдалося відправити. Спробуйте ще раз або напишіть у Telegram: t.me/faststart_digital");
        })
        .finally(function () { btn.textContent = original; });
    });
  }
})();