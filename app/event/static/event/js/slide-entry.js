(function () {
    document.querySelectorAll('[data-slide-entry]').forEach(function (box) {
        if (box.dataset.initialized) return;
        box.dataset.initialized = 'true';
        const form = box.closest('form');
        const urlInput = box.querySelector('[name="slide_url"]');
        const status = box.querySelector('[data-slide-status]');
        const savedStatus = status.cloneNode(true);
        const fallback = box.querySelector('[data-slide-fallback]');
        const savedFallback = !fallback.hidden;
        const note = form.querySelector('#article-auto-generation-note');
        const savedNote = note ? note.textContent : '';
        const retry = box.querySelector('[data-slide-retry]');
        const sourceInput = box.querySelector('[name="slide_source_file"]');
        function importable(value) {
            try {
                const u = new URL(value);
                return u.protocol === 'https:' && u.host === 'speakerdeck.com' && !u.username && !u.password
                    && /^\/[^/%\\]+\/[^/%\\]+\/?$/.test(u.pathname)
                    && !/^https:\/\/[^/]*:/.test(value);
            } catch (_) { return false; }
        }
        function showStatus(chip, message, warning) {
            status.replaceChildren();
            if (chip) {
                const badge = document.createElement('span');
                badge.className = 'detail-chip' + (warning ? ' bg-warning-subtle text-warning-emphasis' : '');
                badge.textContent = chip;
                status.appendChild(badge);
            }
            const text = document.createElement('span');
            text.className = 'text';
            text.textContent = message;
            status.appendChild(text);
        }
        function update() {
            const entry = box.querySelector('[name="slide_entry"]:checked')?.value;
            box.querySelectorAll('[data-slide-panel]').forEach(function (panel) {
                panel.hidden = panel.dataset.slidePanel !== entry;
            });
            const consent = form.querySelector('[name="article_consent"]:checked')?.value;
            const ng = consent ? consent === 'ng' : box.dataset.isNg === 'true';
            const value = urlInput.value.trim();
            const changed = value !== box.dataset.savedUrl;
            const canImport = importable(value);
            fallback.hidden = ng || !((value && !canImport) || (!changed && savedFallback));
            if (retry) retry.hidden = ng || changed || entry !== 'link';
            if (ng) {
                showStatus('記事化しない', 'PDF は読み込みません。リンクは公開ページに載せます。');
                if (note) note.textContent = '発表者が記事化を希望していないため、記事は作りません。';
            } else if (changed && entry === 'link') {
                if (value && canImport) {
                    showStatus('未保存', '保存すると PDF を読み込みます。', true);
                    if (note) note.textContent = '保存後に記事を作ります。';
                } else if (value) {
                    showStatus('リンクのみ', 'リンクは掲載できます。記事を作るには PDF か動画を追加してください。');
                    if (note) note.textContent = 'PDF か動画を登録すると、記事を作ります。';
                } else {
                    showStatus('', '');
                    if (note) note.textContent = 'スライドか動画を登録すると、記事を自動で作ります。';
                }
            } else if (box.dataset.isNg === 'true') {
                showStatus(value ? 'リンクのみ' : '', value ? 'リンクは掲載できます。記事を作るには PDF か動画を追加してください。' : '');
                if (note) note.textContent = '保存後に記事を作ります。';
            } else {
                status.replaceChildren(...Array.from(savedStatus.cloneNode(true).childNodes));
                if (note) note.textContent = entry === 'pdf' && box.dataset.hasPublicPdf !== 'true' && !box.querySelector('[name="slide_file"]')?.files?.length
                    ? 'スライドか動画を登録すると、記事を自動で作ります。' : savedNote;
            }
            if (sourceInput?.files?.length && note && !ng && entry === 'link') note.textContent = '保存後に記事を作ります。';
        }
        form.addEventListener('input', update);
        form.addEventListener('change', update);
        document.querySelector('[data-slide-review]')?.addEventListener('click', function () {
            const toggle = document.querySelector('#detail-settings-toggle button');
            if (box.classList.contains('field-hidden') && toggle) toggle.click();
        });
        update();
    });
})();
