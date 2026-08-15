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

  /* ---------- AR gallery injection ---------- */
  var AR_ITEMS = [
    {
      title: "Інтерактивний офіс 3D",
      desc: "Віртуальний простір у реальній кімнаті",
      glb: "https://modelviewer.dev/shared-assets/models/Astronaut.glb",
      usdz: "https://modelviewer.dev/shared-assets/models/Astronaut.usdz",
      note: "прототип-модель для демонстрації AR"
    },
    {
      title: "Шоурум продукту",
      desc: "Демонстрація товару на полиці клієнта",
      glb: "https://modelviewer.dev/shared-assets/models/NeilArmstrong.glb",
      usdz: "https://modelviewer.dev/shared-assets/models/NeilArmstrong.usdz",
      note: "прототип-модель для демонстрації AR"
    },
    {
      title: "Віртуальний асистент",
      desc: "AI-персонаж, що розповідає про продукт",
      glb: "https://modelviewer.dev/shared-assets/models/RobotExpressive.glb",
      usdz: "https://modelviewer.dev/shared-assets/models/RobotExpressive.usdz",
      note: "прототип-модель для демонстрації AR"
    },
    {
      title: "Технодемо-вузол",
      desc: "Інтерактивна частина інтерфейсу",
      glb: "https://modelviewer.dev/shared-assets/models/DamagedHelmet.glb",
      usdz: "https://modelviewer.dev/shared-assets/models/DamagedHelmet.usdz",
      note: "прототип-модель для демонстрації AR"
    }
  ];

  var grid = document.getElementById("ar-grid");
  if (grid && window.customElements && customElements.get("model-viewer")) {
    grid.innerHTML = AR_ITEMS.map(function (item, i) {
      var states = ["", "d1", "d2", "d3"];
      return (
        '<div class="ar-card reveal ' + states[i % 4] + '">' +
        '<div style="position:relative">' +
        '<model-viewer src="' + item.glb + '" ios-src="' + item.usdz + '" ar ' +
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
        '<div class="ar-note mt-2">' + item.note + ' · .glb / .usdz</div>' +
        '</div>' +
        '</div>'
      );
    }).join("");
    grid.querySelectorAll(".reveal").forEach(function (el) { revealObserver.observe(el); });
  }

  /* model-viewer lazy-load: load when visible */
  if (window.customElements && customElements.get("model-viewer")) {
    var mvObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.load && e.target.load(); mvObserver.unobserve(e.target); }
      });
    }, { rootMargin: "300px" });
    document.querySelectorAll("model-viewer").forEach(function (el) { mvObserver.observe(el); });
  }

  /* ---------- lead form ---------- */
  var leadForm = document.getElementById("lead-form");
  var leadSuccess = document.getElementById("lead-success");
  if (leadForm) {
    leadForm.addEventListener("submit", function (e) {
      e.preventDefault();
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
            alert("Не вдалося відправити. Напишіть нам у Telegram: t.me/faststart_digital");
          }
        })
        .catch(function () {
          alert("Не вдалося відправити. Напишіть нам у Telegram: t.me/faststart_digital");
        })
        .finally(function () { btn.textContent = original; });
    });
  }
})();