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

  /* ---------- AR gallery (self-hosted models, lazy model-viewer) ---------- */
  var AR_ITEMS = [
    {
      title: "Інтерактивний офіс 3D",
      desc: "Віртуальний простір у реальній кімнаті",
      glb: "/models/astronaut.glb",
      usdz: "/models/astronaut.usdz",
      note: "прототип-модель для демонстрації AR"
    },
    {
      title: "Шоурум продукту",
      desc: "Демонстрація товару на полиці клієнта",
      glb: "/models/toycar.glb",
      usdz: "",
      note: "прототип-модель для демонстрації AR"
    },
    {
      title: "Віртуальний асистент",
      desc: "AI-персонаж, що розповідає про продукт",
      glb: "/models/robot.glb",
      usdz: "",
      note: "прототип-модель для демонстрації AR"
    },
    {
      title: "Технодемо-вузол",
      desc: "Інтерактивна частина інтерфейсу",
      glb: "/models/helmet.glb",
      usdz: "",
      note: "прототип-модель для демонстрації AR"
    }
  ];

  var grid = document.getElementById("ar-grid");
  var mvReady = function () {
    return window.customElements && customElements.get("model-viewer");
  };

  function renderARGrid() {
    if (!grid || grid.childElementCount) return;
    grid.innerHTML = AR_ITEMS.map(function (item, i) {
      var states = ["", "d1", "d2", "d3"];
      var ios = item.usdz ? ' ios-src="' + item.usdz + '"' : "";
      var formats = item.usdz ? ".glb / .usdz" : ".glb";
      return (
        '<div class="ar-card reveal ' + states[i % 4] + '">' +
        '<div style="position:relative">' +
        '<model-viewer src="' + item.glb + '"' + ios + ' ar ' +
        'ar-modes="webxr scene-viewer quick-look" camera-controls auto-rotate ' +
        'shadow-intensity="1.1" environment-image="neutral" tone-mapping="aces" ' +
        'style="width:100%;height:260px" alt="' + item.title + '">' +
        '<button slot="ar-button" class="ar-btn">' +
        '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
        '<path d="M12 2l8.5 4.9v9.8L12 21.6l-8.5-4.9V6.9L12 2zm0 0v9.8m8.5-4.9L12 11.8 3.5 6.9"/></svg> ' +
        'Дивитись у AR' +
        '</button>' +
        '</model-viewer>' +
        '</div>' +
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