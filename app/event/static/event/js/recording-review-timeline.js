/* スタッフ向け録画確認 UI のドラッグ対応タイムライン（拡大切替・ハンドル・再生ヘッド）制御。 */
(function () {
    'use strict';

    function clamp(val, minVal, maxVal) {
        return Math.min(maxVal, Math.max(minVal, val));
    }

    function roundTenth(val) {
        return Math.round(val * 10) / 10;
    }

    function createTimelineController(options) {
        const tl = window.VrcRecordingCutsTimeline;
        const formatSec = options.formatSec || (tl ? tl.formatSec : function (v) { return String(v); });
        const formatAxisSec = tl && typeof tl.formatAxisSec === 'function' ? tl.formatAxisSec : formatSec;
        const computeZoomWindow = tl && typeof tl.computeZoomWindow === 'function'
            ? tl.computeZoomWindow
            : function (_m, _s, _e, lo, hi) { return { viewMin: lo, viewMax: hi }; };

        const timelineBar = options.timelineBar;
        const handleStartEl = options.handleStartEl;
        const handleEndEl = options.handleEndEl;
        const playheadEl = options.playheadEl;
        const tooltipEl = options.tooltipEl;
        const axisMinEl = options.axisMinEl;
        const axisMaxEl = options.axisMaxEl;
        const zoomButtons = Array.prototype.slice.call(options.zoomButtons || []);

        const safeMin = Number(options.safeMin) || 0;
        const safeMax = Number(options.safeMax) || 0;
        const totalSpan = Math.max(0, safeMax - safeMin);
        const minGap = totalSpan > 0 ? Math.min(0.1, Math.max(0.01, totalSpan / 2)) : 0;

        let canSubmit = Boolean(options.canSubmit);
        let zoomMode = 'all';
        let viewMin = safeMin;
        let viewMax = safeMax;
        let dragState = null; // { type: 'start'|'end'|'playhead', pointerId: number, currentVal: number }

        function toPct(sec) {
            const span = viewMax - viewMin;
            if (span <= 0) return 0;
            return clamp(((sec - viewMin) / span) * 100, 0, 100);
        }

        function fromClientX(clientX) {
            if (!timelineBar) return viewMin;
            const rect = timelineBar.getBoundingClientRect();
            if (rect.width <= 0 || viewMax <= viewMin) return viewMin;
            const ratio = clamp((clientX - rect.left) / rect.width, 0, 1);
            return roundTenth(viewMin + ratio * (viewMax - viewMin));
        }

        function updateTooltip(sec, label) {
            if (!tooltipEl) return;
            tooltipEl.textContent = label + ' ' + formatSec(sec);
            tooltipEl.style.left = toPct(sec).toFixed(3) + '%';
            tooltipEl.hidden = false;
        }

        function hideTooltip() {
            if (tooltipEl) tooltipEl.hidden = true;
        }

        function syncPositions(state) {
            const startSec = state && Number.isFinite(state.startSec) ? state.startSec : options.getStartSec();
            const endSec = state && Number.isFinite(state.endSec) ? state.endSec : options.getEndSec();
            const curSec = state && Number.isFinite(state.currentSec) ? state.currentSec : options.getCurrentSec();
            const eps = 0.001;

            if (axisMinEl) axisMinEl.textContent = formatAxisSec(viewMin);
            if (axisMaxEl) axisMaxEl.textContent = formatAxisSec(viewMax);

            if (handleStartEl) {
                const startVisible = totalSpan > 0 && startSec >= viewMin - eps && startSec <= viewMax + eps;
                handleStartEl.hidden = !startVisible;
                handleStartEl.disabled = !canSubmit;
                if (startVisible) {
                    handleStartEl.style.left = toPct(startSec).toFixed(3) + '%';
                }
            }
            if (handleEndEl) {
                const endVisible = totalSpan > 0 && endSec >= viewMin - eps && endSec <= viewMax + eps;
                handleEndEl.hidden = !endVisible;
                handleEndEl.disabled = !canSubmit;
                if (endVisible) {
                    handleEndEl.style.left = toPct(endSec).toFixed(3) + '%';
                }
            }
            if (playheadEl) {
                const headVisible = totalSpan > 0 && curSec >= viewMin - eps && curSec <= viewMax + eps;
                playheadEl.hidden = !headVisible;
                if (headVisible) {
                    playheadEl.style.left = toPct(curSec).toFixed(3) + '%';
                }
            }
        }

        function setZoom(mode) {
            zoomMode = mode === 'start' || mode === 'end' ? mode : 'all';
            const win = computeZoomWindow(zoomMode, options.getStartSec(), options.getEndSec(), safeMin, safeMax);
            viewMin = win.viewMin;
            viewMax = win.viewMax;

            zoomButtons.forEach(function (btn) {
                const active = btn.dataset.zoomMode === zoomMode;
                btn.classList.toggle('is-active', active);
                btn.setAttribute('aria-pressed', active ? 'true' : 'false');
            });

            if (typeof options.onZoomChanged === 'function') {
                options.onZoomChanged({ zoomMode: zoomMode, viewMin: viewMin, viewMax: viewMax });
            }
            syncPositions();
        }

        function applyDragMove(clientX) {
            if (!dragState || totalSpan <= 0) return;
            const rawSec = fromClientX(clientX);
            if (dragState.type === 'start') {
                const maxStart = Math.max(safeMin, roundTenth(options.getEndSec() - minGap));
                const next = clamp(rawSec, safeMin, maxStart);
                dragState.currentVal = next;
                updateTooltip(next, '開始');
                if (typeof options.onDragPreview === 'function') {
                    options.onDragPreview('start', next);
                }
            } else if (dragState.type === 'end') {
                const minEnd = Math.min(safeMax, roundTenth(options.getStartSec() + minGap));
                const next = clamp(rawSec, minEnd, safeMax);
                dragState.currentVal = next;
                updateTooltip(next, '終了');
                if (typeof options.onDragPreview === 'function') {
                    options.onDragPreview('end', next);
                }
            } else if (dragState.type === 'playhead') {
                const next = clamp(rawSec, safeMin, safeMax);
                dragState.currentVal = next;
                updateTooltip(next, '現在');
                syncPositions({
                    startSec: options.getStartSec(),
                    endSec: options.getEndSec(),
                    currentSec: next,
                });
                if (typeof options.onPlayheadPreview === 'function') {
                    options.onPlayheadPreview(next);
                }
            }
        }

        function finishDrag(ev) {
            if (!dragState) return;
            if (ev && ev.pointerId !== undefined && dragState.pointerId !== undefined && ev.pointerId !== dragState.pointerId) {
                return;
            }
            const finished = dragState;
            dragState = null;
            if (handleStartEl) handleStartEl.classList.remove('is-dragging');
            if (handleEndEl) handleEndEl.classList.remove('is-dragging');
            if (playheadEl) playheadEl.classList.remove('is-dragging');
            hideTooltip();

            if (finished.type === 'start' || finished.type === 'end') {
                if (typeof options.onDragCommit === 'function') {
                    options.onDragCommit(finished.type, finished.currentVal);
                }
            } else if (finished.type === 'playhead') {
                if (typeof options.onPlayheadSeek === 'function') {
                    options.onPlayheadSeek(finished.currentVal);
                }
            }
        }

        function beginDrag(type, targetEl, ev) {
            if (totalSpan <= 0) return;
            if (ev.pointerType === 'mouse' && ev.button !== 0) return;
            if ((type === 'start' || type === 'end') && !canSubmit) return;

            ev.stopPropagation();
            ev.preventDefault();

            const pointerId = ev.pointerId;
            if (targetEl && typeof targetEl.setPointerCapture === 'function' && pointerId !== undefined) {
                try {
                    targetEl.setPointerCapture(pointerId);
                } catch (_) {
                    // Pointer capture unavailable in synthetic pointer tests.
                }
            }
            dragState = {
                type: type,
                pointerId: pointerId,
                currentVal: type === 'start' ? options.getStartSec() : (type === 'end' ? options.getEndSec() : options.getCurrentSec()),
            };
            if (type === 'start' && handleStartEl) handleStartEl.classList.add('is-dragging');
            if (type === 'end' && handleEndEl) handleEndEl.classList.add('is-dragging');
            if (type === 'playhead' && playheadEl) playheadEl.classList.add('is-dragging');
            applyDragMove(ev.clientX);
        }

        function bindPointerTarget(el, type) {
            if (!el) return;
            el.addEventListener('pointerdown', function (ev) {
                beginDrag(type, el, ev);
            });
            el.addEventListener('pointermove', function (ev) {
                if (!dragState || dragState.type !== type) return;
                if (ev.pointerId !== undefined && dragState.pointerId !== undefined && ev.pointerId !== dragState.pointerId) return;
                applyDragMove(ev.clientX);
            });
            el.addEventListener('pointerup', finishDrag);
            el.addEventListener('pointercancel', finishDrag);
        }

        bindPointerTarget(handleStartEl, 'start');
        bindPointerTarget(handleEndEl, 'end');
        bindPointerTarget(playheadEl, 'playhead');

        if (timelineBar) {
            timelineBar.addEventListener('pointerdown', function (ev) {
                if (ev.target.closest('.recording-tl-handle, .recording-tl-cand, .recording-tl-cut, .recording-tl-playhead')) {
                    return;
                }
                beginDrag('playhead', timelineBar, ev);
            });
            timelineBar.addEventListener('pointermove', function (ev) {
                if (!dragState || dragState.type !== 'playhead') return;
                if (ev.pointerId !== undefined && dragState.pointerId !== undefined && ev.pointerId !== dragState.pointerId) return;
                applyDragMove(ev.clientX);
            });
            timelineBar.addEventListener('pointerup', finishDrag);
            timelineBar.addEventListener('pointercancel', finishDrag);
        }

        window.addEventListener('pointermove', function (ev) {
            if (!dragState) return;
            if (ev.pointerId !== undefined && dragState.pointerId !== undefined && ev.pointerId !== dragState.pointerId) return;
            applyDragMove(ev.clientX);
        });
        window.addEventListener('pointerup', finishDrag);
        window.addEventListener('pointercancel', finishDrag);

        zoomButtons.forEach(function (btn) {
            btn.addEventListener('click', function () {
                setZoom(btn.dataset.zoomMode || 'all');
            });
        });

        setZoom('all');

        return {
            setZoom: setZoom,
            getWindow: function () {
                return { zoomMode: zoomMode, viewMin: viewMin, viewMax: viewMax };
            },
            isDragging: function () {
                return dragState !== null;
            },
            setCanSubmit: function (nextCanSubmit) {
                canSubmit = Boolean(nextCanSubmit);
                syncPositions();
            },
            syncPositions: syncPositions,
        };
    }

    window.VrcRecordingTimelineController = {
        createTimelineController: createTimelineController,
    };
})();
