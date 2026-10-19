// Keep the dashboard at the same scroll position after Complete or Undo.
//
// Those buttons submit a normal POST form. The server redirects back to the
// dashboard, which is a brand-new page load, so the browser has no reliable
// way to know where the user was. We save the position just before the form
// submits and restore it once on the next page load.
//
// Forms opt in with the data-preserve-scroll attribute. Without JavaScript
// the forms still work exactly as before.
(function () {
    var STORAGE_KEY = "taskflow-scroll-position";

    function readSavedPosition() {
        try {
            var saved = sessionStorage.getItem(STORAGE_KEY);
            sessionStorage.removeItem(STORAGE_KEY);
            return saved;
        } catch (error) {
            // Storage can be blocked (for example in some private modes).
            return null;
        }
    }

    var saved = readSavedPosition();

    if (saved !== null) {
        // Stop the browser's own approximate restore from overriding ours.
        // This only applies to the current history entry.
        if ("scrollRestoration" in history) {
            history.scrollRestoration = "manual";
        }

        window.scrollTo(0, parseInt(saved, 10) || 0);
    }

    var forms = document.querySelectorAll("form[data-preserve-scroll]");

    for (var i = 0; i < forms.length; i++) {
        forms[i].addEventListener("submit", function () {
            try {
                sessionStorage.setItem(STORAGE_KEY, String(window.scrollY));
            } catch (error) {
                // If storage is unavailable the page simply reloads as before.
            }
        });
    }
})();
