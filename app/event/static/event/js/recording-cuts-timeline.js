/* スタッフ向け録画確認 UI の時刻変換・途中カット検証・タイムライン背景描画。 */
(function () {
    'use strict';

    const MIN_KEEP_SEC = 1.0;
    const ZOOM_HALF_WINDOW_SEC = 120;
    const KIND_LABELS = {
        silence: '無音区間',
        trouble: '機材トラブル',
        break: '離席・休憩',
        manual: '手動カット',
        other: 'その他',
    };
    const ACTION_META = {
        pending: { label: '保留中', badgeClass: 'bg-warning-subtle text-warning-emphasis' },
        accept: { label: '採用', badgeClass: 'bg-danger-subtle text-danger-emphasis' },
        reject: { label: '却下', badgeClass: 'bg-secondary-subtle text-secondary-emphasis' },
    };

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

    function formatAxisSec(sec) {
        const clamped = Math.max(0, Math.round(Number(sec) || 0));
        const mins = Math.floor(clamped / 60);
        const rem = clamped % 60;
        const hrs = Math.floor(mins / 60);
        const remMin = mins % 60;
        if (hrs > 0) {
            return hrs + ':' + String(remMin).padStart(2, '0') + ':' + String(rem).padStart(2, '0');
        }
        return remMin + ':' + String(rem).padStart(2, '0');
    }

    function parseTimeInput(rawStr) {
        const text = String(rawStr === undefined || rawStr === null ? '' : rawStr).trim();
        if (!text) return null;
        if (/^\d+(?:\.\d+)?$/.test(text)) {
            const val = Number(text);
            return Number.isFinite(val) && val >= 0 ? val : null;
        }
        const parts = text.split(':');
        if (parts.length !== 2 && parts.length !== 3) return null;
        const nums = [];
        for (let i = 0; i < parts.length; i += 1) {
            const p = parts[i].trim();
            const isLast = i === parts.length - 1;
            const pattern = isLast ? /^\d+(?:\.\d+)?$/ : /^\d+$/;
            if (!pattern.test(p)) return null;
            const n = Number(p);
            if (!Number.isFinite(n) || n < 0) return null;
            nums.push(n);
        }
        if (nums.length === 2) {
            if (nums[1] >= 60) return null;
            return nums[0] * 60 + nums[1];
        }
        if (nums[1] >= 60 || nums[2] >= 60) return null;
        return nums[0] * 3600 + nums[1] * 60 + nums[2];
    }

    function computeZoomWindow(mode, startSec, endSec, safeMin, safeMax) {
        const total = Math.max(0, safeMax - safeMin);
        if (total <= 0 || mode === 'all') {
            return { viewMin: safeMin, viewMax: safeMax };
        }
        const span = Math.min(total, ZOOM_HALF_WINDOW_SEC * 2);
        if (mode === 'start') {
            let vMin = Math.max(safeMin, startSec - ZOOM_HALF_WINDOW_SEC);
            let vMax = Math.min(safeMax, vMin + span);
            if (vMax - vMin < span) {
                vMin = Math.max(safeMin, vMax - span);
            }
            return { viewMin: vMin, viewMax: vMax };
        }
        let vMax = Math.min(safeMax, endSec + ZOOM_HALF_WINDOW_SEC);
        let vMin = Math.max(safeMin, vMax - span);
        if (vMax - vMin < span) {
            vMax = Math.min(safeMax, vMin + span);
        }
        return { viewMin: vMin, viewMax: vMax };
    }

    function createEl(tag, className, text) {
        const el = document.createElement(tag);
        if (className) el.className = className;
        if (text !== undefined && text !== null) el.textContent = String(text);
        return el;
    }

    function sortCuts(cuts) {
        cuts.sort(function (a, b) {
            const diffStart = Number(a.start_sec) - Number(b.start_sec);
            if (diffStart !== 0) return diffStart;
            const diffEnd = Number(a.end_sec) - Number(b.end_sec);
            if (diffEnd !== 0) return diffEnd;
            return String(a.id).localeCompare(String(b.id));
        });
    }

    function evaluateCutsState(cuts, options) {
        const trimStart = options.trimStart;
        const trimEnd = options.trimEnd;
        const allowAcceptCuts = options.allowAcceptCuts;
        const fmt = options.formatSec || formatSec;

        const cutErrors = new Map();
        const globalErrors = [];
        let acceptCount = 0;
        let pendingCount = 0;
        let rejectCount = 0;

        sortCuts(cuts);
        let prevAccept = null;
        const validAccepts = [];

        cuts.forEach(function (cut) {
            const errs = [];
            if (cut.action === 'accept') acceptCount += 1;
            else if (cut.action === 'pending') pendingCount += 1;
            else rejectCount += 1;

            if (!(trimStart <= cut.start_sec && cut.start_sec < cut.end_sec && cut.end_sec <= trimEnd)) {
                errs.push(
                    '開始〜終了の範囲（' + fmt(trimStart) + ' 〜 ' + fmt(trimEnd) + '）外です。区間を調整または削除してください。'
                );
            }
            if (cut.action === 'accept') {
                if (prevAccept && cut.start_sec <= prevAccept.end_sec) {
                    errs.push(
                        '他の採用カット（' + fmt(prevAccept.start_sec) + ' 〜 ' + fmt(prevAccept.end_sec) + '）と重複または接触しています。'
                    );
                    const prevErrs = cutErrors.get(prevAccept.id) || [];
                    prevErrs.push('後続の採用カットと重複または接触しています。');
                    cutErrors.set(prevAccept.id, prevErrs);
                } else if (trimStart <= cut.start_sec && cut.start_sec < cut.end_sec && cut.end_sec <= trimEnd) {
                    validAccepts.push(cut);
                }
                prevAccept = cut;
            }
            if (errs.length > 0) {
                cutErrors.set(cut.id, errs);
            }
        });

        const keepSpans = [];
        let adoptedDurationSec = 0;
        if (trimEnd > trimStart) {
            let cursor = trimStart;
            validAccepts.forEach(function (cut) {
                if (cut.start_sec > cursor) {
                    keepSpans.push([cursor, cut.start_sec]);
                    adoptedDurationSec += cut.start_sec - cursor;
                }
                cursor = Math.max(cursor, cut.end_sec);
            });
            if (cursor < trimEnd) {
                keepSpans.push([cursor, trimEnd]);
                adoptedDurationSec += trimEnd - cursor;
            }
        }
        adoptedDurationSec = Math.round(adoptedDurationSec * 1000) / 1000;

        if (cutErrors.size > 0) {
            globalErrors.push('開始〜終了の範囲外のカットまたは重複している採用カットがあります。各カードの赤字を確認してください。');
        }
        if (trimEnd > trimStart && adoptedDurationSec < MIN_KEEP_SEC) {
            globalErrors.push(
                '採用時間が ' + adoptedDurationSec.toFixed(1) + ' 秒です。' + MIN_KEEP_SEC.toFixed(1) + ' 秒以上残るように調整してください。'
            );
        }

        return {
            trimStart: trimStart,
            trimEnd: trimEnd,
            cutErrors: cutErrors,
            globalErrors: globalErrors,
            keepSpans: keepSpans,
            adoptedDurationSec: adoptedDurationSec,
            acceptCount: acceptCount,
            pendingCount: pendingCount,
            rejectCount: rejectCount,
            acceptBlocked: !allowAcceptCuts && acceptCount > 0,
        };
    }

    function secToPctInWindow(sec, viewMin, span) {
        if (span <= 0) return 0;
        return Math.max(0, Math.min(100, ((sec - viewMin) / span) * 100));
    }

    function appendMarginBand(container, startPct, endPct, title, edgeClass) {
        if (endPct - startPct <= 0.05) return;
        const margin = createEl('div', 'recording-tl-margin' + (edgeClass ? ' ' + edgeClass : ''));
        margin.style.left = startPct.toFixed(3) + '%';
        margin.style.width = (endPct - startPct).toFixed(3) + '%';
        margin.title = title;
        container.appendChild(margin);
    }

    function renderCandidateMarkers(container, candidates, viewMin, viewMax, span, params) {
        const canSubmit = Boolean(params.canSubmit);
        const fmt = params.formatSec;
        const onCandidateClick = params.onCandidateClick;
        [
            { side: 'start', list: (candidates && candidates.start) || [], labelPrefix: '開始候補' },
            { side: 'end', list: (candidates && candidates.end) || [], labelPrefix: '終了候補' },
        ].forEach(function (group) {
            group.list.forEach(function (cand) {
                const sec = Number(cand.sec);
                if (!Number.isFinite(sec) || sec < viewMin || sec > viewMax) return;
                const pct = secToPctInWindow(sec, viewMin, span);
                const btn = createEl('button', 'recording-tl-cand is-' + group.side);
                btn.type = 'button';
                btn.disabled = !canSubmit;
                btn.style.left = pct.toFixed(3) + '%';
                btn.dataset.candidateSide = group.side;
                btn.dataset.candidateSec = String(sec);
                btn.dataset.testid = 'timeline-cand-' + group.side;
                const detailLabel = cand.label ? ' (' + cand.label + ')' : '';
                btn.title = group.labelPrefix + ' ' + fmt(sec) + detailLabel + ' を適用して確認再生';
                btn.setAttribute('aria-label', btn.title);
                btn.addEventListener('pointerdown', function (ev) {
                    ev.stopPropagation();
                });
                btn.addEventListener('click', function (ev) {
                    ev.stopPropagation();
                    if (typeof onCandidateClick === 'function') {
                        onCandidateClick(group.side, sec);
                    }
                });
                container.appendChild(btn);
            });
        });
    }

    function renderTimelineLayers(params) {
        const timelineBar = params.timelineBar;
        const layersEl = params.layersEl || timelineBar;
        const keepSpansSummaryEl = params.keepSpansSummaryEl;
        const state = params.state;
        const cuts = params.cuts;
        const ctx = params.ctx;
        const viewMin = Number.isFinite(params.viewMin) ? params.viewMin : ctx.safeMin;
        const viewMax = Number.isFinite(params.viewMax) ? params.viewMax : ctx.safeMax;

        if (keepSpansSummaryEl) {
            if (state.keepSpans.length === 0) {
                keepSpansSummaryEl.textContent = '採用区間なし（0.0秒）';
            } else {
                const preview = state.keepSpans.slice(0, 4).map(function (s) {
                    return ctx.formatSec(s[0]) + '〜' + ctx.formatSec(s[1]);
                }).join(', ');
                const suffix = state.keepSpans.length > 4 ? ' 他' + (state.keepSpans.length - 4) + '区間' : '';
                keepSpansSummaryEl.textContent =
                    state.keepSpans.length + '区間: ' + preview + suffix;
            }
        }

        if (!layersEl) return;
        layersEl.replaceChildren();
        const isFullMode = typeof ctx.getPlaybackMode === 'function' && ctx.getPlaybackMode() === 'full';
        if (timelineBar) timelineBar.classList.toggle('is-full-mode', isFullMode);

        const span = Math.max(0.001, viewMax - viewMin);

        if (state.trimStart > viewMin) {
            const leftEnd = Math.min(viewMax, state.trimStart);
            const leftPct = secToPctInWindow(leftEnd, viewMin, span);
            appendMarginBand(
                layersEl,
                0,
                leftPct,
                '開始前の余白: ' + ctx.formatSec(ctx.safeMin) + ' 〜 ' + ctx.formatSec(state.trimStart),
                'is-left'
            );
        }
        if (state.trimEnd < viewMax) {
            const rightStart = Math.max(viewMin, state.trimEnd);
            const rightPct = secToPctInWindow(rightStart, viewMin, span);
            appendMarginBand(
                layersEl,
                rightPct,
                100,
                '終了後の余白: ' + ctx.formatSec(state.trimEnd) + ' 〜 ' + ctx.formatSec(ctx.safeMax),
                'is-right'
            );
        }

        state.keepSpans.forEach(function (keep) {
            const segStart = Math.max(viewMin, keep[0]);
            const segEnd = Math.min(viewMax, keep[1]);
            if (segEnd <= segStart) return;
            const left = secToPctInWindow(segStart, viewMin, span);
            const right = secToPctInWindow(segEnd, viewMin, span);
            const band = createEl('div', 'recording-tl-keep');
            band.style.left = left.toFixed(3) + '%';
            band.style.width = Math.max(0.35, right - left).toFixed(3) + '%';
            band.title = '採用区間: ' + ctx.formatSec(keep[0]) + ' 〜 ' + ctx.formatSec(keep[1]);
            layersEl.appendChild(band);
        });

        cuts.forEach(function (cut) {
            const segStart = Math.max(viewMin, cut.start_sec);
            const segEnd = Math.min(viewMax, cut.end_sec);
            if (segEnd <= segStart) return;
            const left = secToPctInWindow(segStart, viewMin, span);
            const right = secToPctInWindow(segEnd, viewMin, span);
            const seg = createEl('div', 'recording-tl-cut is-' + cut.action);
            seg.style.left = left.toFixed(3) + '%';
            seg.style.width = Math.max(0.8, right - left).toFixed(3) + '%';
            seg.title = cut.label + '（' + ctx.formatSec(cut.start_sec) + ' 〜 ' + ctx.formatSec(cut.end_sec) + '）';
            seg.addEventListener('pointerdown', function (ev) {
                ev.stopPropagation();
            });
            seg.addEventListener('click', function (ev) {
                ev.stopPropagation();
                ctx.seekAndPlay(cut.start_sec, null, { allowBeforeStart: true, allowCutThrough: true });
            });
            layersEl.appendChild(seg);
        });

        renderCandidateMarkers(layersEl, params.candidates, viewMin, viewMax, span, {
            canSubmit: params.canSubmit,
            formatSec: ctx.formatSec,
            onCandidateClick: params.onCandidateClick,
        });
    }

    window.VrcRecordingCutsTimeline = {
        KIND_LABELS: KIND_LABELS,
        ACTION_META: ACTION_META,
        formatSec: formatSec,
        formatAxisSec: formatAxisSec,
        parseTimeInput: parseTimeInput,
        computeZoomWindow: computeZoomWindow,
        createEl: createEl,
        sortCuts: sortCuts,
        evaluateCutsState: evaluateCutsState,
        renderTimelineLayers: renderTimelineLayers,
    };
})();
