/* スタッフ向け録画確認 UI の EDL 確定送信・409 競合ハンドリング。 */
(function () {
    'use strict';

    function bindSubmit(ctx) {
        const form = ctx.form;
        const config = ctx.config;
        const revInput = document.getElementById('id_expected_revision');
        const submitBtn = document.getElementById('recording-submit-btn');
        const conflictBanner = document.getElementById('recording-conflict-banner');
        const conflictMsg = document.getElementById('recording-conflict-message');
        const submitErr = document.getElementById('recording-submit-error');
        const submitSuccess = document.getElementById('recording-submit-success');

        function showError(msg) {
            if (!submitErr) return;
            submitErr.textContent = msg;
            submitErr.hidden = false;
            if (submitSuccess) submitSuccess.hidden = true;
        }

        function clearBanners() {
            if (submitErr) submitErr.hidden = true;
            if (submitSuccess) submitSuccess.hidden = true;
            if (conflictBanner) conflictBanner.hidden = true;
        }

        function syncSecondaryActionControls(disableAll, clearRevision) {
            if (clearRevision) {
                document.querySelectorAll('[data-recording-revision-input]').forEach(function (el) { el.value = ''; });
            }
            if (!disableAll) return;
            const sel = '[data-testid="emergency-stop-trigger"], [data-testid="retry-failed-order-btn"], [data-testid="cancel-failed-order-trigger"], [data-testid="cancel-active-order-trigger"]';
            document.querySelectorAll(sel).forEach(function (btn) { btn.disabled = true; });
            document.querySelectorAll('#recording-stop-publish-panel, #cancel-failed-order, #cancel-active-order').forEach(function (p) { p.hidden = true; });
        }

        function setTrimInputsDisabled(disabled) {
            const sel = [
                'input[name="start_sec"]',
                'input[name="end_sec"]',
                '#id_start_sec',
                '#id_end_sec',
                '[data-adjust-target]',
                '[data-action="set-start-from-player"]',
                '[data-action="set-end-from-player"]',
                '[data-candidate-side]',
                '[data-candidate-reset]',
                '.recording-tl-handle',
                '.recording-tl-cand',
            ].join(', ');
            form.querySelectorAll(sel).forEach(function (el) { el.disabled = disabled; });
            if (typeof ctx.onSubmitLockChanged === 'function') {
                ctx.onSubmitLockChanged(!disabled);
            }
        }

        form.addEventListener('submit', async function (event) {
            event.preventDefault();
            clearBanners();
            if (!config.canSubmit || !submitBtn || submitBtn.disabled) return;

            const startSec = ctx.getStartSec();
            const endSec = ctx.getEndSec();
            if (!Number.isFinite(startSec) || !Number.isFinite(endSec) || startSec < ctx.safeMin || endSec > ctx.safeMax || startSec >= endSec) {
                showError('開始・終了時刻は録画全体（' + ctx.safeMin.toFixed(1) + '秒 〜 ' + ctx.safeMax.toFixed(1) + '秒）内で「開始 < 終了」となるように指定してください。');
                return;
            }
            const cutsEditor = ctx.getCutsEditor();
            if (cutsEditor) {
                const cutCheck = cutsEditor.validateForSubmit();
                if (!cutCheck.ok) {
                    showError(cutCheck.message);
                    if (cutCheck.focusEl && typeof cutCheck.focusEl.focus === 'function') cutCheck.focusEl.focus();
                    return;
                }
            }

            const csrfInput = form.querySelector('input[name="csrfmiddlewaretoken"]');
            const expectedRevision = Number(revInput ? revInput.value : 1);
            const originalText = submitBtn.textContent;

            submitBtn.disabled = true;
            submitBtn.textContent = '送信中…';
            setTrimInputsDisabled(true);
            if (cutsEditor) cutsEditor.setDisabled(true);

            function restoreAfterFailure() {
                setTrimInputsDisabled(false);
                if (cutsEditor) cutsEditor.setDisabled(false);
                submitBtn.disabled = false;
                submitBtn.textContent = originalText;
            }

            try {
                const payload = { expected_revision: expectedRevision, start_sec: startSec, end_sec: endSec };
                if (cutsEditor) payload.cuts = cutsEditor.getCuts();
                const resp = await fetch(form.action, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/json',
                        'Accept': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest',
                        'X-CSRFToken': csrfInput ? csrfInput.value : '',
                    },
                    body: JSON.stringify(payload),
                });
                const data = await resp.json().catch(function () { return {}; });

                if (resp.ok) {
                    if (revInput && data.edl_revision) revInput.value = String(data.edl_revision);
                    config.canSubmit = false;
                    if (cutsEditor) cutsEditor.setDisabled(true);
                    form.dispatchEvent(new Event('admin:saved'));
                    if (submitSuccess) {
                        submitSuccess.textContent = data.message || 'トリミングを確定し、書き出しキューへ登録しました。';
                        submitSuccess.hidden = false;
                    }
                    const chipWrap = document.getElementById('recording-order-status-chip');
                    if (chipWrap) {
                        const chip = document.createElement('span');
                        chip.className = 'detail-chip bg-warning-subtle text-warning-emphasis';
                        chip.textContent = 'キュー: 待機中';
                        chipWrap.replaceChildren(chip);
                    }
                    submitBtn.textContent = '確定指示を処理中…';
                    submitBtn.disabled = true;
                    syncSecondaryActionControls(true, false);
                    return;
                }

                if (resp.status === 409) {
                    config.canSubmit = false;
                    if (cutsEditor) cutsEditor.setDisabled(true);
                    if (revInput) revInput.value = '';
                    setTrimInputsDisabled(true);
                    syncSecondaryActionControls(true, true);
                    if (conflictBanner && conflictMsg) {
                        conflictMsg.textContent = data.detail || '他のスタッフまたは処理によって更新されました。最新の状態を再読み込みしてください。';
                        conflictBanner.hidden = false;
                        conflictBanner.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    }
                    submitBtn.textContent = '最新状態の再読み込みが必要';
                    submitBtn.disabled = true;
                    return;
                }

                restoreAfterFailure();
                showError(data.detail || '確定指示の登録に失敗しました。入力内容を確認してください。');
            } catch (_) {
                restoreAfterFailure();
                showError('通信エラーが発生しました。時間を置いてもう一度お試しください。');
            }
        });
    }

    window.VrcRecordingReviewSubmit = { bindSubmit: bindSubmit };
})();
