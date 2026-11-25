/* スタッフ向け録画プロキシ再生・開始終了トリミング制御 UI。 */
(function () {
    'use strict';

    function fallbackFormatSec(sec) {
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

        const tl = window.VrcRecordingCutsTimeline;
        const formatSec = tl && typeof tl.formatSec === 'function' ? tl.formatSec : fallbackFormatSec;
        const safeMin = Number(config.safeMin) || 0;
        const safeMax = Number(config.safeMax) || 0;
        const video = document.getElementById('recording-proxy-player');
        const loadingEl = document.getElementById('recording-player-loading');
        const errorEl = document.getElementById('recording-player-error');
        const currentTimeEl = document.getElementById('recording-current-time');
        const adoptedDurationEl = document.getElementById('recording-adopted-duration');
        const modeBannerEl = document.getElementById('recording-mode-banner');
        const modeBadgeEl = document.getElementById('recording-mode-badge');
        const modeDescEl = document.getElementById('recording-mode-description');
        const feedbackEl = document.getElementById('recording-playback-feedback');
        const startInput = document.getElementById('id_start_sec');
        const endInput = document.getElementById('id_end_sec');
        const startDisplayEl = document.getElementById('recording-start-display');
        const endDisplayEl = document.getElementById('recording-end-display');
        const playBeforeEndBtn = form.querySelector('[data-action="play-before-end"]');
        const playFromStartBtn = form.querySelector('[data-action="play-from-start"]');
        const setStartFromPlayerBtn = form.querySelector('[data-action="set-start-from-player"]');
        const setEndFromPlayerBtn = form.querySelector('[data-action="set-end-from-player"]');
        const playTrimRangeBtn = document.querySelector('[data-action="play-trim-range"]');

        let segmentStopAt = null;
        let pendingSeek = null;
        let isSyncingTrim = false;
        let allowBeforeStartPreview = false;
        let allowCutThroughPreview = false;
        let programmaticSeekTarget = null;
        let metadataApplied = false;
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

        function showFeedback(msg) {
            if (feedbackEl) feedbackEl.textContent = msg || '';
        }

        function updateTrimTimeDisplays() {
            if (startDisplayEl) startDisplayEl.textContent = formatSec(getStartSec());
            if (endDisplayEl) endDisplayEl.textContent = formatSec(getEndSec());
        }

        function updateModeUI() {
            const isPreview = getPlaybackMode() === 'preview';
            if (modeBannerEl) modeBannerEl.classList.toggle('is-preview', isPreview);
            if (modeBadgeEl) {
                modeBadgeEl.textContent = isPreview ? 'プレビュー中' : '全体表示中';
                modeBadgeEl.className = isPreview
                    ? 'detail-chip bg-primary-subtle text-primary-emphasis'
                    : 'detail-chip bg-secondary-subtle text-secondary-emphasis';
            }
            if (modeDescEl) {
                modeDescEl.textContent = isPreview
                    ? '開始 ' + formatSec(getStartSec()) + ' 〜 終了 ' + formatSec(getEndSec()) + ' のみ再生（余白・採用カットはスキップ）'
                    : '録画全体（' + formatSec(safeMin) + ' 〜 ' + formatSec(safeMax) + '）を余白込みで自由にシーク・再生可能';
            }
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

        function findCutContaining(sec) {
            if (!cutsEditor) return null;
            return cutsEditor.getAcceptSpans().find(function (span) {
                return sec >= span[0] && sec < span[1] - 0.05;
            }) || null;
        }

        function enforcePreviewStartBounds() {
            if (!video || video.readyState < 1) return false;
            if (getPlaybackMode() !== 'preview' || allowBeforeStartPreview) return false;
            const start = getStartSec();
            if (video.currentTime < start - 0.05) {
                programmaticSeekTarget = start;
                video.currentTime = start;
                showFeedback('プレビュー中のため開始位置（' + formatSec(start) + '）へ移動しました');
                return true;
            }
            return false;
        }

        function tryPlayVideo() {
            if (!video) return;
            const playPromise = video.play();
            if (playPromise && typeof playPromise.catch === 'function') {
                playPromise.catch(function (err) {
                    if (err && err.name === 'AbortError') return;
                    showFeedback('自動再生が制限されました。動画の再生ボタンを押してください。');
                });
            }
        }

        function setTrimValue(side, rawVal, options) {
            const emitChange = !options || options.emitChange !== false;
            const seekPausedStart = Boolean(options && options.seekPausedStart);
            let next = Number(rawVal);
            if (!Number.isFinite(next)) return null;
            isSyncingTrim = true;
            try {
                if (side === 'start') {
                    const maxAllowed = Math.max(safeMin, roundTrim(getEndSec() - 0.1));
                    next = roundTrim(Math.max(safeMin, Math.min(maxAllowed, next)));
                    startInput.value = String(next);
                    syncCandidateSelection('start_candidate', next);
                    allowBeforeStartPreview = false;
                    if (!enforcePreviewStartBounds() && seekPausedStart && video && video.readyState >= 1 && video.paused && getPlaybackMode() === 'preview') {
                        programmaticSeekTarget = next;
                        video.currentTime = next;
                    }
                    if (emitChange) startInput.dispatchEvent(new Event('change', { bubbles: true }));
                } else {
                    const minAllowed = Math.min(safeMax, roundTrim(getStartSec() + 0.1));
                    next = roundTrim(Math.min(safeMax, Math.max(minAllowed, next)));
                    endInput.value = String(next);
                    syncCandidateSelection('end_candidate', next);
                    if (segmentStopAt !== null) segmentStopAt = next;
                    if (emitChange) endInput.dispatchEvent(new Event('change', { bubbles: true }));
                }
            } finally {
                isSyncingTrim = false;
            }
            if (cutsEditor) cutsEditor.onTrimChanged();
            updateTrimTimeDisplays();
            updateModeUI();
            updateAdoptedDuration();
            return next;
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
                tryPlayVideo();
            } else {
                pendingSeek = bounded;
                tryPlayVideo();
            }
        }

        function onMetadataReady() {
            if (!video || video.readyState < 1) return;
            if (pendingSeek !== null) {
                programmaticSeekTarget = pendingSeek;
                video.currentTime = pendingSeek;
                pendingSeek = null;
                metadataApplied = true;
                return;
            }
            if (!metadataApplied && getPlaybackMode() === 'preview') {
                const start = getStartSec();
                if (start > 0 && video.currentTime < start) {
                    programmaticSeekTarget = start;
                    video.currentTime = start;
                    showFeedback('プレビュー中のため開始位置（' + formatSec(start) + '）へ移動しました');
                }
            }
            metadataApplied = true;
        }

        form.querySelectorAll('input[name="start_candidate"]').forEach(function (radio) {
            radio.addEventListener('change', function () {
                if (radio.checked) {
                    const val = setTrimValue('start', Number(radio.value), { emitChange: true });
                    if (val !== null) showFeedback('開始位置を ' + formatSec(val) + ' に設定しました');
                }
            });
        });
        form.querySelectorAll('input[name="end_candidate"]').forEach(function (radio) {
            radio.addEventListener('change', function () {
                if (radio.checked) {
                    const val = setTrimValue('end', Number(radio.value), { emitChange: true });
                    if (val !== null) showFeedback('終了位置を ' + formatSec(val) + ' に設定しました');
                }
            });
        });

        form.querySelectorAll('[data-adjust-target]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const side = btn.dataset.adjustTarget;
                const delta = Number(btn.dataset.adjustDelta) || 0;
                const base = side === 'start' ? getStartSec() : getEndSec();
                const val = setTrimValue(side, base + delta, { emitChange: true, seekPausedStart: side === 'start' });
                if (val !== null) {
                    showFeedback((side === 'start' ? '開始' : '終了') + '位置を ' + formatSec(val) + '（' + val.toFixed(1) + '秒）へ調整しました');
                }
            });
        });

        [
            { btn: setStartFromPlayerBtn, side: 'start', label: '開始' },
            { btn: setEndFromPlayerBtn, side: 'end', label: '終了' },
        ].forEach(function (item) {
            if (!item.btn) return;
            item.btn.addEventListener('click', function () {
                if (!video || video.readyState < 1) {
                    showFeedback('動画の読み込み後に実行してください');
                    return;
                }
                const val = setTrimValue(item.side, video.currentTime, { emitChange: true });
                if (val !== null) {
                    showFeedback(item.label + '位置を現在位置（' + formatSec(val) + '）に設定しました');
                }
            });
        });

        startInput.addEventListener('change', function () {
            if (!isSyncingTrim) setTrimValue('start', startInput.value, { emitChange: false });
        });
        endInput.addEventListener('change', function () {
            if (!isSyncingTrim) setTrimValue('end', endInput.value, { emitChange: false });
        });

        document.querySelectorAll('input[name="end_preview_offset"]').forEach(function (radio) {
            radio.addEventListener('change', function () {
                if (playBeforeEndBtn) playBeforeEndBtn.textContent = getEndOffset() + '秒前から再生';
            });
        });

        if (playFromStartBtn) {
            playFromStartBtn.addEventListener('click', function () {
                const start = getStartSec();
                const stopAt = getPlaybackMode() === 'preview' ? getEndSec() : null;
                showFeedback('開始位置（' + formatSec(start) + '）から再生します');
                seekAndPlay(start, stopAt);
            });
        }

        if (playBeforeEndBtn) {
            playBeforeEndBtn.addEventListener('click', function () {
                const cand = getEndSec();
                const seekTo = Math.max(safeMin, cand - getEndOffset());
                showFeedback('終了位置の ' + getEndOffset() + ' 秒前（' + formatSec(seekTo) + '）から再生します');
                seekAndPlay(seekTo, cand, { allowBeforeStart: seekTo < getStartSec() });
            });
        }

        if (playTrimRangeBtn) {
            playTrimRangeBtn.addEventListener('click', function () {
                const start = getStartSec();
                const end = getEndSec();
                showFeedback('トリミング区間（' + formatSec(start) + ' 〜 ' + formatSec(end) + '）を再生します');
                seekAndPlay(start, end);
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
                        showFeedback('開始位置（' + formatSec(start) + '）へ移動しました');
                    } else {
                        const cutSpan = findCutContaining(video.currentTime);
                        if (cutSpan && video.readyState >= 1) {
                            programmaticSeekTarget = cutSpan[1];
                            video.currentTime = cutSpan[1];
                            showFeedback('採用カット区間をスキップしました（' + formatSec(cutSpan[1]) + '）');
                        } else {
                            showFeedback('トリミング後プレビューに切り替えました');
                        }
                    }
                } else {
                    segmentStopAt = null;
                    showFeedback('余白込み全体モードに切り替えました（先頭や余白も自由に再生できます）');
                }
                updateModeUI();
                if (cutsEditor && typeof cutsEditor.onPlaybackModeChanged === 'function') {
                    cutsEditor.onPlaybackModeChanged();
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
            video.addEventListener('loadedmetadata', onMetadataReady);
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
                if (currentTimeEl) currentTimeEl.textContent = formatSec(video.currentTime);
                if (cutsEditor) cutsEditor.onPlayerTimeUpdate(video.currentTime);
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
                if (enforcePreviewStartBounds()) return;
                if (segmentStopAt !== null && video.currentTime >= segmentStopAt) {
                    video.pause();
                    video.currentTime = segmentStopAt;
                    showFeedback('終了位置（' + formatSec(segmentStopAt) + '）で停止しました');
                    segmentStopAt = null;
                    allowBeforeStartPreview = false;
                    allowCutThroughPreview = false;
                } else if (getPlaybackMode() === 'preview' && !video.paused) {
                    if (!allowCutThroughPreview) {
                        const skipSpan = findCutContaining(video.currentTime);
                        if (skipSpan) {
                            programmaticSeekTarget = skipSpan[1];
                            video.currentTime = skipSpan[1];
                            showFeedback('採用カットをスキップしました（' + formatSec(skipSpan[0]) + ' → ' + formatSec(skipSpan[1]) + '）');
                            return;
                        }
                    }
                    const end = getEndSec();
                    if (video.currentTime >= end) {
                        video.pause();
                        video.currentTime = end;
                        showFeedback('終了位置（' + formatSec(end) + '）で停止しました');
                    }
                }
            });
        }

        if (window.VrcRecordingReviewSubmit && typeof window.VrcRecordingReviewSubmit.bindSubmit === 'function') {
            window.VrcRecordingReviewSubmit.bindSubmit({
                form: form,
                config: config,
                safeMin: safeMin,
                safeMax: safeMax,
                getStartSec: getStartSec,
                getEndSec: getEndSec,
                getCutsEditor: function () { return cutsEditor; },
            });
        }

        if (window.VrcRecordingCuts && typeof window.VrcRecordingCuts.createEditor === 'function') {
            cutsEditor = window.VrcRecordingCuts.createEditor({
                form: form,
                config: config,
                video: video,
                safeMin: safeMin,
                safeMax: safeMax,
                getStartSec: getStartSec,
                getEndSec: getEndSec,
                getPlaybackMode: getPlaybackMode,
                seekAndPlay: seekAndPlay,
                showFeedback: showFeedback,
                formatSec: formatSec,
                roundTrim: roundTrim,
                onCutsUpdated: updateAdoptedDuration,
            });
        }
        updateTrimTimeDisplays();
        updateModeUI();
        updateAdoptedDuration();
        onMetadataReady();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
