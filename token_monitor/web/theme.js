/* Sets the colour theme before the page paints (loaded in <head>, so there is no flash).
   Order: ?theme=, the choice saved in localStorage, else the system preference. The toggle lives in app.js. */
(function () {
  var saved = null;
  try { saved = localStorage.getItem("tm-theme"); } catch (e) { /* storage blocked */ }
  var asked = /[?&]theme=(light|dark)\b/.exec(location.search);   // ?theme=dark: just for this visit (links, screenshots)
  if (asked) saved = asked[1];
  var light = saved ? saved === "light" : window.matchMedia && matchMedia("(prefers-color-scheme: light)").matches;
  document.documentElement.setAttribute("data-theme", light ? "light" : "dark");
})();
