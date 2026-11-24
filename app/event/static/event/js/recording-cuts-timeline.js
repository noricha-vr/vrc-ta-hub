/* スタッフ向け録画確認 UI の途中カット（cuts[]）検証・keep_spans 算出・タイムライン描画。 */
(function () {
    'use strict';

    const MIN_KEEP_SEC = 1.0;
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
        const formatSec = options.formatSec;

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
                    'trim 範囲（' + formatSec(trimStart) + ' 〜 ' + formatSec(trimEnd) + '）外です。区間を調整または削除してください。'
                );
            }
            if (cut.action === 'accept') {
                if (prevAccept && cut.start_sec <= prevAccept.end_sec) {
                    errs.push(
                        '他の採用カット（' + formatSec(prevAccept.start_sec) + ' 〜 ' + formatSec(prevAccept.end_sec) + '）と重複または接触しています。'
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
            globalErrors.push('trim 範囲外のカットまたは重複している採用カットがあります。各カードの赤字を確認してください。');
        }
        if (trimEnd > trimStart && adoptedDurationSec < MIN_KEEP_SEC) {
            globalErrors.push(
                '維持時間（keep_spans 合計）が ' + adoptedDurationSec.toFixed(1) + ' 秒です。' + MIN_KEEP_SEC.toFixed(1) + ' 秒以上残るように調整してください。'
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

    function renderTimelineBar(params) {
        const timelineBar = params.timelineBar;
        const keepSpansSummaryEl = params.keepSpansSummaryEl;
        const state = params.state;
        const cuts = params.cuts;
        const ctx = params.ctx;

        if (keepSpansSummaryEl) {
            if (state.keepSpans.length === 0) {
                keepSpansSummaryEl.textContent = '維持区間なし（0.0秒）';
            } else {
                const preview = state.keepSpans.slice(0, 4).map(function (s) {
                    return ctx.formatSec(s[0]) + '〜' + ctx.formatSec(s[1]);
                }).join(', ');
                const suffix = state.keepSpans.length > 4 ? ' 他' + (state.keepSpans.length - 4) + '区間' : '';
                keepSpansSummaryEl.textContent =
                    state.keepSpans.length + '区間（計 ' + state.adoptedDurationSec.toFixed(1) + '秒）: ' + preview + suffix;
            }
        }

        if (!timelineBar) return null;
        timelineBar.replaceChildren();
        const total = Math.max(0.001, ctx.safeMax - ctx.safeMin);

        state.keepSpans.forEach(function (span) {
            const left = ((span[0] - ctx.safeMin) / total) * 100;
            const width = Math.max(0.4, ((span[1] - span[0]) / total) * 100);
            const band = createEl('div', 'recording-tl-keep');
            band.style.left = left.toFixed(3) + '%';
            band.style.width = width.toFixed(3) + '%';
            band.title = '採用区間: ' + ctx.formatSec(span[0]) + ' 〜 ' + ctx.formatSec(span[1]);
            timelineBar.appendChild(band);
        });

        cuts.forEach(function (cut) {
            const left = Math.max(0, Math.min(100, ((cut.start_sec - ctx.safeMin) / total) * 100));
            const right = Math.max(left, Math.min(100, ((cut.end_sec - ctx.safeMin) / total) * 100));
            const seg = createEl('div', 'recording-tl-cut is-' + cut.action);
            seg.style.left = left.toFixed(3) + '%';
            seg.style.width = Math.max(0.8, right - left).toFixed(3) + '%';
            seg.title = cut.label + '（' + ctx.formatSec(cut.start_sec) + ' 〜 ' + ctx.formatSec(cut.end_sec) + '）';
            seg.addEventListener('click', function (ev) {
                ev.stopPropagation();
                ctx.seekAndPlay(cut.start_sec, null, { allowBeforeStart: true, allowCutThrough: true });
            });
            timelineBar.appendChild(seg);
        });

        const playheadEl = createEl('div', 'recording-tl-playhead');
        timelineBar.appendChild(playheadEl);
        return playheadEl;
    }

    window.VrcRecordingCutsTimeline = {
        KIND_LABELS: KIND_LABELS,
        ACTION_META: ACTION_META,
        createEl: createEl,
        sortCuts: sortCuts,
        evaluateCutsState: evaluateCutsState,
        renderTimelineBar: renderTimelineBar,
    };
})();
