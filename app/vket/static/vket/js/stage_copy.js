(() => {
    const feedback = document.getElementById('stage-copy-message');
    document.querySelectorAll('[data-copy-target]').forEach(button => {
        button.addEventListener('click', async () => {
            const field = document.getElementById(button.dataset.copyTarget);
            try {
                await navigator.clipboard.writeText(field.value);
                button.textContent = 'コピー済み';
                feedback.textContent = 'コピーしました。';
            } catch {
                field.focus();
                field.select();
                feedback.textContent = 'コピーできませんでした。選択した文字を手動でコピーしてください。';
            }
        });
    });
    const body = document.getElementById('stage-body');
    const count = document.getElementById('stage-body-count');
    const updateCount = () => {
        const length = Array.from(body.value).length;
        count.textContent = `${length} / 1000文字` + (length > 1000 ? '：1000文字を超えています。短くしてください。' : '');
        count.classList.toggle('text-danger', length > 1000);
    };
    body.addEventListener('input', updateCount);
    updateCount();
    const form = document.getElementById('stage-thumbnail-form');
    if (form) {
        form.addEventListener('submit', () => {
            form.querySelector('button[type="submit"]').disabled = true;
            document.getElementById('stage-thumbnail-progress').hidden = false;
        });
    }
})();
