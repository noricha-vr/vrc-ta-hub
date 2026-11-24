/* スタッフ向け録画プロキシ再生・開始終了トリミング・途中カット確定 UI。 */
(function () {
    'use strict';

    function formatSec(sec) {
        const clamped = Math.max(0, Number(sec) || 0);
        const tenths = Math.round(clamped * 10);
        const whole = Math.floor(tenths / 10);
        const frac = tenths % 10;
        const mins = Math.floor(whole / 60);
        const rem = whole % 60;
        const hrs = Math.floor(mins / 60);
        const remMin = mins % 60;
        const base = hrs > 0
            ? hrs + ':' + String(remMin).padStart(2, '0') + ':' + String(rem).padStart(2, '0')
            : remMin + ':' + String(rem).padStart(2, '0');
        return base + '.' + frac;
    }

    function roundTrim(val) {
        return Math.round(val * 1000) / 1000;
    }

    function formatInputVal(val) {
        const rounded = roundTrim(val);
        return Number.isInteger(rounded) ? String(rounded) : String(rounded);
    }

    function init() {
        const configEl = document.getElementById('recording-review-config');
        const form = document.getElementById('recording-trim-form');
        if (!configEl || !form) return;

        let config;
        try {
            config = JSON.parse(configEl.textContent || '{}');
        } catch (_) {
            return;
        }

        const safeMin = Number(config.safeMin) || 0;
        const safeMax = Number(config.safeMax) || 0;
        const video = document.getElementById('recording-proxy-player');
        const loadingEl = document.getElementById('recording-player-loading');
        const errorEl = document.getElementById('recording-player-error');
        const currentTimeEl = document.getElementById('recording-current-time');
        const adoptedDurationEl = document.getElementById('recording-adopted-duration');
        const startInput = document.getElementById('id_start_sec');
        const endInput = document.getElementById('id_end_sec');
        const revInput = document.getElementById('id_expected_revision');
        const submitBtn = document.getElementById('recording-submit-btn');
        const conflictBanner = document.getElementById('recording-conflict-banner');
        const conflictMsg = document.getElementById('recording-conflict-message');
        const submitErr = document.getElementById('recording-submit-error');
        const submitSuccess = document.getElementById('recording-submit-success');
        const playBeforeEndBtn = form.querySelector('[data-action="play-before-end"]');
        const playFromStartBtn = form.querySelector('[data-action="play-from-start"]');
        const playTrimRangeBtn = document.querySelector('[data-action="play-trim-range"]');

        let segmentStopAt = null;
        let pendingSeek = null;
        let isSyncingTrim = false;
        let allowBeforeStartPreview = false;
        let allowCutThroughPreview = false;
        let programmaticSeekTarget = null;
        let cutsEditor = null;

        function getStartSec() {
            const parsed = Number(startInput.value);
            return Number.isFinite(parsed) ? parsed : safeMin;
        }

        function getEndSec() {
            const parsed = Number(endInput.value);
            return Number.isFinite(parsed) ? parsed : safeMax;
        }

        function getPlaybackMode() {
            const checked = document.querySelector('input[name="playback_mode"]:checked');
            return checked ? checked.value : 'preview';
        }

        function getEndOffset() {
            const checked = document.querySelector('input[name="end_preview_offset"]:checked');
            const parsed = checked ? Number(checked.value) : 7;
            return Number.isFinite(parsed) && parsed > 0 ? parsed : 7;
        }

        function updateAdoptedDuration() {
            if (!adoptedDurationEl) return;
            const dur = cutsEditor ? cutsEditor.getAdoptedDuration() : (getEndSec() - getStartSec());
            adoptedDurationEl.textContent = dur > 0 ? formatSec(dur) + '（' + dur.toFixed(1) + '秒）' : '不正な範囲';
        }

        function syncCandidateSelection(radioName, targetValue) {
            form.querySelectorAll('input[name="' + radioName + '"]').forEach(function (radio) {
                radio.checked = Math.abs(Number(radio.value) - targetValue) < 0.005;
            });
        }

        function enforcePreviewStartBounds() {
            if (!video || video.readyState < 1) return false;
            if (getPlaybackMode() !== 'preview' || allowBeforeStartPreview) return false;
            const start = getStartSec();
            if (video.currentTime < start - 0.05) {
                programmaticSeekTarget = start;
                video.currentTime = start;
                return true;
            }
            return false;
        }

        function setTrimValue(side, rawVal, options) {
            const emitChange = !options || options.emitChange !== false;
            let next = Number(rawVal);
            if (!Number.isFinite(next)) return;
            isSyncingTrim = true;
            try {
                if (side === 'start') {
                    const maxAllowed = Math.max(safeMin, roundTrim(getEndSec() - 0.1));
                    next = roundTrim(Math.max(safeMin, Math.min(maxAllowed, next)));
                    startInput.value = formatInputVal(next);
                    syncCandidateSelection('start_candidate', next);
                    allowBeforeStartPreview = false;
                    enforcePreviewStartBounds();
                    if (emitChange) {
                        startInput.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                } else {
                    const minAllowed = Math.min(safeMax, roundTrim(getStartSec() + 0.1));
                    next = roundTrim(Math.min(safeMax, Math.max(minAllowed, next)));
                    endInput.value = formatInputVal(next);
                    syncCandidateSelection('end_candidate', next);
                    if (segmentStopAt !== null) {
                        segmentStopAt = next;
                    }
                    if (emitChange) {
                        endInput.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }
            } finally {
                isSyncingTrim = false;
            }
            if (cutsEditor) cutsEditor.onTrimChanged();
            updateAdoptedDuration();
        }

        function seekAndPlay(targetSec, stopAtSec, options) {
            if (!video) return;
            const bounded = Math.max(safeMin, Math.min(safeMax, targetSec));
            allowBeforeStartPreview = Boolean(options && options.allowBeforeStart);
            allowCutThroughPreview = Boolean(options && options.allowCutThrough);
            programmaticSeekTarget = bounded;
            segmentStopAt = stopAtSec !== null && stopAtSec !== undefined ? Math.max(safeMin, Math.min(safeMax, stopAtSec)) : null;
            if (video.readyState >= 1) {
                video.currentTime = bounded;
                const playPromise = video.play();
                if (playPromise && typeof playPromise.catch === 'function') {
                    playPromise.catch(function () {});
                }
            } else {
                pendingSeek = bounded;
                const playPromise = video.play();
                if (playPromise && typeof playPromise.catch === 'function') {
                    playPromise.catch(function () {});
                }
            }
        }

        form.querySelectorAll('input[name="start_candidate"]').forEach(function (radio) {
            radio.addEventListener('change', function () {
                if (radio.checked) setTrimValue('start', Number(radio.value), { emitChange: true });
            });
        });
        form.querySelectorAll('input[name="end_candidate"]').forEach(function (radio) {
            radio.addEventListener('change', function () {
                if (radio.checked) setTrimValue('end', Number(radio.value), { emitChange: true });
            });
        });

        form.querySelectorAll('[data-adjust-target]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const side = btn.dataset.adjustTarget;
                const delta = Number(btn.dataset.adjustDelta) || 0;
                const base = side === 'start' ? getStartSec() : getEndSec();
                setTrimValue(side, base + delta, { emitChange: true });
            });
        });

        startInput.addEventListener('change', function () {
            if (isSyncingTrim) return;
            setTrimValue('start', startInput.value, { emitChange: false });
        });
        endInput.addEventListener('change', function () {
            if (isSyncingTrim) return;
            setTrimValue('end', endInput.value, { emitChange: false });
        });

        document.querySelectorAll('input[name="end_preview_offset"]').forEach(function (radio) {
            radio.addEventListener('change', function () {
                if (playBeforeEndBtn) {
                    playBeforeEndBtn.textContent = getEndOffset() + '秒前から再生';
                }
            });
        });

        if (playFromStartBtn) {
            playFromStartBtn.addEventListener('click', function () {
                const start = getStartSec();
                const stopAt = getPlaybackMode() === 'preview' ? getEndSec() : null;
                seekAndPlay(start, stopAt);
            });
        }

        if (playBeforeEndBtn) {
            playBeforeEndBtn.addEventListener('click', function () {
                const cand = getEndSec();
                const seekTo = Math.max(safeMin, cand - getEndOffset());
                seekAndPlay(seekTo, cand, { allowBeforeStart: seekTo < getStartSec() });
            });
        }

        if (playTrimRangeBtn) {
            playTrimRangeBtn.addEventListener('click', function () {
                seekAndPlay(getStartSec(), getEndSec());
            });
        }

        document.querySelectorAll('input[name="playback_mode"]').forEach(function (radio) {
            radio.addEventListener('change', function () {
                if (!video) return;
                allowBeforeStartPreview = false;
                allowCutThroughPreview = false;
                if (getPlaybackMode() === 'preview') {
                    const start = getStartSec();
                    const end = getEndSec();
                    segmentStopAt = end;
                    if (video.currentTime < start || video.currentTime >= end) {
                        if (video.readyState >= 1) {
                            programmaticSeekTarget = start;
                            video.currentTime = start;
                        }
                    }
                } else {
                    segmentStopAt = null;
                }
            });
        });

        if (video) {
            video.addEventListener('loadstart', function () {
                if (loadingEl) loadingEl.hidden = false;
                if (errorEl) errorEl.hidden = true;
            });
            video.addEventListener('waiting', function () {
                if (loadingEl) loadingEl.hidden = false;
            });
            video.addEventListener('loadedmetadata', function () {
                if (pendingSeek !== null) {
                    programmaticSeekTarget = pendingSeek;
                    video.currentTime = pendingSeek;
                    pendingSeek = null;
                } else if (getPlaybackMode() === 'preview') {
                    const start = getStartSec();
                    if (start > 0 && video.currentTime < start) {
                        programmaticSeekTarget = start;
                        video.currentTime = start;
                    }
                }
            });
            video.addEventListener('canplay', function () {
                if (loadingEl) loadingEl.hidden = true;
            });
            video.addEventListener('error', function () {
                if (loadingEl) loadingEl.hidden = true;
                if (errorEl) errorEl.hidden = false;
            });
            video.addEventListener('seeking', function () {
                const isProgrammatic = programmaticSeekTarget !== null && Math.abs(video.currentTime - programmaticSeekTarget) <= 0.15;
                if (!isProgrammatic) {
                    allowBeforeStartPreview = false;
                    allowCutThroughPreview = false;
                }
                if (!isProgrammatic) {
                    enforcePreviewStartBounds();
                }
            });
            video.addEventListener('seeked', function () {
                const isProgrammatic = programmaticSeekTarget !== null && Math.abs(video.currentTime - programmaticSeekTarget) <= 0.15;
                programmaticSeekTarget = null;
                if (!isProgrammatic) {
                    allowBeforeStartPreview = false;
                    allowCutThroughPreview = false;
                }
                enforcePreviewStartBounds();
            });
            video.addEventListener('play', function () {
                if (allowBeforeStartPreview && video.currentTime >= getStartSec() - 0.05) {
                    allowBeforeStartPreview = false;
                }
                enforcePreviewStartBounds();
            });
            video.addEventListener('timeupdate', function () {
                if (currentTimeEl) currentTimeEl.textContent = formatSec(video.currentTime);
                if (cutsEditor) cutsEditor.onPlayerTimeUpdate(video.currentTime);
                if (allowBeforeStartPreview && video.currentTime >= getStartSec() - 0.05) {
                    allowBeforeStartPreview = false;
                }
                if (enforcePreviewStartBounds()) {
                    return;
                }
                if (segmentStopAt !== null && video.currentTime >= segmentStopAt) {
                    video.pause();
                    video.currentTime = segmentStopAt;
                    segmentStopAt = null;
                    allowBeforeStartPreview = false;
                    allowCutThroughPreview = false;
                } else if (getPlaybackMode() === 'preview' && !video.paused) {
                    if (!allowCutThroughPreview && cutsEditor) {
                        const skipSpan = cutsEditor.getAcceptSpans().find(function (span) {
                            return video.currentTime >= span[0] && video.currentTime < span[1] - 0.05;
                        });
                        if (skipSpan) {
                            programmaticSeekTarget = skipSpan[1];
                            video.currentTime = skipSpan[1];
                            return;
                        }
                    }
                    const end = getEndSec();
                    if (video.currentTime >= end) {
                        video.pause();
                        video.currentTime = end;
                    }
                }
            });
        }

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

        form.addEventListener('submit', async function (event) {
            event.preventDefault();
            clearBanners();

            if (!config.canSubmit || !submitBtn || submitBtn.disabled) {
                return;
            }

            const startSec = getStartSec();
            const endSec = getEndSec();
            if (!Number.isFinite(startSec) || !Number.isFinite(endSec) || startSec < safeMin || endSec > safeMax || startSec >= endSec) {
                showError('開始・終了時刻は許容範囲（' + safeMin.toFixed(1) + '秒 〜 ' + safeMax.toFixed(1) + '秒）内で「開始 < 終了」となるように指定してください。');
                return;
            }
            if (cutsEditor) {
                const cutCheck = cutsEditor.validateForSubmit();
                if (!cutCheck.ok) {
                    showError(cutCheck.message);
                    if (cutCheck.focusEl && typeof cutCheck.focusEl.focus === 'function') {
                        cutCheck.focusEl.focus();
                    }
                    return;
                }
            }

            const csrfInput = form.querySelector('input[name="csrfmiddlewaretoken"]');
            const expectedRevision = Number(revInput ? revInput.value : 1);
            const originalText = submitBtn.textContent;
            submitBtn.disabled = true;
            submitBtn.textContent = '送信中…';

            try {
                const payload = {
                    expected_revision: expectedRevision,
                    start_sec: startSec,
                    end_sec: endSec,
                };
                if (cutsEditor) {
                    payload.cuts = cutsEditor.getCuts();
                }
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
                        chip.textContent = 'キュー: 待機中 (queued)';
                        chipWrap.replaceChildren(chip);
                    }
                    submitBtn.textContent = '確定指示を処理中…';
                    submitBtn.disabled = true;
                    return;
                }

                if (resp.status === 409) {
                    config.canSubmit = false;
                    if (cutsEditor) cutsEditor.setDisabled(true);
                    if (revInput) revInput.value = '';
                    form.querySelectorAll('input[name="start_sec"], input[name="end_sec"], input[name="start_candidate"], input[name="end_candidate"], [data-adjust-target]').forEach(function (el) {
                        el.disabled = true;
                    });
                    if (conflictBanner && conflictMsg) {
                        conflictMsg.textContent = data.detail || '他のスタッフまたは処理によって更新されました。最新の状態を再読み込みしてください。';
                        conflictBanner.hidden = false;
                        conflictBanner.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    }
                    submitBtn.textContent = '最新状態の再読み込みが必要';
                    submitBtn.disabled = true;
                    return;
                }

                showError(data.detail || '確定指示の登録に失敗しました。入力内容を確認してください。');
                submitBtn.disabled = false;
                submitBtn.textContent = originalText;
            } catch (_) {
                showError('通信エラーが発生しました。時間を置いてもう一度お試しください。');
                submitBtn.disabled = false;
                submitBtn.textContent = originalText;
            }
        });

        if (window.VrcRecordingCuts && typeof window.VrcRecordingCuts.createEditor === 'function') {
            cutsEditor = window.VrcRecordingCuts.createEditor({
                form: form,
                config: config,
                video: video,
                safeMin: safeMin,
                safeMax: safeMax,
                getStartSec: getStartSec,
                getEndSec: getEndSec,
                seekAndPlay: seekAndPlay,
                formatSec: formatSec,
                roundTrim: roundTrim,
                onCutsUpdated: updateAdoptedDuration,
            });
        }
        updateAdoptedDuration();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
