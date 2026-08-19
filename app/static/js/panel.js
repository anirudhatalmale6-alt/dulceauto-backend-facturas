/*
  JavaScript del panel.

  Muy poco a proposito. La navegacion, los datos y los permisos los resuelve el
  servidor; aqui solo queda el cambio de tema, que es puramente visual.

  Nada relacionado con contrasenas vive en este archivo ni puede vivir: la
  comprobacion se hace siempre en el servidor contra un hash.
*/
(function () {
  "use strict";

  var THEMES = { light: "", soft: "theme-soft", night: "theme-night" };

  function applyTheme(theme) {
    var body = document.body;
    body.classList.remove("theme-soft", "theme-night");
    if (THEMES[theme]) body.classList.add(THEMES[theme]);

    document.querySelectorAll("[data-theme-btn]").forEach(function (btn) {
      btn.classList.toggle("active", btn.getAttribute("data-theme-btn") === theme);
    });
  }

  window.setTheme = function (theme) {
    if (!Object.prototype.hasOwnProperty.call(THEMES, theme)) return;
    applyTheme(theme);
    try {
      localStorage.setItem("da_theme", theme);
    } catch (e) {
      /* navegador con almacenamiento bloqueado: la cookie basta */
    }
    // Un ano. La cookie es la que permite al servidor pintar el tema correcto
    // ya en la primera respuesta, sin parpadeo.
    document.cookie = "da_theme=" + theme + ";path=/;max-age=31536000;samesite=lax";
  };

  // Si el navegador tiene un tema guardado y no coincide con el que sirvio el
  // servidor, se corrige al vuelo. Ocurre solo si se borro la cookie.
  try {
    var saved = localStorage.getItem("da_theme");
    if (saved && THEMES.hasOwnProperty(saved)) {
      var current = document.body.classList.contains("theme-night")
        ? "night"
        : document.body.classList.contains("theme-soft")
        ? "soft"
        : "light";
      if (saved !== current) window.setTheme(saved);
    }
  } catch (e) {
    /* sin almacenamiento: se queda con lo que sirvio el servidor */
  }
})();
