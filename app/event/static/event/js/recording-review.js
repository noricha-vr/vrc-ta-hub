/* スタッフ向け録画プロキシ再生・開始終了トリミング統合ドック制御。 */
(function () {
    'use strict';

    function fallbackFormatSec(sec) {
        const t = Math.round(Math.max(0, Number(sec) || 0) * 10);
        const w = Math.floor(t / 10);
        const s = String(w % 60).padStart(2, '0');
        const m = Math.floor(w / 60);
        const h = Math.floor(m / 60);
        return (h > 0 ? h + ':' + String(m % 60).padStart(2, '0') + ':' + s : m + ':' + s) + '.' + (t % 10);
    }

    function roundTrim(val) {
        return Math.round(val * 1000) / 1000;
    }

    function parseJsonEl(id, fallback) {
        const el = document.getElementById(id);
        if (!el) return fallback;
        try {
            return JSON.parse(el.textContent || '');
        } catch (_) {
            return fallback;
        }
    }

    function init() {
        const config = parseJsonEl('recording-review-config', null);
        const form = document.getElementById('recording-trim-form');
        if (!config || !form) return;

        const candidates = parseJsonEl('recording-candidates-data', { start: [], end: [] });
        const tl = window.VrcRecordingCutsTimeline;
        const formatSec = tl && typeof tl.formatSec === 'function' ? tl.formatSec : fallbackFormatSec;
        const parseTimeInput = tl && typeof tl.parseTimeInput === 'function'
            ? tl.parseTimeInput
            : function (s) { const n = Number(s); return Number.isFinite(n) && n >= 0 ? n : null; };

        const safeMin = Number(config.safeMin) || 0;
        const safeMax = Number(config.safeMax) || 0;
        const video = document.getElementById('recording-proxy-player');
        const currentTimeEl = document.getElementById('recording-current-time');
        const adoptedDurationEl = document.getElementById('recording-adopted-duration');
        const modeBannerEl = document.getElementById('recording-mode-banner');
        const modeBadgeEl = document.getElementById('recording-mode-badge');
        const zoneBadgeEl = document.getElementById('recording-zone-badge');
        const modeDescEl = document.getElementById('recording-mode-description');
        const feedbackEl = document.getElementById('recording-playback-feedback');
        const startInput = document.getElementById('id_start_sec');
        const endInput = document.getElementById('id_end_sec');
        const startRawInput = document.getElementById('id_start_sec_raw');
        const endRawInput = document.getElementById('id_end_sec_raw');
        const endOffsetSelect = document.getElementById('recording-end-preview-offset');

        let isSyncingTrim = false;
        let cutsEditor = null;
        let timelineCtrl = null;
        let playerCtrl = null;

        function getStartSec() {
            const parsed = Number(startRawInput ? startRawInput.value : (startInput ? startInput.value : ''));
            return Number.isFinite(parsed) ? parsed : safeMin;
        }

        function getEndSec() {
            const parsed = Number(endRawInput ? endRawInput.value : (endInput ? endInput.value : ''));
            return Number.isFinite(parsed) ? parsed : safeMax;
        }

        function getCurrentPlayerSec() {
            return playerCtrl ? playerCtrl.getCurrentPlayerSec() : getStartSec();
        }

        function getPlaybackMode() {
            const checked = document.querySelector('input[name="playback_mode"]:checked');
            return checked ? checked.value : 'preview';
        }

        function getEndOffset() {
            const parsed = endOffsetSelect ? Number(endOffsetSelect.value) : 7;
            return Number.isFinite(parsed) && parsed > 0 ? parsed : 7;
        }

        function showFeedback(msg) {
            if (feedbackEl) feedbackEl.textContent = msg || '';
        }

        function syncTrimDisplays(forceDisplay) {
            if (startInput && (forceDisplay || document.activeElement !== startInput)) startInput.value = formatSec(getStartSec());
            if (endInput && (forceDisplay || document.activeElement !== endInput)) endInput.value = formatSec(getEndSec());
        }

        function syncCandidateChips() {
            const s = getStartSec();
            const e = getEndSec();
            form.querySelectorAll('[data-candidate-side], [data-candidate-reset]').forEach(function (btn) {
                const side = btn.dataset.candidateSide || btn.dataset.candidateReset;
                const candSec = Number(btn.dataset.candidateSec);
                const active = Number.isFinite(candSec) && Math.abs((side === 'start' ? s : e) - candSec) < 0.05;
                btn.classList.toggle('is-active', active);
                btn.setAttribute('aria-pressed', active ? 'true' : 'false');
            });
        }

        function updateZoneBadge(sec) {
            if (!zoneBadgeEl) return;
            const cur = Number.isFinite(sec) ? sec : getCurrentPlayerSec();
            if (cur < getStartSec() - 0.05) {
                zoneBadgeEl.textContent = '冒頭の余白（除外）';
                zoneBadgeEl.className = 'recording-toolbar-chip';
            } else if (cur > getEndSec() + 0.05) {
                zoneBadgeEl.textContent = '末尾の余白（除外）';
                zoneBadgeEl.className = 'recording-toolbar-chip';
            } else if (findCutContaining(cur)) {
                zoneBadgeEl.textContent = '途中カット（除外）';
                zoneBadgeEl.className = 'recording-toolbar-chip';
            } else {
                zoneBadgeEl.textContent = '本編（採用区間）';
                zoneBadgeEl.className = 'recording-toolbar-chip is-zone-keep';
            }
        }

        function updateModeUI() {
            const isPreview = getPlaybackMode() === 'preview';
            if (modeBannerEl) modeBannerEl.classList.toggle('is-preview', isPreview);
            if (modeBadgeEl) {
                modeBadgeEl.textContent = isPreview ? 'プレビュー中' : '全体表示中';
                modeBadgeEl.className = isPreview ? 'recording-toolbar-chip is-preview' : 'recording-toolbar-chip';
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

        function syncTimelinePositions(sec, forceSync) {
            const cur = Number.isFinite(sec) ? sec : getCurrentPlayerSec();
            updateZoneBadge(cur);
            if (timelineCtrl && (forceSync || !timelineCtrl.isDragging())) {
                timelineCtrl.syncPositions({ startSec: getStartSec(), endSec: getEndSec(), currentSec: cur });
            }
        }

        function findCutContaining(sec) {
            if (!cutsEditor) return null;
            return cutsEditor.getAcceptSpans().find(function (span) {
                return sec >= span[0] && sec < span[1] - 0.05;
            }) || null;
        }

        function clampTrimSide(side, rawVal) {
            const totalSpan = Math.max(0, safeMax - safeMin);
            const minGap = totalSpan > 0 ? Math.min(0.1, Math.max(0.01, totalSpan / 2)) : 0;
            const parsed = Number(rawVal);
            if (!Number.isFinite(parsed)) return null;
            if (side === 'start') {
                return roundTrim(Math.max(safeMin, Math.min(Math.max(safeMin, roundTrim(getEndSec() - minGap)), parsed)));
            }
            return roundTrim(Math.min(safeMax, Math.max(Math.min(safeMax, roundTrim(getStartSec() + minGap)), parsed)));
        }

        function refreshTrimViews(forceSync) {
            syncTrimDisplays(true);
            syncCandidateChips();
            if (cutsEditor) cutsEditor.onTrimChanged();
            updateModeUI();
            updateAdoptedDuration();
            syncTimelinePositions(undefined, forceSync);
        }

        function previewTrimValue(side, rawVal) {
            const next = clampTrimSide(side, rawVal);
            if (next === null) return null;
            if (side === 'start' && startRawInput) startRawInput.value = String(next);
            if (side === 'end' && endRawInput) endRawInput.value = String(next);
            refreshTrimViews(true);
            return next;
        }

        function setTrimValue(side, rawVal, options) {
            const emitChange = !options || options.emitChange !== false;
            const seekPausedStart = Boolean(options && options.seekPausedStart);
            const forceDisplaySync = Boolean(options && options.forceDisplaySync);
            const next = clampTrimSide(side, rawVal);
            if (next === null) return null;

            isSyncingTrim = true;
            try {
                const targetRaw = side === 'start' ? startRawInput : endRawInput;
                if (targetRaw) targetRaw.value = String(next);
                if (playerCtrl) {
                    if (side === 'start') playerCtrl.onStartTrimChanged(next, seekPausedStart);
                    else playerCtrl.onEndTrimChanged(next);
                }
                if (emitChange && targetRaw) targetRaw.dispatchEvent(new Event('change', { bubbles: true }));
            } finally {
                isSyncingTrim = false;
            }
            syncTrimDisplays(forceDisplaySync);
            syncCandidateChips();
            if (cutsEditor) cutsEditor.onTrimChanged();
            updateModeUI();
            updateAdoptedDuration();
            syncTimelinePositions();
            return next;
        }

        function seekAndPlay(targetSec, stopAtSec, options) {
            if (playerCtrl) playerCtrl.seekAndPlay(targetSec, stopAtSec, options);
        }

        function applyCandidate(side, rawSec, isReset) {
            const val = setTrimValue(side, rawSec, { emitChange: true, forceDisplaySync: true });
            if (val === null) return;
            if (isReset) {
                showFeedback((side === 'start' ? '開始位置を先頭（' : '終了位置を末尾（') + formatSec(val) + '）に戻しました');
                return;
            }
            if (side === 'start') {
                showFeedback('開始候補（' + formatSec(val) + '）を適用して再生します');
                seekAndPlay(val, getPlaybackMode() === 'preview' ? getEndSec() : null);
            } else {
                const offset = getEndOffset();
                const seekTo = Math.max(safeMin, val - offset);
                showFeedback('終了候補（' + formatSec(val) + '）を適用し、' + offset + ' 秒前から再生します');
                seekAndPlay(seekTo, val, { allowBeforeStart: seekTo < getStartSec() });
            }
        }

        function bindTimecodeInput(inputEl, side) {
            if (!inputEl) return;
            inputEl.addEventListener('focus', function () {
                if (typeof inputEl.select === 'function') inputEl.select();
            });
            inputEl.addEventListener('keydown', function (ev) {
                if (ev.key === 'Enter') {
                    ev.preventDefault();
                    inputEl.blur();
                }
            });
            inputEl.addEventListener('change', function () {
                if (isSyncingTrim) return;
                const parsed = parseTimeInput(inputEl.value);
                if (parsed === null) {
                    showFeedback('時刻は 90.5 または 1:30.5 の形式で入力してください');
                    syncTrimDisplays(true);
                    return;
                }
                const applied = setTrimValue(side, parsed, { emitChange: true, seekPausedStart: side === 'start', forceDisplaySync: true });
                if (applied !== null) showFeedback((side === 'start' ? '開始' : '終了') + '位置を ' + formatSec(applied) + ' に設定しました');
            });
        }

        if (window.VrcRecordingPlayerController) {
            playerCtrl = window.VrcRecordingPlayerController.createPlayerController({
                video: video,
                safeMin: safeMin,
                safeMax: safeMax,
                formatSec: formatSec,
                loadingEl: document.getElementById('recording-player-loading'),
                errorEl: document.getElementById('recording-player-error'),
                currentTimeEl: currentTimeEl,
                getStartSec: getStartSec,
                getEndSec: getEndSec,
                getPlaybackMode: getPlaybackMode,
                findCutContaining: findCutContaining,
                showFeedback: showFeedback,
                onTimeChanged: function (sec) {
                    if (cutsEditor) cutsEditor.onPlayerTimeUpdate(sec);
                },
            });
        }

        bindTimecodeInput(startInput, 'start');
        bindTimecodeInput(endInput, 'end');

        form.querySelectorAll('[data-candidate-side]').forEach(function (btn) {
            btn.addEventListener('click', function () { applyCandidate(btn.dataset.candidateSide, Number(btn.dataset.candidateSec), false); });
        });
        form.querySelectorAll('[data-candidate-reset]').forEach(function (btn) {
            btn.addEventListener('click', function () { applyCandidate(btn.dataset.candidateReset, Number(btn.dataset.candidateSec), true); });
        });
        form.querySelectorAll('[data-adjust-target]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const side = btn.dataset.adjustTarget;
                const base = side === 'start' ? getStartSec() : getEndSec();
                const val = setTrimValue(side, base + (Number(btn.dataset.adjustDelta) || 0), {
                    emitChange: true,
                    seekPausedStart: side === 'start',
                    forceDisplaySync: true,
                });
                if (val !== null) showFeedback((side === 'start' ? '開始' : '終了') + '位置を ' + formatSec(val) + '（' + val.toFixed(1) + '秒）へ調整しました');
            });
        });

        [
            { sel: '[data-action="set-start-from-player"]', side: 'start', label: '開始' },
            { sel: '[data-action="set-end-from-player"]', side: 'end', label: '終了' },
        ].forEach(function (item) {
            const btn = form.querySelector(item.sel);
            if (!btn) return;
            btn.addEventListener('click', function () {
                if (!video || video.readyState < 1) {
                    showFeedback('動画の読み込み後に実行してください');
                    return;
                }
                const val = setTrimValue(item.side, video.currentTime, { emitChange: true, forceDisplaySync: true });
                if (val !== null) showFeedback(item.label + '位置を現在位置（' + formatSec(val) + '）に設定しました');
            });
        });

        const playFromStartBtn = form.querySelector('[data-action="play-from-start"]');
        const playBeforeEndBtn = form.querySelector('[data-action="play-before-end"]');
        const playTrimRangeBtn = document.querySelector('[data-action="play-trim-range"]');
        if (playFromStartBtn) {
            playFromStartBtn.addEventListener('click', function () {
                const start = getStartSec();
                showFeedback('開始位置（' + formatSec(start) + '）から再生します');
                seekAndPlay(start, getPlaybackMode() === 'preview' ? getEndSec() : null);
            });
        }
        if (playBeforeEndBtn) {
            playBeforeEndBtn.addEventListener('click', function () {
                const cand = getEndSec();
                const offset = getEndOffset();
                const seekTo = Math.max(safeMin, cand - offset);
                showFeedback('終了位置の ' + offset + ' 秒前（' + formatSec(seekTo) + '）から再生します');
                seekAndPlay(seekTo, cand, { allowBeforeStart: seekTo < getStartSec() });
            });
        }
        if (playTrimRangeBtn) {
            playTrimRangeBtn.addEventListener('click', function () {
                const start = getStartSec();
                showFeedback('開始位置（' + formatSec(start) + '）から再生します');
                seekAndPlay(start, getEndSec());
            });
        }

        document.querySelectorAll('input[name="playback_mode"]').forEach(function (radio) {
            radio.addEventListener('change', function () {
                if (playerCtrl) playerCtrl.onPlaybackModeChanged();
                updateModeUI();
                if (cutsEditor && typeof cutsEditor.onPlaybackModeChanged === 'function') cutsEditor.onPlaybackModeChanged();
            });
        });

        if (window.VrcRecordingTimelineController && document.getElementById('recording-timeline-bar')) {
            timelineCtrl = window.VrcRecordingTimelineController.createTimelineController({
                timelineBar: document.getElementById('recording-timeline-bar'),
                handleStartEl: document.getElementById('recording-tl-handle-start'),
                handleEndEl: document.getElementById('recording-tl-handle-end'),
                playheadEl: document.getElementById('recording-tl-playhead'),
                tooltipEl: document.getElementById('recording-tl-tooltip'),
                axisMinEl: document.getElementById('recording-tl-axis-min'),
                axisMaxEl: document.getElementById('recording-tl-axis-max'),
                zoomButtons: document.querySelectorAll('[data-zoom-mode]'),
                safeMin: safeMin,
                safeMax: safeMax,
                canSubmit: Boolean(config.canSubmit),
                getStartSec: getStartSec,
                getEndSec: getEndSec,
                getCurrentSec: getCurrentPlayerSec,
                getPlaybackMode: getPlaybackMode,
                formatSec: formatSec,
                onDragPreview: previewTrimValue,
                onDragCommit: function (side, finalVal) {
                    const applied = setTrimValue(side, finalVal, { emitChange: true, seekPausedStart: side === 'start', forceDisplaySync: true });
                    if (applied !== null) showFeedback((side === 'start' ? '開始' : '終了') + '位置を ' + formatSec(applied) + ' に調整しました');
                },
                onPlayheadPreview: function (sec) {
                    if (currentTimeEl) currentTimeEl.textContent = formatSec(sec);
                    updateZoneBadge(sec);
                },
                onPlayheadSeek: function (sec) {
                    if (playerCtrl) playerCtrl.seekFromTimeline(sec);
                },
                onZoomChanged: function () {
                    if (cutsEditor && typeof cutsEditor.onZoomChanged === 'function') cutsEditor.onZoomChanged();
                },
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
                onSubmitLockChanged: function (unlocked) {
                    if (timelineCtrl) timelineCtrl.setCanSubmit(unlocked);
                },
            });
        }

        if (window.VrcRecordingCuts && typeof window.VrcRecordingCuts.createEditor === 'function') {
            cutsEditor = window.VrcRecordingCuts.createEditor({
                form: form,
                config: config,
                video: video,
                candidates: candidates,
                safeMin: safeMin,
                safeMax: safeMax,
                getStartSec: getStartSec,
                getEndSec: getEndSec,
                getPlaybackMode: getPlaybackMode,
                getTimelineWindow: function () {
                    return timelineCtrl ? timelineCtrl.getWindow() : { viewMin: safeMin, viewMax: safeMax };
                },
                onTimelineSync: syncTimelinePositions,
                onCandidateClick: function (side, sec) { applyCandidate(side, sec, false); },
                seekAndPlay: seekAndPlay,
                showFeedback: showFeedback,
                formatSec: formatSec,
                roundTrim: roundTrim,
                onCutsUpdated: updateAdoptedDuration,
            });
        }

        syncTrimDisplays(true);
        syncCandidateChips();
        updateModeUI();
        updateAdoptedDuration();
        syncTimelinePositions();
        if (playerCtrl) playerCtrl.onMetadataReady();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
