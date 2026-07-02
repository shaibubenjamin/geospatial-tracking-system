/* ERITAS - idle auto-logout.
 *
 * Signs a logged-in user out after IDLE_LIMIT_MS of no interaction, and
 * coordinates across tabs via localStorage. No-op for anonymous visitors
 * (the public dashboard), so it's safe to include on shared pages.
 *
 * Include on every authenticated web page:
 *   <script src="/static/idle-logout.js?v=1"></script>
 */
(function () {
  'use strict';

  var IDLE_LIMIT_MS   = 10 * 60 * 1000; // log out after 10 minutes idle
  var CHECK_EVERY_MS  = 15 * 1000;      // re-evaluate every 15s
  var WRITE_THROTTLE  = 5 * 1000;       // at most one activity write / 5s
  var ACTIVITY_KEY    = 'eritas_last_activity';

  function hasSession() { return !!localStorage.getItem('token'); }
  // Anonymous (public dashboard) → nothing to expire.
  if (!hasSession()) return;

  var lastWrite = 0;

  function stamp(t) { try { localStorage.setItem(ACTIVITY_KEY, String(t)); } catch (e) {} }
  function lastActivity() { return parseInt(localStorage.getItem(ACTIVITY_KEY) || '0', 10) || 0; }

  // Seed now so a fresh load doesn't immediately count as idle.
  stamp(Date.now());
  lastWrite = Date.now();

  function onActivity() {
    var t = Date.now();
    if (t - lastWrite < WRITE_THROTTLE) return; // throttle cross-tab writes
    lastWrite = t;
    stamp(t);
  }

  var EVENTS = ['mousemove', 'mousedown', 'keydown', 'scroll', 'touchstart', 'click', 'wheel'];
  EVENTS.forEach(function (ev) { window.addEventListener(ev, onActivity, { passive: true }); });

  function logout() {
    try { localStorage.clear(); } catch (e) {}
    window.location.href = '/login?reason=idle';
  }

  function check() {
    if (!hasSession()) return;                       // already signed out elsewhere
    if (Date.now() - lastActivity() >= IDLE_LIMIT_MS) logout();
  }
  setInterval(check, CHECK_EVERY_MS);
  // Re-check when a backgrounded tab returns to the foreground.
  document.addEventListener('visibilitychange', function () { if (!document.hidden) check(); });

  // If another tab signs out (token cleared), follow it here.
  window.addEventListener('storage', function () {
    if (!localStorage.getItem('token')) window.location.href = '/login?reason=idle';
  });
})();
