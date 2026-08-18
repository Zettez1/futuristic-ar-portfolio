/* FastStart Digital i18n engine — 6 languages (uk/en/ru/sk/pl/de).
   Data lives in i18n-a/b/c.js (window.FSD_I18N_DICT). Load data files FIRST.
   Exposes: FSD_T(ua, ...args), FSD_CUR(), FSD_I18N_APPLY(lang). */
(function () {
  "use strict";

  var LANGS = ["uk", "en", "ru", "sk", "pl", "de"];
  var STORE = "fsd_lang";
  var cur = "uk";

  var META = {
    uk: { title: "FastStart Digital — Веб-розробка · Web3D/WebAR · AI-Агенти", desc: "Перетворюємо ідеї на інтерактивний та цифровий код: швидкі веб-сайти, Web3D/WebAR-візуалізація у 1 клік, AI-агенти та Python-автоматизація для бізнесу. Розрахунок вартості 24/7." },
    en: { title: "FastStart Digital — Web Development · Web3D/WebAR · AI Agents", desc: "We turn ideas into interactive and digital code: fast websites, one-click Web3D/WebAR visualization, AI agents and Python automation for business. Cost estimate 24/7." },
    ru: { title: "FastStart Digital — Веб-разработка · Web3D/WebAR · ИИ-агенты", desc: "Превращаем идеи в интерактивный и цифровой код: быстрые веб-сайты, Web3D/WebAR-визуализация в 1 клик, ИИ-агенты и Python-автоматизация для бизнеса. Расчёт стоимости 24/7." },
    sk: { title: "FastStart Digital — webový vývoj · Web3D/WebAR · AI agenti", desc: "Premieňame nápady na interaktívny a digitálny kód: rýchle webové stránky, Web3D/WebAR vizualizácia na 1 klik, AI agenti a Python automatizácia pre biznis. Kalkulácia 24/7." },
    pl: { title: "FastStart Digital — tworzenie stron · Web3D/WebAR · agenci AI", desc: "Zmieniamy pomysły w interaktywny i cyfrowy kod: szybkie strony, wizualizacja Web3D/WebAR w 1 klik, agenci AI i automatyzacja Python dla biznesu. Wycena 24/7." },
    de: { title: "FastStart Digital — Webentwicklung · Web3D/WebAR · KI-Agenten", desc: "Wir verwandeln Ideen in interaktiven und digitalen Code: schnelle Websites, Web3D/WebAR-Visualisierung mit 1 Klick, KI-Agenten und Python-Automatisierung für Unternehmen. Kostenkalkulation 24/7." }
  };

  var PRIVACY = {
    uk: { title: "Політика конфіденційності — FastStart Digital", desc: "Політика конфіденційності FastStart Digital: які дані ми збираємо, як їх обробляємо та зберігаємо. Відповідність GDPR та Закону України «Про захист персональних даних»." },
    en: { title: "Privacy Policy — FastStart Digital", desc: "FastStart Digital Privacy Policy: what data we collect, how we process and store it. Compliance with GDPR and the Law of Ukraine on Personal Data Protection." },
    ru: { title: "Политика конфиденциальности — FastStart Digital", desc: "Политика конфиденциальности FastStart Digital: какие данные мы собираем, как их обрабатываем и храним. Соответствие GDPR и Закону Украины «О защите персональных данных»." },
    sk: { title: "Zásady ochrany osobných údajov — FastStart Digital", desc: "Zásady ochrany osobných údajov FastStart Digital: aké údaje zbierame, ako ich spracúvame a uchovávame. Súlad s GDPR a zákonom Ukrajiny o ochrane osobných údajov." },
    pl: { title: "Polityka prywatności — FastStart Digital", desc: "Polityka prywatności FastStart Digital: jakie dane zbieramy, jak je przetwarzamy i przechowujemy. Zgodność z RODO i ustawą Ukrainy o ochronie danych osobowych." },
    de: { title: "Datenschutzerklärung — FastStart Digital", desc: "Datenschutzerklärung FastStart Digital: welche Daten wir erheben, wie wir sie verarbeiten und speichern. Einhaltung von DSGVO und dem ukrainischen Gesetz zum Schutz personenbezogener Daten." }
  };

  function norm(s) { return String(s).replace(/\s+/g, " ").trim(); }
  function dict() { return window.FSD_I18N_DICT || {}; }

  /* originals: keep UA texts so re-translation works after the DOM has been translated */
  var nodeOrig = typeof WeakMap !== "undefined" ? new WeakMap() : null;
  var elOrig = {};
  var lastPH = {};
  var lastAR = {};
  function origFor(el, kind) {
    if (el.dataset && !el.dataset.fsdKey) { el.dataset.fsdKey = String(elOrig._n === undefined ? (elOrig._n = 0) : ++elOrig._n); }
    var k = kind + "|" + el.dataset.fsdKey;
    var attr = kind === "ph" ? "placeholder" : "aria-label";
    var cur = el.getAttribute(attr) || "";
    var lastMap = kind === "ph" ? lastPH : lastAR;
    var orig = elOrig[k];
    if (orig === undefined || (lastMap[k] !== undefined && cur !== lastMap[k])) {
      orig = cur;
      elOrig[k] = orig;
    }
    return orig;
  }

  function detect() {
    try {
      var s = localStorage.getItem(STORE);
      if (s && LANGS.indexOf(s) >= 0) return s;
    } catch (e) {}
    try {
      var n = (navigator.language || "uk").toLowerCase().split("-")[0];
      if (LANGS.indexOf(n) >= 0) return n;
      if (n === "be") return "ru";
    } catch (e) {}
    return "uk";
  }

  function t() {
    var ua = arguments[0];
    var args = Array.prototype.slice.call(arguments, 1);
    var e = dict()[norm(ua)];
    var s = e && e[cur] ? e[cur] : ua;
    if (args.length) {
      var i = 0;
      s = s.replace(/%s/g, function () { return args[i++]; });
    }
    return s;
  }

  function setMeta(sel, content) {
    var el = document.querySelector(sel);
    if (el) el.setAttribute("content", content);
  }

  function applyMeta(lang) {
    var isPrivacy = location.pathname.indexOf("privacy") >= 0;
    var m = isPrivacy ? PRIVACY[lang] : META[lang];
    if (!m) return;
    document.title = m.title;
    setMeta('meta[name="description"]', m.desc);
    setMeta('meta[property="og:title"]', m.title);
    setMeta('meta[property="og:description"]', m.desc);
    setMeta('meta[name="twitter:title"]', m.title);
    setMeta('meta[name="twitter:description"]', m.desc);
  }

  function translateDom(lang) {
    var D = dict();
    var nodes = [];
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        var p = n.parentNode;
        if (!p) return NodeFilter.FILTER_REJECT;
        var tag = p.nodeName;
        if (tag === "SCRIPT" || tag === "STYLE" || tag === "NOSCRIPT" || tag === "TEXTAREA") return NodeFilter.FILTER_REJECT;
        if (p.nodeType === 1 && p.hasAttribute && p.hasAttribute("data-i18n-skip")) return NodeFilter.FILTER_REJECT;
        if (!n.nodeValue || !n.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(function (n) {
      var origText = nodeOrig ? nodeOrig.get(n) : undefined;
      if (origText === undefined) { origText = n.nodeValue; if (nodeOrig) nodeOrig.set(n, origText); }
      var lead = (origText.match(/^\s*/) || [""])[0];
      var tail = (origText.match(/\s*$/) || [""])[0];
      var e = D[norm(origText)];
      if (e && e[lang]) n.nodeValue = lead + e[lang] + tail;
    });
    document.querySelectorAll("[placeholder]").forEach(function (el) {
      var e = D[norm(origFor(el, "ph"))];
      if (e && e[lang]) { el.setAttribute("placeholder", e[lang]); lastPH[el.dataset.fsdKey] = e[lang]; }
    });
    document.querySelectorAll("[aria-label]").forEach(function (el) {
      var e = D[norm(origFor(el, "ar"))];
      if (e && e[lang]) { el.setAttribute("aria-label", e[lang]); lastAR[el.dataset.fsdKey] = e[lang]; }
    });
  }

  function buildSwitcher() {
    if (document.getElementById("fsd-lang-switch")) return;
    var host = document.querySelector("[data-lang-host]") ||
      document.querySelector("#navbar .flex.items-center.gap-3");
    if (!host) return;
    var sel = document.createElement("select");
    sel.id = "fsd-lang-switch";
    sel.className = "lang-switch";
    sel.setAttribute("aria-label", "Language / Мова");
    LANGS.forEach(function (l) {
      var o = document.createElement("option");
      o.value = l;
      o.textContent = l.toUpperCase();
      if (l === cur) o.selected = true;
      sel.appendChild(o);
    });
    sel.addEventListener("change", function () { applyI18n(sel.value); });
    host.insertBefore(sel, host.firstChild);
  }

  function applyI18n(lang) {
    cur = LANGS.indexOf(lang) >= 0 ? lang : "uk";
    try { localStorage.setItem(STORE, cur); } catch (e) {}
    document.documentElement.setAttribute("lang", cur);
    applyMeta(cur);
    buildSwitcher();
    translateDom(cur);
    try {
      var ev = new CustomEvent("fsd:lang", { detail: { lang: cur }, bubbles: true });
      document.dispatchEvent(ev);
      window.dispatchEvent(ev);
    } catch (e) {}
  }

  function init() {
    cur = detect();
    applyI18n(cur);
  }

  window.FSD_T = t;
  window.FSD_CUR = function () { return cur; };
  window.FSD_I18N_APPLY = applyI18n;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();