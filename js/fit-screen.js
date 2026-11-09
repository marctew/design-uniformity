/* Design Uniformity: full-height pages on phones (PWA-ICONS.md 3.1), with no framework.
 *
 * A page that should be exactly the screen's height (a list that scrolls inside its card, with the bottom nav
 * on the bottom edge and nothing else moving) puts the class on <html> and loads this:
 *   <html class="full-height ...">  and  <script src="/static/design/fit-screen.js" defer></script>
 * uniform.css then lays the shell out as a column of that height, and the element with the class "fill" inside
 * .main takes the room that's left (give its scrolling part overflow: auto).
 *
 * It sets --app-h on <html> from window.innerHeight, because iOS's CSS heights don't match what's showing. As a
 * home-screen app with the black-translucent status bar, iOS also reports the screen short by the status bar's
 * height, and a page that tall leaves the fixed bottom nav floating above the bottom edge, so in standalone mode
 * the status bar's height (env(safe-area-inset-top), measured) is added back. Found in HomeForm, 9 Nov 2026.
 * Pages without the class are left alone. Call duFitScreen() after changing anything that affects it.
 */
(function () {
  var root = document.documentElement;
  var standalone = window.navigator.standalone === true ||
    (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches);

  function statusBar() {
    var probe = document.createElement("div");
    probe.style.cssText = "position:fixed;top:0;height:env(safe-area-inset-top);visibility:hidden;pointer-events:none";
    document.body.appendChild(probe);
    var px = probe.getBoundingClientRect().height;
    probe.remove();
    return px;
  }

  function fit() {
    if (!root.classList.contains("full-height") || !document.body) return;
    root.style.setProperty("--app-h", (window.innerHeight + (standalone ? statusBar() : 0)) + "px");
  }

  window.duFitScreen = fit;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fit); else fit();
  window.addEventListener("resize", fit);
  window.addEventListener("orientationchange", function () { setTimeout(fit, 300); });
})();
