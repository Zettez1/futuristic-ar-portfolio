
    (function () {
      "use strict";
      var T = window.FSD_T || function (s) { return s; };

      var turnstileToken = { login: "", register: "", recover: "" };
      window.onTurnstileLogin = function (t) { turnstileToken.login = t; };
      window.onTurnstileRegister = function (t) { turnstileToken.register = t; };
      window.onTurnstileRecover = function (t) { turnstileToken.recover = t; };

      function goGoogle(kind) {
        var tok = kind === "login" ? turnstileToken.login : turnstileToken.register;
        if (!tok) {
          showMsg(T("Спершу пройдіть перевірку «Я не робот» вище"), false);
          return;
        }
        var btn = document.getElementById(kind === "login" ? "google-login" : "google-register");
        if (btn) { btn.disabled = true; btn.style.opacity = "0.6"; btn.innerHTML = '<span class="spinner" style="display:inline-block;width:16px;height:16px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:spin .7s linear infinite;margin-right:8px;vertical-align:middle"></span>' + T("Перенаправлення…"); }
        var url = "/api/auth/google?mode=select&token=" + encodeURIComponent(tok);
        var n = qp("next") || "";
        if (n) url += "&next=" + encodeURIComponent(n);
        location.href = url;
      }
      if (!document.getElementById("spin-style")) {
        var s = document.createElement("style");
        s.id = "spin-style";
        s.textContent = "@keyframes spin{to{transform:rotate(360deg)}}";
        document.head.appendChild(s);
      }

      document.getElementById("google-login").addEventListener("click", function () {
        goGoogle("login");
      });
      document.getElementById("google-register").addEventListener("click", function () {
        goGoogle("register");
      });

      window.addEventListener("pageshow", function (e) {
        if (e.persisted) {
          turnstileToken.login = "";
          turnstileToken.register = "";
          turnstileToken.recover = "";
          if (window.turnstile) {
            [].forEach.call(document.querySelectorAll(".cf-turnstile"), function (el) {
              window.turnstile.reset(el);
            });
          }
        }
      });

      function qp(name) { return new URLSearchParams(location.search).get(name); }

      var next = qp("next") || "";

      fetch("/api/auth/me", { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d && d.ok && d.user) location.href = next || "/";
        })
        .catch(function () {});

      var cur = qp("tab") === "register" || qp("register") === "1" ? "register" : "login";
      var tabs = Array.prototype.slice.call(document.querySelectorAll(".auth-tab"));
      var panels = Array.prototype.slice.call(document.querySelectorAll(".auth-panel"));

      function showMsg(html, ok) {
        var m = document.getElementById("auth-msg");
        m.style.display = "block";
        m.className = "auth-msg " + (ok ? "ok" : "err");
        m.textContent = html;
      }
      function hideMsg() { document.getElementById("auth-msg").style.display = "none"; }

      function showTab(name) {
        cur = name;
        tabs.forEach(function (t) {
          t.classList.toggle("active", t.getAttribute("data-tab") === cur);
        });
        panels.forEach(function (p) { p.style.display = p.getAttribute("data-panel") === cur ? "block" : "none"; });
        document.getElementById("auth-title").textContent = cur === "register" ? T("Реєстрація") : T("Вхід");
        document.getElementById("auth-switch-hint").innerHTML =
          '<button type="button" class="auth-link" data-switch="' + (cur === "register" ? "login" : "register") + '">' +
          T(cur === "register" ? "Вже маєте акаунт? Увійдіть" : "Новий клієнт? Зареєструйтеся") + "</button>";
        hideMsg();
        if (cur === "register") document.getElementById("reg-code-box").style.display = "none";
        if (cur === "login") closeRecover();
      }

      tabs.forEach(function (t) {
        t.addEventListener("click", function () { showTab(t.getAttribute("data-tab")); });
      });
      document.getElementById("auth-switch-hint").addEventListener("click", function (e) {
        var b = e.target.closest("[data-switch]");
        if (b) showTab(b.getAttribute("data-switch"));
      });
      showTab(cur);

      var auth = qp("auth");
      if (auth === "registered") showMsg(T("Акаунт створено!"), true);
      else if (auth === "denied") showMsg(T("Доступ заборонено"), false);

      function post(url, data) {
        return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) })
          .then(function (r) { return r.json().then(function (d) { return { status: r.status, d: d }; }); });
      }
      function btnState(btn, busy) { btn.disabled = busy; btn.style.opacity = busy ? "0.5" : "1"; }

      /* LOGIN */
      document.getElementById("login-form").addEventListener("submit", function (e) {
        e.preventDefault();
        var email = document.getElementById("login-email").value.trim();
        var pw = document.getElementById("login-password").value;
        if (!email || !pw) return;
        var btn = this.querySelector(".auth-submit");
        btnState(btn, true);
        post("/api/auth/login", { email: email, password: pw, cf_turnstile: turnstileToken.login })
          .then(function (res) {
            if (res.status === 200) { if (window.turnstile) turnstile.reset(); location.href = next || "/"; }
            else if (res.status === 401 && res.d.detail === "use google login") showMsg(T("Цей акаунт створено через Google. Увійдіть через Google"), false);
            else showMsg(T("Невірна пошта або пароль"), false);
          })
          .catch(function () { showMsg(T("Помилка з'єднання"), false); })
          .finally(function () { btnState(btn, false); });
      });

      /* RECOVERY */
      var recoverEmail = "";
      function openRecover() {
        hideMsg();
        document.getElementById("login-main").style.display = "none";
        document.getElementById("recover-box").style.display = "block";
        document.getElementById("recover-email-step").style.display = "block";
        document.getElementById("recover-code-step").style.display = "none";
        document.getElementById("recover-email").focus();
      }
      function closeRecover() {
        document.getElementById("recover-box").style.display = "none";
        document.getElementById("login-main").style.display = "block";
      }
      document.getElementById("forgot-btn").addEventListener("click", openRecover);
      document.getElementById("recover-back").addEventListener("click", closeRecover);

      document.getElementById("recover-send").addEventListener("click", function () {
        var email = document.getElementById("recover-email").value.trim();
        if (!email) return;
        var btn = this;
        btnState(btn, true);
        post("/api/auth/recover", { email: email, cf_turnstile: turnstileToken.recover })
          .then(function (res) {
            if (res.status === 200) {
              if (window.turnstile) turnstile.reset();
              recoverEmail = email;
              document.getElementById("recover-email-step").style.display = "none";
              document.getElementById("recover-code-step").style.display = "block";
              document.getElementById("recover-code").focus();
              showMsg(T("Код надіслано на %s", email), true);
            } else if (res.status === 404) showMsg(T("Цю пошту не знайдено"), false);
            else showMsg(T("Не вдалося відправити код. Спробуйте пізніше"), false);
          })
          .catch(function () { showMsg(T("Помилка з'єднання"), false); })
          .finally(function () { btnState(btn, false); });
      });

      document.getElementById("recover-resend").addEventListener("click", function () {
        var email = recoverEmail || document.getElementById("recover-email").value.trim();
        if (!email) return;
        var btn = this;
        btnState(btn, true);
        post("/api/auth/recover", { email: email, cf_turnstile: turnstileToken.recover })
          .then(function (res) {
            if (res.status === 200) showMsg(T("Код надіслано на %s", email), true);
            else if (res.status === 404) showMsg(T("Цю пошту не знайдено"), false);
            else showMsg(T("Не вдалося відправити код. Спробуйте пізніше"), false);
          })
          .catch(function () { showMsg(T("Помилка з'єднання"), false); })
          .finally(function () { btnState(btn, false); });
      });

      document.getElementById("recover-confirm").addEventListener("click", function () {
        var code = document.getElementById("recover-code").value.trim();
        var pw = document.getElementById("recover-password").value;
        var pw2 = document.getElementById("recover-password2").value;
        if (pw !== pw2) { showMsg(T("Паролі не збігаються"), false); return; }
        if (pw.length < 8) { showMsg(T("Пароль занадто короткий (мін. 8 символів)"), false); return; }
        var btn = this;
        btnState(btn, true);
        post("/api/auth/recover/confirm", { email: recoverEmail, code: code, password: pw, cf_turnstile: turnstileToken.recover })
          .then(function (res) {
            if (res.status === 200) {
              if (window.turnstile) turnstile.reset();
              showMsg(T("Пароль змінено! Увійдіть з новим паролем"), true);
              document.getElementById("recover-code-step").style.display = "none";
              document.getElementById("recover-email-step").style.display = "block";
              setTimeout(closeRecover, 2500);
            } else if (res.status === 400 && res.d.detail === "wrong code") showMsg(T("Невірний код. Спробуйте ще раз"), false);
            else if (res.status === 400 && res.d.detail === "code expired") showMsg(T("Код протерміновано. Надішліть новий"), false);
            else showMsg(T("Невірний код. Спробуйте ще раз"), false);
          })
          .catch(function () { showMsg(T("Помилка з'єднання"), false); })
          .finally(function () { btnState(btn, false); });
      });

      /* REGISTER */
      var regEmail = "";
      document.getElementById("reg-form").addEventListener("submit", function (e) {
        e.preventDefault();
        var email = document.getElementById("reg-email").value.trim();
        var pw = document.getElementById("reg-password").value;
        var pw2 = document.getElementById("reg-password2").value;
        if (pw !== pw2) { showMsg(T("Паролі не збігаються"), false); return; }
        var btn = this.querySelector(".auth-submit");
        btnState(btn, true);
        post("/api/auth/register", { email: email, password: pw, cf_turnstile: turnstileToken.register })
          .then(function (res) {
            if (res.status === 200) {
              if (window.turnstile) turnstile.reset();
              regEmail = email;
              document.getElementById("reg-form").style.display = "none";
              document.getElementById("reg-code-box").style.display = "block";
              document.getElementById("reg-code").focus();
              showMsg(T("Код надіслано на %s", email), true);
            } else if (res.status === 409) showMsg(T("Ця пошта вже зареєстрована"), false);
            else if (res.status === 400 && res.d.detail === "invalid email") showMsg(T("Невірна пошта"), false);
            else if (res.status === 400 && res.d.detail === "password too short") showMsg(T("Пароль занадто короткий (мін. 8 символів)"), false);
            else showMsg(T("Не вдалося відправити код. Спробуйте пізніше"), false);
          })
          .catch(function () { showMsg(T("Помилка з'єднання"), false); })
          .finally(function () { btnState(btn, false); });
      });

      document.getElementById("reg-confirm").addEventListener("click", function () {
        var code = document.getElementById("reg-code").value.trim();
        if (!code) return;
        var btn = this;
        btnState(btn, true);
        post("/api/auth/verify", { email: regEmail, code: code })
          .then(function (res) {
            if (res.status === 200) location.href = next || "/?auth=registered";
            else if (res.status === 400 && res.d.detail === "wrong code") showMsg(T("Невірний код. Спробуйте ще раз"), false);
            else if (res.status === 400 && res.d.detail === "code expired") showMsg(T("Код протерміновано. Надішліть новий"), false);
            else showMsg(T("Невірний код. Спробуйте ще раз"), false);
          })
          .catch(function () { showMsg(T("Помилка з'єднання"), false); })
          .finally(function () { btnState(btn, false); });
      });

      document.getElementById("reg-resend").addEventListener("click", function () {
        var email = regEmail || document.getElementById("reg-email").value.trim();
        if (!email) return;
        var btn = this;
        btnState(btn, true);
        post("/api/auth/register", { email: email, password: document.getElementById("reg-password").value, cf_turnstile: turnstileToken.register })
          .then(function (res) {
            if (res.status === 200) showMsg(T("Код надіслано на %s", email), true);
            else showMsg(T("Не вдалося відправити код. Спробуйте пізніше"), false);
          })
          .catch(function () { showMsg(T("Помилка з'єднання"), false); })
          .finally(function () { btnState(btn, false); });
      });

      function bindCodeAuto(elId, btnId) {
        var input = document.getElementById(elId);
        input.addEventListener("input", function () {
          this.value = this.value.replace(/[^\d]/g, "").slice(0, 6);
          if (this.value.length === 6) document.getElementById(btnId).click();
        });
      }
      bindCodeAuto("reg-code", "reg-confirm");
      bindCodeAuto("recover-code", "recover-confirm");
    })();
  