(function () {
  "use strict";

  var T = window.FSD_T || function (s) { return s; };

  var term = document.getElementById("jobs-term");
  var btn = document.getElementById("jobs-run");
  if (!term || !btn) return;

  var SAMPLE = [
    { t: "Python Developer (AI team)", c: "GlobalLogic", s: "95 000–130 000 грн", src: "djinni" },
    { t: "ML Engineer / NLP", c: "grammarly-альтернатива-startup", s: "$3 500–5 000/mo", src: "work.ua" },
    { t: "Backend Engineer (FastAPI)", c: "finstream", s: "110 000 грн", src: "dou.ua" },
    { t: "AI Agent Developer", c: "Nova-Labs", s: "$4 000–6 000/mo", src: "remoteok" },
    { t: "Data Scientist (LLM)", c: "metrika.in", s: "від 120 000 грн", src: "djinni" },
    { t: "Python QA Automation", c: "cloudpilot", s: "70 000–90 000 грн", src: "work.ua" },
    { t: "MLOps / Infrastructure", c: "pipeline.tech", s: "від 135 000 грн", src: "djinni" },
    { t: "Junior Python / Django", c: "softserve-junior", s: "45 000–60 000 грн", src: "dou.ua" },
  ];
  var SOURCES = ["djinni", "work.ua", "dou.ua", "remoteok", "jobs.ua", "rabota.ua", "linkedin", "justjoin"];
  var BOTS = ["@t.me/candidate", "@t.me/hr_filter", "@t.me/dev_digest"];

  var cursor = null;
  var running = false;

  function ensureCursor() {
    if (cursor && cursor.parentNode === term) return;
    cursor = document.createElement("span");
    cursor.className = "t-ok";
    cursor.textContent = "\u258a";
    term.appendChild(cursor);
  }

  function line(text, cls) {
    ensureCursor();
    var div = document.createElement("div");
    div.className = cls || "";
    div.textContent = text;
    term.insertBefore(div, cursor);
    term.scrollTop = term.scrollHeight;
  }

  function delay(ms) {
    return new Promise(function (res) { setTimeout(res, ms); });
  }

  function fmt(n) {
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  }

  function run() {
    if (running) return;
    running = true;
    btn.disabled = true;
    btn.style.opacity = "0.5";
    term.innerHTML = "";
    var t0 = new Date().toTimeString().slice(0, 5);

    (async function () {
      line(t0 + " python bot.py --sources djinni,work.ua,dou.ua,remoteok", "text-slate-400");
      await delay(500);
      line(t0 + " " + T("підключення до %s джерел…", SOURCES.length), "text-slate-400");
      await delay(700);
      var est = 900 + Math.floor(Math.random() * 600);
      line(t0 + " " + T("зібрано ~%s вакансій за 38 сек", fmt(est)), "text-emerald-400");
      await delay(600);
      line(t0 + " " + T("фільтр: python/ai · досвід 1–3 р · Україна/Remote"), "text-slate-400");
      await delay(700);
      var after = Math.floor(est * (0.12 + Math.random() * 0.08));
      line(t0 + " " + T("після фільтра: %s -> дедуплікація -> %s унікальних", fmt(after), fmt(Math.floor(after * 0.85))), "text-cyan-300");
      await delay(600);
      line(t0 + " " + T("топ-кандидати:"), "");
      var picks = SAMPLE.slice().sort(function () { return Math.random() - 0.5; }).slice(0, 4);
      picks.forEach(function (j) {
        line("   \u2713 " + j.t + " · " + j.c + " · " + j.s + "  [" + j.src + "]", "");
      });
      await delay(700);
      var who = BOTS[Math.floor(Math.random() * BOTS.length)];
      line(t0 + " " + T("надіслано digest → %s (%s вакансій)", who, picks.length), "text-emerald-400");
      await delay(400);
      line(t0 + " наступний запуск: через 60 хв (cron)", "text-slate-500");
      running = false;
      btn.disabled = false;
      btn.style.opacity = "1";
    })();
  }

  btn.addEventListener("click", run);
})();