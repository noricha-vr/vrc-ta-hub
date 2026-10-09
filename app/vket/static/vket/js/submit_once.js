// form.js-submit-once の送信ボタンを送信中は押せなくし、data-loading-text を表示する。
// 戻るボタンでページが復元された時（bfcache）は、ボタンを元の状態に戻す。
(function () {
    'use strict';

    var SPINNER = '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>';

    function submitButton(form) {
        return form.querySelector('button[type="submit"]');
    }

    function lock(form) {
        var btn = submitButton(form);
        if (!btn || btn.disabled) { return; }
        btn.dataset.originalHtml = btn.innerHTML;
        btn.disabled = true;
        var text = document.createElement('span');
        text.textContent = form.dataset.loadingText || '送信中…';
        btn.innerHTML = SPINNER;
        btn.appendChild(text);
    }

    function restoreAll() {
        document.querySelectorAll('form.js-submit-once').forEach(function (form) {
            var btn = submitButton(form);
            if (!btn || btn.dataset.originalHtml === undefined) { return; }
            btn.disabled = false;
            btn.innerHTML = btn.dataset.originalHtml;
            delete btn.dataset.originalHtml;
        });
    }

    // submit はブラウザの入力チェックを通った後にだけ発火する
    document.addEventListener('submit', function (event) {
        var form = event.target;
        if (form instanceof HTMLFormElement && form.classList.contains('js-submit-once')) {
            lock(form);
        }
    });
    window.addEventListener('pageshow', restoreAll);
})();
