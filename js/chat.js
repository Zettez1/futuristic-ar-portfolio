(function () {
  "use strict";

  var root = document.getElementById("chat-root");
  if (!root) return;

  var T = window.FSD_T || function (s) { return s; };
  var PLACEHOLDER = T("Напишіть повідомлення…");
  var state = { step: 0, projectType: null, budget: null, channel: null, name: null, contact: null, input: null, custom: null };

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
    '<button class="chat-fab" id="chat-fab" aria-label="' + T("Відкрити AI-чат") + '">' + ICON_CHAT +
    '<span class="chat-badge">1</span></button>' +
    '<div class="chat-panel hidden" id="chat-panel" role="dialog" aria-label="' + T("AI-агент FastStart Digital") + '">' +
    '<div class="chat-head">' +
    '<div class="chat-avatar">F</div>' +
    '<div><div class="font-display font-semibold text-white text-sm" id="chat-title">' + T("NOVA · AI-агент FastStart Digital") + '</div>' +
    '<div class="chat-online" id="chat-online">' + T("online · відповідає миттєво") + '</div></div>' +
    '<button class="chat-close" id="chat-close" aria-label="' + T("Закрити") + '">' + ICON_CLOSE + '</button>' +
    '</div>' +
    '<div class="chat-body" id="chat-body"></div>' +
    '<div class="chat-chips" id="chat-chips"></div>' +
    '<form class="chat-input" id="chat-form">' +
    '<input id="chat-text" type="text" autocomplete="off" placeholder="' + PLACEHOLDER + '" />' +
    '<button class="chat-send" type="submit">' + ICON_SEND + '</button>' +
    '</form>' +
    '<div class="chat-note" id="chat-note">' + T("AI-агент збирає контакти для розрахунку КП · t.me/faststart_digital") + '</div>' +
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

  /* Live LLM answer (Qwen -> NVIDIA → fallbackReply) */
  function askNova(text, cb) {
    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, lang: (window.FSD_CUR ? FSD_CUR() : "uk") })
    }).then(function (r) { return r.json(); })
      .then(function (d) { cb(d && d.reply ? d.reply : fallbackReply()); })
      .catch(function () { cb(fallbackReply()); });
  }

  function fallbackReply() {
    return T("Записала ваше повідомлення — інженер підготує розрахунок і відповість разом із КП.");
  }

  /* LLM classifier: is the funnel answer a real contact or a question? */
  function classify(text, done) {
    fetch("/api/classify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text })
    }).then(function (r) { return r.json(); })
      .then(function (d) { done(d && d.kind ? d.kind : "other"); })
      .catch(function () { done("other"); });
  }

  /* Human-like guard: if the user asks a live question instead of
     filling the funnel field, NOVA answers and repeats the question. */
  var QUESTION_WORDS = ["як ", "що ", "скільки", "коли", "де ", "чому", "хто", "чи ", "зв'язат", "звязат", "контакт", "телефон", "привіт", "добрий", "hello", "можна", "как", "связат", "сколько", "какие", "какая", "какой", "стоит", "стоимост", "цена", "прайс", "тариф", "вартост", "вартiст", "можно", "привет", "здравств", "доллар", "евро", "euro", "дай", "пришли", "покажи", "скинь", "отправь", "таблиц", "пример", "счет", "квита", "смет", "каталог", "расскажи", "подскажи", "надо", "хочу", "нужно", "узнать", "узнат"];
  function looksLikeQuestion(t) {
    if (t.indexOf("?") !== -1) return true;
    if (t.length > 28) return true;
    for (var i = 0; i < QUESTION_WORDS.length; i++) {
      if (t.indexOf(QUESTION_WORDS[i]) !== -1) return true;
    }
    return false;
  }
  function isBudget(t) {
    if (/поки не знаю|не знаю|поки що|не визнач/.test(t)) return true;
    if (!/\d/.test(t)) return false;
    var rest = t.replace(/^(від|до)\s+/, "").replace(/[\d\s,.\u20ac€$kгрнтисяч]+/g, "");
    return rest.trim() === "";
  }
  function isContact(t) {
    return /[@]|\d|t\.me|telegram|tg|email|пошта|viber|whatsapp|wa\.me|skype/.test(t);
  }

  /* Show typing dots, keep them until the "done" callback fires with the text */
  function botThink(done) {
    var t = el('<div class="msg msg-bot msg-typing"><span></span><span></span><span></span></div>');
    body.appendChild(t);
    body.scrollTop = body.scrollHeight;
    done(function (text) {
      t.remove();
      addMsg(text, "bot");
      body.scrollTop = body.scrollHeight;
    });
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
    if (type === "Веб-розробка" || type == T("Веб-розробка")) return T("250 – 1 500 €");
    if (type === "3D / WebAR-візуалізація") return T("6 000 – 25 000 грн");
    if (type === "AI-агент / автоматизація") return T("150 – 1 200 €");
    if (type === "Комплексний проєкт") return T("350 – 650 €");
    return T("250 – 1 500 €");
  }

  var SERVICE_KEYS = ["Веб-розробка", "3D / WebAR-візуалізація", "AI-агент / автоматизація", "Комплексний проєкт"];
  var BUDGET_KEYS = ["до 15 000", "15 000 – 50 000", "50 000 – 150 000", "від 150 000", "Поки не знаю"];
  var CHANNEL_KEYS = ["Telegram", "WhatsApp", "Instagram", "Facebook", "Email", "Телефон"];
  var TYPES = {
    "Веб-розробка": "Веб-розробка",
    "3D / WebAR-візуалізація": "3D / WebAR-візуалізація",
    "AI-агент / автоматизація": "AI-агент / автоматизація",
    "Комплексний проєкт": "Комплексний проєкт"
  };

  function start() {
    typing(function () {
      addMsg(T("Привіт! Я NOVA — консультант FastStart Digital. Допоможу підібрати оптимальний варіант під вашу задачу — за 30 секунд."), "bot");
      setTimeout(function () {
        addMsg(T("Що вас цікавить?"), "bot");
        setChips(SERVICE_KEYS.map(function (k) { return T(k); }));
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
      if (looksLikeQuestion(t)) {
        botThink(function (done) {
          askNova(text, function (reply) {
            done(reply);
            setChips(SERVICE_KEYS.map(function (k) { return T(k); }));
          });
        });
        return;
      }
      var map = [
        { k: "сайт", v: "Веб-розробка" },
        { k: "лендінг", v: "Веб-розробка" },
        { k: "веб", v: "Веб-розробка" },
        { k: "магазин", v: "Веб-розробка" },
        { k: "застосунок", v: "Веб-розробка" },
        { k: "3d", v: "3D / WebAR-візуалізація" },
        { k: "webar", v: "3D / WebAR-візуалізація" },
        { k: "візуалізац", v: "3D / WebAR-візуалізація" },
        { k: "модел", v: "3D / WebAR-візуалізація" },
        { k: "ai", v: "AI-агент / автоматизація" },
        { k: "агент", v: "AI-агент / автоматизація" },
        { k: "автоматизац", v: "AI-агент / автоматизація" },
        { k: "бот", v: "AI-агент / автоматизація" },
        { k: "чат", v: "AI-агент / автоматизація" },
        { k: "комплексн", v: "Комплексний проєкт" }
      ];
      var matched = map.find(function (m) { return t.indexOf(m.k) !== -1; });
      if (matched) {
        state.step++;
        state.projectType = matched.v;
        typing(function () {
          addMsg(T("Чудово! Тоді середній чек для «%s» — <b>%s</b> без урахування матеріалів.", T(state.projectType), budgetGuess(state.projectType)), "bot");
          setTimeout(function () {
            addMsg(T("Який орієнтовний бюджет закладаєте?"), "bot");
            setChips(BUDGET_KEYS.map(function (k) { return T(k); }));
          }, 350);
        });
      } else {
        botThink(function (done) {
          askNova(text, function (reply) {
            done(reply);
            setChips(SERVICE_KEYS.map(function (k) { return T(k); }));
          });
        });
      }
      return;
    }

    if (state.step === 1) {
      if (!isBudget(t) && looksLikeQuestion(t)) {
        botThink(function (done) {
          askNova(text, function (reply) {
            done(reply);
            addMsg(T("Який орієнтовний бюджет закладаєте?"), "bot");
            setChips(BUDGET_KEYS.map(function (k) { return T(k); }));
          });
        });
        return;
      }
      state.step++;
      state.budget = text;
      typing(function () {
        addMsg(T("Зафіксувала: бюджет <b>%s</b>. Зроблю попередній розрахунок і підготую КП.", text), "bot");
        setTimeout(function () {
          addMsg(T("Де вам зручніше отримати розрахунок?"), "bot");
          setChips(CHANNEL_KEYS.map(function (k) { return T(k); }));
        }, 350);
      });
      return;
    }

    if (state.step === 2) {
      if (looksLikeQuestion(t)) {
        botThink(function (done) {
          askNova(text, function (reply) {
            done(reply);
            addMsg(T("Де вам зручніше отримати розрахунок?"), "bot");
            setChips(CHANNEL_KEYS.map(function (k) { return T(k); }));
          });
        });
        return;
      }
      var chMap = [
        { k: "telegram", v: "telegram" },
        { k: "ватсап", v: "whatsapp" },
        { k: "whatsapp", v: "whatsapp" },
        { k: "вайбер", v: "whatsapp" },
        { k: "viber", v: "whatsapp" },
        { k: "instagram", v: "instagram" },
        { k: "інста", v: "instagram" },
        { k: "facebook", v: "facebook" },
        { k: "фейсбук", v: "facebook" },
        { k: "email", v: "email" },
        { k: "пошта", v: "email" },
        { k: "мейл", v: "email" },
        { k: "телефон", v: "phone" },
        { k: "номер", v: "phone" },
        { k: "дзвон", v: "phone" }
      ];
      var cm = chMap.find(function (m) { return t.indexOf(m.k) !== -1; });
      state.step++;
      state.channel = cm ? cm.v : "telegram";
      var prompts = {
        telegram: "Ваш нік у Telegram (наприклад @ivan)?",
        whatsapp: "Ваш номер у WhatsApp (наприклад +380 67 123 45 67)?",
        instagram: "Ваш нік в Instagram (наприклад @ivan.design)?",
        facebook: "Ім'я або посилання на Facebook?",
        email: "Ваша пошта (наприклад name@email.com)?",
        phone: "Ваш номер телефону (наприклад +380 67 123 45 67)?"
      };
      typing(function () {
        addMsg(T("Дякую! Щоб інженер точно знайшов вас:"), "bot");
        setTimeout(function () {
          addMsg(T(prompts[state.channel] || prompts.telegram), "bot");
        }, 300);
      });
      return;
    }

    if (state.step === 3) {
      if (looksLikeQuestion(t)) {
        botThink(function (done) {
          askNova(text, function (reply) {
            done(reply);
            addMsg(T("Як до вас звертатись?"), "bot");
          });
        });
        return;
      }
      state.step++;
      state.name = text;
      typing(function () {
        addMsg(T("Приємно познайомитись, <b>%s</b>! Останній крок:", text), "bot");
        setTimeout(function () {
          addMsg(T("Залиште ваш контакт — щоб інженер надіслав вам КП:"), "bot");
        }, 350);
      });
      return;
    }

    if (state.step === 4) {
      if (isContact(t)) {
        state.contact = text;
        state.step++;
        sendLead();
        return;
      }
      if (looksLikeQuestion(t)) {
        botThink(function (done) {
          askNova(text, function (reply) {
            done(reply);
            addMsg(T("Залиште ваш контакт — щоб інженер надіслав вам КП:"), "bot");
          });
        });
        return;
      }
      botThink(function (done) {
        classify(text, function (kind) {
          if (kind === "contact") {
            state.contact = text;
            state.step++;
            done(T("Дякую — зафіксувала ваш контакт!"));
            setTimeout(sendLead, 350);
          } else {
            askNova(text, function (reply) {
              done(reply);
              addMsg(T("Залиште ваш контакт — щоб інженер надіслав вам КП:"), "bot");
            });
          }
        });
      });
      return;
    }

    /* post-completion free chat — NOVA (LLM) */
    botThink(function (done) {
      askNova(text, function (reply) {
        done(reply);
        setChips(["Так, ще питання", "Дякую, чекаю КП"].map(function (k) { return T(k); }));
      });
    });
    saveLead({ message: text, source: "chat-followup" });
  }

  function sendLead() {
    typing(function () {
      addMsg(T("Дякую, %s! Ваша заявка прийнята. Інженер надішле розрахунок та КП на <b>%s</b> (%s) протягом 24 годин.", state.name, state.contact, state.channel || "Telegram"), "bot");
      setTimeout(function () {
        addMsg(T("Поки чекаєте — відкрийте WebAR-галерею вище, щоб побачити, як виглядає проєкт у реальному просторі."), "bot");
        setChips(["Відкрити AR-галерею", "Почитати про послуги"].map(function (k) { return T(k); }));
      }, 400);
    });
    saveLead({
      name: state.name,
      contact: state.contact,
      channel: state.channel || "telegram",
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

  document.addEventListener("fsd:lang", function () {
    var online = document.getElementById("chat-online");
    var note = document.getElementById("chat-note");
    var title = document.getElementById("chat-title");
    var fab = document.getElementById("chat-fab");
    if (input) input.placeholder = T("Напишіть повідомлення…");
    if (online) online.textContent = T("online · відповідає миттєво");
    if (note) note.textContent = T("AI-агент збирає контакти для розрахунку КП · t.me/faststart_digital");
    if (title) title.textContent = T("NOVA · AI-агент FastStart Digital");
    if (fab) fab.setAttribute("aria-label", T("Відкрити AI-чат"));
  });
})();