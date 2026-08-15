(function () {
  "use strict";

  var root = document.getElementById("chat-root");
  if (!root) return;

  var PLACEHOLDER = "РќР°РїРёС€С–С‚СЊ РїРѕРІС–РґРѕРјР»РµРЅРЅСЏвЂ¦";
  var state = { step: 0, projectType: null, budget: null, name: null, contact: null, input: null, custom: null };

  var ICON_CHAT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.4 8.4 0 01-9 8.4 8.6 8.6 0 01-3.4-.7L3 21l1.8-5.6A8.4 8.4 0 0121 11.5z"/></svg>';
  var ICON_SEND = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg>';
  var ICON_CLOSE = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>';

  function el(html) {
    var d = document.createElement("div");
    d.innerHTML = html.trim();
    return d.firstChild;
  }

  /* ---------------- render shell ---------------- */
  root.innerHTML =
    '<button class="chat-fab" id="chat-fab" aria-label="Р’С–РґРєСЂРёС‚Рё AI-С‡Р°С‚">' + ICON_CHAT +
    '<span class="chat-badge">1</span></button>' +
    '<div class="chat-panel hidden" id="chat-panel" role="dialog" aria-label="AI-Р°РіРµРЅС‚ FastStart Digital">' +
    '<div class="chat-head">' +
    '<div class="chat-avatar">F</div>' +
    '<div><div class="font-display font-semibold text-white text-sm">NOVA В· AI-Р°РіРµРЅС‚ FastStart Digital</div>' +
    '<div class="chat-online">online В· РІС–РґРїРѕРІС–РґР°С” РјРёС‚С‚С”РІРѕ</div></div>' +
    '<button class="chat-close" id="chat-close" aria-label="Р—Р°РєСЂРёС‚Рё">' + ICON_CLOSE + '</button>' +
    '</div>' +
    '<div class="chat-body" id="chat-body"></div>' +
    '<div class="chat-chips" id="chat-chips"></div>' +
    '<form class="chat-input" id="chat-form">' +
    '<input id="chat-text" type="text" autocomplete="off" placeholder="' + PLACEHOLDER + '" />' +
    '<button class="chat-send" type="submit">' + ICON_SEND + '</button>' +
    '</form>' +
    '<div class="chat-note">AI-Р°РіРµРЅС‚ Р·Р±РёСЂР°С” РєРѕРЅС‚Р°РєС‚Рё РґР»СЏ СЂРѕР·СЂР°С…СѓРЅРєСѓ РљРџ В· t.me/faststart_digital</div>' +
    '</div>';

  var fab = document.getElementById("chat-fab");
  var panel = document.getElementById("chat-panel");
  var close = document.getElementById("chat-close");
  var body = document.getElementById("chat-body");
  var chips = document.getElementById("chat-chips");
  var form = document.getElementById("chat-form");
  var input = document.getElementById("chat-text");
  var badge = fab.querySelector(".chat-badge");

  fab.addEventListener("click", function () {
    panel.classList.toggle("hidden");
    badge.style.display = "none";
    if (!panel.classList.contains("hidden")) {
      setTimeout(function () { input.focus(); }, 100);
      if (body.children.length === 0) start();
    }
  });
  close.addEventListener("click", function () { panel.classList.add("hidden"); });

  /* ---------------- message helpers ---------------- */
  function addMsg(text, who) {
    var m = el('<div class="msg msg-' + who + '">' + text + "</div>");
    body.appendChild(m);
    body.scrollTop = body.scrollHeight;
    return m;
  }

  function typing(cb) {
    var t = el('<div class="msg msg-bot msg-typing"><span></span><span></span><span></span></div>');
    body.appendChild(t);
    body.scrollTop = body.scrollHeight;
    setTimeout(function () { t.remove(); cb(); }, 650 + Math.random() * 450);
  }

  function setChips(items) {
    chips.innerHTML = "";
    items.forEach(function (label) {
      var c = el('<button class="chat-chip" type="button">' + label + "</button>");
      c.addEventListener("click", function () { handleInput(label); });
      chips.appendChild(c);
    });
  }

  function clearChips() { chips.innerHTML = ""; }

  /* ---------------- flow ---------------- */
  function budgetGuess(type) {
    if (type === "Р’РµР±-СЂРѕР·СЂРѕР±РєР°") return "12 000 вЂ“ 60 000 РіСЂРЅ";
    if (type === "3D / WebAR-РІС–Р·СѓР°Р»С–Р·Р°С†С–СЏ") return "6 000 вЂ“ 25 000 РіСЂРЅ";
    if (type === "AI-Р°РіРµРЅС‚ / Р°РІС‚РѕРјР°С‚РёР·Р°С†С–СЏ") return "20 000 вЂ“ 120 000 РіСЂРЅ";
    return "8 000 вЂ“ 45 000 РіСЂРЅ";
  }

  function start() {
    typing(function () {
      addMsg("РџСЂРёРІС–С‚! РЇ <b>NOVA</b> вЂ” AI-Р°РіРµРЅС‚ FastStart Digital. Р”РѕРїРѕРјРѕР¶Сѓ РѕС†С–РЅРёС‚Рё РІР°СЂС‚С–СЃС‚СЊ РїСЂРѕС”РєС‚Сѓ С‚Р° РїС–РґР±РµСЂСѓ С„РѕСЂРјР°С‚ Р·Р° 30 СЃРµРєСѓРЅРґ.", "bot");
      setTimeout(function () {
        addMsg("Р©Рѕ РІР°СЃ С†С–РєР°РІРёС‚СЊ?", "bot");
        setChips(["Р’РµР±-СЂРѕР·СЂРѕР±РєР°", "3D / WebAR-РІС–Р·СѓР°Р»С–Р·Р°С†С–СЏ", "AI-Р°РіРµРЅС‚ / Р°РІС‚РѕРјР°С‚РёР·Р°С†С–СЏ", "РљРѕРјРїР»РµРєСЃРЅРёР№ РїСЂРѕС”РєС‚"]);
      }, 350);
    });
  }

  function handleInput(text) {
    if (!text || !text.trim()) return;
    input.value = "";
    addMsg(text, "user");
    clearChips();

    var t = text.trim().toLowerCase();

    if (state.step === 0) {
      var map = [
        { k: "СЃР°Р№С‚", v: "Р’РµР±-СЂРѕР·СЂРѕР±РєР°" },
        { k: "Р»РµРЅРґС–РЅРі", v: "Р’РµР±-СЂРѕР·СЂРѕР±РєР°" },
        { k: "РІРµР±", v: "Р’РµР±-СЂРѕР·СЂРѕР±РєР°" },
        { k: "РјР°РіР°Р·РёРЅ", v: "Р’РµР±-СЂРѕР·СЂРѕР±РєР°" },
        { k: "Р·Р°СЃС‚РѕСЃСѓРЅРѕРє", v: "Р’РµР±-СЂРѕР·СЂРѕР±РєР°" },
        { k: "3d", v: "3D / WebAR-РІС–Р·СѓР°Р»С–Р·Р°С†С–СЏ" },
        { k: "webar", v: "3D / WebAR-РІС–Р·СѓР°Р»С–Р·Р°С†С–СЏ" },
        { k: "РІС–Р·СѓР°Р»С–Р·Р°С†", v: "3D / WebAR-РІС–Р·СѓР°Р»С–Р·Р°С†С–СЏ" },
        { k: "РјРѕРґРµР»", v: "3D / WebAR-РІС–Р·СѓР°Р»С–Р·Р°С†С–СЏ" },
        { k: "ai", v: "AI-Р°РіРµРЅС‚ / Р°РІС‚РѕРјР°С‚РёР·Р°С†С–СЏ" },
        { k: "Р°РіРµРЅС‚", v: "AI-Р°РіРµРЅС‚ / Р°РІС‚РѕРјР°С‚РёР·Р°С†С–СЏ" },
        { k: "Р°РІС‚РѕРјР°С‚РёР·Р°С†", v: "AI-Р°РіРµРЅС‚ / Р°РІС‚РѕРјР°С‚РёР·Р°С†С–СЏ" },
        { k: "Р±РѕС‚", v: "AI-Р°РіРµРЅС‚ / Р°РІС‚РѕРјР°С‚РёР·Р°С†С–СЏ" },
        { k: "С‡Р°С‚", v: "AI-Р°РіРµРЅС‚ / Р°РІС‚РѕРјР°С‚РёР·Р°С†С–СЏ" },
        { k: "РєРѕРјРїР»РµРєСЃРЅ", v: "РљРѕРјРїР»РµРєСЃРЅРёР№ РїСЂРѕС”РєС‚" }
      ];
      var matched = map.find(function (m) { return t.indexOf(m.k) !== -1; });
      if (matched) {
        state.step++;
        state.projectType = matched.v;
        typing(function () {
          addMsg("Р§СѓРґРѕРІРѕ! РўРѕРґС– СЃРµСЂРµРґРЅС–Р№ С‡РµРє РґР»СЏ В«" + state.projectType + "В» вЂ” <b>" + budgetGuess(state.projectType) + "</b> Р±РµР· СѓСЂР°С…СѓРІР°РЅРЅСЏ РјР°С‚РµСЂС–Р°Р»С–РІ.", "bot");
          setTimeout(function () {
            addMsg("РЇРєРёР№ РѕСЂС–С”РЅС‚РѕРІРЅРёР№ Р±СЋРґР¶РµС‚ Р·Р°РєР»Р°РґР°С”С‚Рµ?", "bot");
            setChips(["РґРѕ 15 000", "15 000 вЂ“ 50 000", "50 000 вЂ“ 150 000", "РІС–Рґ 150 000", "РџРѕРєРё РЅРµ Р·РЅР°СЋ"]);
          }, 350);
        });
      } else {
        typing(function () {
          addMsg("Р РѕР·СѓРјС–СЋ: В«" + text + "В». РћРїРёС€Сѓ РІР°С€ РєРµР№СЃ Сѓ РљРџ вЂ” Р°Р»Рµ СЃРїРµСЂС€Сѓ СѓС‚РѕС‡РЅСЋ РїР°СЂСѓ РјРѕРјРµРЅС‚С–РІ, С‰РѕР± СЂРѕР·СЂР°С…СѓРЅРѕРє Р±СѓРІ С‚РѕС‡РЅРёРј. Р©Рѕ РІР°СЃ С†С–РєР°РІРёС‚СЊ?", "bot");
          setChips(["Р’РµР±-СЂРѕР·СЂРѕР±РєР°", "3D / WebAR-РІС–Р·СѓР°Р»С–Р·Р°С†С–СЏ", "AI-Р°РіРµРЅС‚ / Р°РІС‚РѕРјР°С‚РёР·Р°С†С–СЏ"]);
        });
      }
      return;
    }

    if (state.step === 1) {
      state.step++;
      state.budget = text;
      typing(function () {
        addMsg("Р—Р°С„С–РєСЃСѓРІР°Р»Р°: Р±СЋРґР¶РµС‚ <b>" + text + "</b>. Р—СЂРѕР±Р»СЋ РїРѕРїРµСЂРµРґРЅС–Р№ СЂРѕР·СЂР°С…СѓРЅРѕРє С– РїС–РґРіРѕС‚СѓСЋ РљРџ.", "bot");
        setTimeout(function () {
          addMsg("РЇРє РґРѕ РІР°СЃ Р·РІРµСЂС‚Р°С‚РёСЃСЊ?", "bot");
        }, 350);
      });
      return;
    }

    if (state.step === 2) {
      state.step++;
      state.name = text;
      typing(function () {
        addMsg("РџСЂРёС”РјРЅРѕ РїРѕР·РЅР°Р№РѕРјРёС‚РёСЃСЊ, <b>" + text + "</b>! РћСЃС‚Р°РЅРЅС–Р№ РєСЂРѕРє:", "bot");
        setTimeout(function () {
          addMsg("Р—Р°Р»РёС€С‚Рµ РєРѕРЅС‚Р°РєС‚ вЂ” Telegram Р°Р±Рѕ С‚РµР»РµС„РѕРЅ, С‰РѕР± С–РЅР¶РµРЅРµСЂ РЅР°РґС–СЃР»Р°РІ РІР°Рј РљРџ:", "bot");
        }, 350);
      });
      return;
    }

    if (state.step === 3) {
      state.contact = text;
      state.step++;
      sendLead();
      return;
    }

    /* post-completion free chat */
    typing(function () {
      addMsg("Р—Р°РїРёСЃР°Р»Р° РІР°С€Рµ РїРѕРІС–РґРѕРјР»РµРЅРЅСЏ. Р†РЅР¶РµРЅРµСЂ РІС–РґРїРѕРІС–СЃС‚СЊ РЅР° РЅСЊРѕРіРѕ СЂР°Р·РѕРј С–Р· РљРџ. Р„ С‰Рµ РїРёС‚Р°РЅРЅСЏ?", "bot");
      setChips(["РўР°Рє, С‰Рµ РїРёС‚Р°РЅРЅСЏ", "Р”СЏРєСѓСЋ, С‡РµРєР°СЋ РљРџ"]);
    });
    saveLead({ message: text, source: "chat-followup" });
  }

  function sendLead() {
    typing(function () {
      addMsg("Р”СЏРєСѓСЋ, " + state.name + "! Р’Р°С€Р° Р·Р°СЏРІРєР° РїСЂРёР№РЅСЏС‚Р°. Р†РЅР¶РµРЅРµСЂ РЅР°РґС–С€Р»Рµ СЂРѕР·СЂР°С…СѓРЅРѕРє С‚Р° РљРџ РЅР° <b>" + state.contact + "</b> РїСЂРѕС‚СЏРіРѕРј 24 РіРѕРґРёРЅ.", "bot");
      setTimeout(function () {
        addMsg("РџРѕРєРё С‡РµРєР°С”С‚Рµ вЂ” РІС–РґРєСЂРёР№С‚Рµ WebAR-РіР°Р»РµСЂРµСЋ РІРёС‰Рµ, С‰РѕР± РїРѕР±Р°С‡РёС‚Рё, СЏРє РІРёРіР»СЏРґР°С” РїСЂРѕС”РєС‚ Сѓ СЂРµР°Р»СЊРЅРѕРјСѓ РїСЂРѕСЃС‚РѕСЂС–.", "bot");
        setChips(["Р’С–РґРєСЂРёС‚Рё AR-РіР°Р»РµСЂРµСЋ", "РџРѕС‡РёС‚Р°С‚Рё РїСЂРѕ РїРѕСЃР»СѓРіРё"]);
      }, 400);
    });
    saveLead({
      name: state.name,
      contact: state.contact,
      type: state.projectType,
      budget: state.budget,
      source: "ai-chat",
      page: location.pathname
    });
  }

  function saveLead(payload) {
    payload = payload || {};
    payload.ts = new Date().toISOString();
    fetch("/api/lead", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).catch(function () {
      /* offline fallback: keep in localStorage */
      try {
        var ls = JSON.parse(localStorage.getItem("faststart_leads") || "[]");
        ls.push(payload);
        localStorage.setItem("faststart_leads", JSON.stringify(ls));
      } catch (e) {}
    });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var v = input.value;
    if (!v.trim()) return;
    handleInput(v);
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !panel.classList.contains("hidden")) panel.classList.add("hidden");
  });
})();