/* スタッフ向け録画確認 UI の途中カット一覧・採否・手動編集パネル。 */
(function () {
    'use strict';

    function createEditor(ctx) {
        const tl = window.VrcRecordingCutsTimeline;
        const createEl = tl.createEl;
        const KIND_LABELS = tl.KIND_LABELS;
        const ACTION_META = tl.ACTION_META;

        const initialEl = document.getElementById('recording-cuts-initial');
        const cutsJsonInput = document.getElementById('id_cuts_json');
        const listEl = document.getElementById('recording-cuts-list');
        const emptyEl = document.getElementById('cuts-empty-state');
        const countsEl = document.getElementById('recording-cuts-counts');
        const timelineBar = document.getElementById('recording-timeline-bar');
        const timelineLayersEl = document.getElementById('recording-tl-layers');
        const keepSpansSummaryEl = document.getElementById('recording-keep-spans-summary');
        const validationBanner = document.getElementById('cuts-validation-banner');
        const acceptBlockedBanner = document.getElementById('cuts-accept-blocked-banner');
        const pendingWarningEl = document.getElementById('pending-cuts-warning');
        const pendingConfirmLabel = document.getElementById('pending-cuts-confirm-label');
        const confirmPendingCheckbox = document.getElementById('id_confirm_pending_cuts');
        const addStartInput = document.getElementById('cut-add-start');
        const addEndInput = document.getElementById('cut-add-end');
        const addKindSelect = document.getElementById('cut-add-kind');
        const addLabelInput = document.getElementById('cut-add-label');
        const addBtn = document.getElementById('add-manual-cut-btn');
        const addErrorEl = document.getElementById('cut-add-error');
        const submitBtn = document.getElementById('recording-submit-btn');

        let cuts = [];
        try {
            const parsed = JSON.parse((initialEl && initialEl.textContent) || '[]');
            if (Array.isArray(parsed)) {
                cuts = parsed.map(function (c) { return Object.assign({}, c); });
            }
        } catch (_) {
            cuts = [];
        }

        let isDisabled = !ctx.config.canSubmit;
        const allowAcceptCuts = Boolean(ctx.config.allowAcceptCuts);
        const editingCutIds = new Set();
        const originalSubmitText = submitBtn ? submitBtn.textContent : '';
        let lastPlayerSec = ctx.getStartSec();

        function syncHiddenInput(emitDirty) {
            if (!cutsJsonInput) return;
            cutsJsonInput.value = JSON.stringify(cuts);
            if (emitDirty) {
                cutsJsonInput.dispatchEvent(new Event('change', { bubbles: true }));
                if (typeof ctx.onCutsUpdated === 'function') {
                    ctx.onCutsUpdated();
                }
            }
        }

        function evaluateState() {
            return tl.evaluateCutsState(cuts, {
                trimStart: ctx.getStartSec(),
                trimEnd: ctx.getEndSec(),
                allowAcceptCuts: allowAcceptCuts,
                formatSec: ctx.formatSec,
            });
        }

        function updatePlayhead(sec) {
            lastPlayerSec = sec;
            if (typeof ctx.onTimelineSync === 'function') {
                ctx.onTimelineSync(sec);
            }
        }

        function buildInlineEditForm(cut) {
            const wrap = createEl('div', 'recording-cut-edit-grid');
            wrap.dataset.testid = 'cut-edit-panel';

            const sWrap = createEl('div');
            sWrap.appendChild(createEl('label', 'form-label small mb-1', '開始（秒）'));
            const sInput = createEl('input', 'form-control form-control-sm');
            sInput.type = 'number';
            sInput.step = 'any';
            sInput.value = String(cut.start_sec);
            sInput.dataset.testid = 'cut-edit-start';
            sWrap.appendChild(sInput);

            const eWrap = createEl('div');
            eWrap.appendChild(createEl('label', 'form-label small mb-1', '終了（秒）'));
            const eInput = createEl('input', 'form-control form-control-sm');
            eInput.type = 'number';
            eInput.step = 'any';
            eInput.value = String(cut.end_sec);
            eInput.dataset.testid = 'cut-edit-end';
            eWrap.appendChild(eInput);

            const kWrap = createEl('div');
            kWrap.appendChild(createEl('label', 'form-label small mb-1', '種別'));
            const kSelect = createEl('select', 'form-select form-select-sm');
            Object.keys(KIND_LABELS).forEach(function (k) {
                const o = createEl('option', '', KIND_LABELS[k]);
                o.value = k;
                if (cut.kind === k) o.selected = true;
                kSelect.appendChild(o);
            });
            if (!KIND_LABELS[cut.kind]) {
                const customOpt = createEl('option', '', cut.kind);
                customOpt.value = cut.kind;
                customOpt.selected = true;
                kSelect.appendChild(customOpt);
            }
            kWrap.appendChild(kSelect);

            const lWrap = createEl('div');
            lWrap.appendChild(createEl('label', 'form-label small mb-1', 'ラベル'));
            const lInput = createEl('input', 'form-control form-control-sm');
            lInput.type = 'text';
            lInput.maxLength = 120;
            lInput.value = cut.label || '';
            lInput.dataset.testid = 'cut-edit-label';
            lWrap.appendChild(lInput);

            const btnWrap = createEl('div', 'd-flex align-items-end gap-2');
            const saveBtn = createEl('button', 'btn btn-sm btn-primary', '適用');
            saveBtn.type = 'button';
            saveBtn.dataset.testid = 'cut-edit-apply';
            const cancelBtn = createEl('button', 'btn btn-sm btn-outline-secondary', '戻る');
            cancelBtn.type = 'button';
            const errEl = createEl('p', 'small text-danger mb-0 mt-1');
            errEl.hidden = true;

            saveBtn.addEventListener('click', function () {
                const rawS = sInput.value.trim();
                const rawE = eInput.value.trim();
                const ns = rawS === '' ? NaN : ctx.roundTrim(Number(rawS));
                const ne = rawE === '' ? NaN : ctx.roundTrim(Number(rawE));
                const trimS = ctx.getStartSec();
                const trimE = ctx.getEndSec();
                if (!Number.isFinite(ns) || !Number.isFinite(ne) || !(trimS <= ns && ns < ne && ne <= trimE)) {
                    errEl.textContent = '開始・終了は開始〜終了の範囲（' + trimS + '〜' + trimE + '秒）内で「開始 < 終了」にしてください。';
                    errEl.hidden = false;
                    return;
                }
                cut.start_sec = ns;
                cut.end_sec = ne;
                cut.kind = kSelect.value || 'manual';
                cut.label = lInput.value.trim().slice(0, 120);
                editingCutIds.delete(cut.id);
                syncHiddenInput(true);
                renderAll();
            });
            cancelBtn.addEventListener('click', function () {
                editingCutIds.delete(cut.id);
                renderAll();
            });

            btnWrap.appendChild(saveBtn);
            btnWrap.appendChild(cancelBtn);
            wrap.appendChild(sWrap);
            wrap.appendChild(eWrap);
            wrap.appendChild(kWrap);
            wrap.appendChild(lWrap);
            wrap.appendChild(btnWrap);
            wrap.appendChild(errEl);
            return wrap;
        }

        function buildCutCard(cut, errs) {
            const card = createEl('article', 'recording-cut-card is-' + cut.action + (errs.length ? ' is-invalid' : ''));
            card.dataset.cutId = cut.id;
            card.dataset.testid = 'cut-row';

            const topRow = createEl('div', 'd-flex flex-wrap justify-content-between align-items-start gap-2');
            const infoCol = createEl('div', 'min-w-0');

            const metaRow = createEl('div', 'd-flex flex-wrap align-items-center gap-1 mb-1');
            const meta = ACTION_META[cut.action] || ACTION_META.pending;
            const actionBadge = createEl('span', 'detail-chip ' + meta.badgeClass, meta.label);
            actionBadge.dataset.testid = 'cut-action-badge';
            metaRow.appendChild(actionBadge);

            metaRow.appendChild(createEl('span', 'detail-chip', KIND_LABELS[cut.kind] || cut.kind));

            const sourceText = cut.source === 'staff' ? '手動追加' : '自動検出';
            metaRow.appendChild(createEl('span', 'small admin-muted', sourceText));
            if (typeof cut.confidence === 'number') {
                metaRow.appendChild(createEl('span', 'small admin-muted', '信頼度 ' + Math.round(cut.confidence * 100) + '%'));
            }
            infoCol.appendChild(metaRow);

            const dur = Math.max(0, cut.end_sec - cut.start_sec);
            const titleLine = createEl('div', 'fw-bold small');
            titleLine.textContent =
                ctx.formatSec(cut.start_sec) + ' 〜 ' + ctx.formatSec(cut.end_sec) +
                '（' + dur.toFixed(1) + '秒）' + (cut.label ? ' · ' + cut.label : '');
            infoCol.appendChild(titleLine);

            const actionsCol = createEl('div', 'recording-cut-actions');
            [
                { value: 'pending', label: '保留', activeCls: 'btn-warning' },
                { value: 'accept', label: '採用', activeCls: 'btn-danger' },
                { value: 'reject', label: '却下', activeCls: 'btn-secondary' },
            ].forEach(function (opt) {
                const isActive = cut.action === opt.value;
                const btn = createEl('button', 'btn btn-sm ' + (isActive ? opt.activeCls : 'btn-outline-secondary'), opt.label);
                btn.type = 'button';
                btn.disabled = isDisabled;
                btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
                btn.dataset.cutAction = opt.value;
                btn.dataset.testid = 'cut-action-' + opt.value;
                btn.addEventListener('click', function () {
                    cut.action = opt.value;
                    syncHiddenInput(true);
                    renderAll();
                });
                actionsCol.appendChild(btn);
            });

            if (ctx.config.proxyState === 'ready') {
                const prevBtn = createEl('button', 'btn btn-sm btn-outline-primary', '前後を再生');
                prevBtn.type = 'button';
                prevBtn.dataset.testid = 'cut-preview-btn';
                prevBtn.addEventListener('click', function () {
                    const seekTo = Math.max(ctx.safeMin, cut.start_sec - 2.5);
                    const stopAt = Math.min(ctx.safeMax, cut.end_sec + 2.5);
                    ctx.seekAndPlay(seekTo, stopAt, { allowBeforeStart: true, allowCutThrough: true });
                });
                actionsCol.appendChild(prevBtn);
            }

            const editBtn = createEl('button', 'btn btn-sm btn-outline-secondary', editingCutIds.has(cut.id) ? '編集を閉じる' : '編集');
            editBtn.type = 'button';
            editBtn.disabled = isDisabled;
            editBtn.dataset.testid = 'cut-edit-btn';
            editBtn.addEventListener('click', function () {
                if (editingCutIds.has(cut.id)) editingCutIds.delete(cut.id);
                else editingCutIds.add(cut.id);
                renderAll();
            });
            actionsCol.appendChild(editBtn);

            const delBtn = createEl('button', 'btn btn-sm btn-outline-danger', '削除');
            delBtn.type = 'button';
            delBtn.disabled = isDisabled;
            delBtn.dataset.testid = 'cut-delete-btn';
            delBtn.setAttribute('aria-label', 'カット ' + (cut.label || cut.id) + ' を削除');
            delBtn.addEventListener('click', function () {
                cuts = cuts.filter(function (c) { return c.id !== cut.id; });
                editingCutIds.delete(cut.id);
                syncHiddenInput(true);
                renderAll();
            });
            actionsCol.appendChild(delBtn);

            topRow.appendChild(infoCol);
            topRow.appendChild(actionsCol);
            card.appendChild(topRow);

            errs.forEach(function (errMsg) {
                card.appendChild(createEl('p', 'small text-danger mb-0 mt-2', errMsg));
            });

            if (editingCutIds.has(cut.id) && !isDisabled) {
                card.appendChild(buildInlineEditForm(cut));
            }
            return card;
        }

        function renderTimelineOnly(state) {
            const win = typeof ctx.getTimelineWindow === 'function'
                ? ctx.getTimelineWindow()
                : { viewMin: ctx.safeMin, viewMax: ctx.safeMax };
            tl.renderTimelineLayers({
                timelineBar: timelineBar,
                layersEl: timelineLayersEl,
                keepSpansSummaryEl: keepSpansSummaryEl,
                state: state,
                cuts: cuts,
                ctx: ctx,
                viewMin: win.viewMin,
                viewMax: win.viewMax,
                candidates: ctx.candidates,
                canSubmit: !isDisabled,
                onCandidateClick: ctx.onCandidateClick,
            });
            updatePlayhead(lastPlayerSec);
        }

        function renderAll() {
            const state = evaluateState();
            renderTimelineOnly(state);

            if (countsEl) {
                countsEl.textContent = '採用 ' + state.acceptCount + ' / 未判断 ' + state.pendingCount + ' / 却下 ' + state.rejectCount;
            }
            if (listEl) {
                listEl.replaceChildren();
                cuts.forEach(function (cut) {
                    listEl.appendChild(buildCutCard(cut, state.cutErrors.get(cut.id) || []));
                });
            }
            if (emptyEl) emptyEl.hidden = cuts.length > 0;

            if (validationBanner) {
                if (state.globalErrors.length > 0) {
                    validationBanner.textContent = state.globalErrors.join(' ');
                    validationBanner.hidden = false;
                } else {
                    validationBanner.hidden = true;
                }
            }
            if (acceptBlockedBanner) {
                if (state.acceptBlocked) {
                    acceptBlockedBanner.textContent =
                        '現在、途中カットの「採用」を含む確定は録画ワーカー側の対応準備中のため送信できません（' +
                        state.acceptCount + ' 件を採用中）。「保留」または「却下」に変更してください。';
                    acceptBlockedBanner.hidden = false;
                } else {
                    acceptBlockedBanner.hidden = true;
                }
            }
            if (pendingWarningEl) {
                pendingWarningEl.hidden = !(state.pendingCount > 0 && !isDisabled);
                if (pendingConfirmLabel) {
                    pendingConfirmLabel.textContent = '未判断の候補（' + state.pendingCount + '件）をカットせずに確定することを確認しました';
                }
                if (state.pendingCount === 0 && confirmPendingCheckbox) {
                    confirmPendingCheckbox.checked = false;
                }
            }
            if (submitBtn && ctx.config.canSubmit && !isDisabled) {
                if (state.acceptBlocked) {
                    submitBtn.disabled = true;
                    submitBtn.textContent = '採用カットを含むため確定不可（準備中）';
                } else {
                    submitBtn.disabled = false;
                    submitBtn.textContent = originalSubmitText;
                }
            }
        }

        document.querySelectorAll('[data-action="fill-cut-start-from-player"]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                if (!ctx.video || !addStartInput) return;
                if (ctx.video.readyState < 1) {
                    if (typeof ctx.showFeedback === 'function') ctx.showFeedback('動画の読み込み後に実行してください');
                    return;
                }
                const clamped = Math.max(ctx.getStartSec(), Math.min(ctx.getEndSec(), ctx.video.currentTime));
                addStartInput.value = String(ctx.roundTrim(clamped));
            });
        });
        document.querySelectorAll('[data-action="fill-cut-end-from-player"]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                if (!ctx.video || !addEndInput) return;
                if (ctx.video.readyState < 1) {
                    if (typeof ctx.showFeedback === 'function') ctx.showFeedback('動画の読み込み後に実行してください');
                    return;
                }
                const clamped = Math.max(ctx.getStartSec(), Math.min(ctx.getEndSec(), ctx.video.currentTime));
                addEndInput.value = String(ctx.roundTrim(clamped));
            });
        });

        if (addBtn) {
            addBtn.addEventListener('click', function () {
                if (isDisabled) return;
                if (addErrorEl) addErrorEl.hidden = true;
                const rawS = addStartInput ? addStartInput.value.trim() : '';
                const rawE = addEndInput ? addEndInput.value.trim() : '';
                const sVal = rawS === '' ? NaN : ctx.roundTrim(Number(rawS));
                const eVal = rawE === '' ? NaN : ctx.roundTrim(Number(rawE));
                const trimS = ctx.getStartSec();
                const trimE = ctx.getEndSec();

                if (!Number.isFinite(sVal) || !Number.isFinite(eVal) || !(trimS <= sVal && sVal < eVal && eVal <= trimE)) {
                    if (addErrorEl) {
                        addErrorEl.textContent = '開始・終了は開始〜終了の範囲（' + trimS + '秒 〜 ' + trimE + '秒）内で「開始 < 終了」を指定してください。';
                        addErrorEl.hidden = false;
                    }
                    return;
                }
                const kind = addKindSelect ? addKindSelect.value : 'manual';
                const rawLabel = addLabelInput ? addLabelInput.value.trim() : '';
                const label = rawLabel || (KIND_LABELS[kind] || '手動カット');
                const id = 'manual-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 6);

                cuts.push({
                    id: id,
                    start_sec: sVal,
                    end_sec: eVal,
                    kind: kind,
                    label: label.slice(0, 120),
                    source: 'staff',
                    action: 'accept',
                });
                if (addStartInput) addStartInput.value = '';
                if (addEndInput) addEndInput.value = '';
                if (addLabelInput) addLabelInput.value = '';
                syncHiddenInput(true);
                renderAll();
            });
        }

        renderAll();

        return {
            getCuts: function () {
                tl.sortCuts(cuts);
                return cuts.map(function (c) { return Object.assign({}, c); });
            },
            getAcceptSpans: function () {
                tl.sortCuts(cuts);
                return cuts
                    .filter(function (c) { return c.action === 'accept'; })
                    .map(function (c) { return [c.start_sec, c.end_sec]; });
            },
            getAdoptedDuration: function () {
                return evaluateState().adoptedDurationSec;
            },
            validateForSubmit: function () {
                const state = evaluateState();
                if (state.acceptBlocked) {
                    return {
                        ok: false,
                        message: '現在、途中カットの「採用」を含む確定は録画ワーカー準備中のため送信できません。',
                    };
                }
                if (state.globalErrors.length > 0) {
                    return { ok: false, message: state.globalErrors.join(' ') };
                }
                if (state.pendingCount > 0 && confirmPendingCheckbox && !confirmPendingCheckbox.checked) {
                    return {
                        ok: false,
                        message: '未判断の候補が ' + state.pendingCount + ' 件残っています。確認チェックを入れてから確定してください。',
                        focusEl: confirmPendingCheckbox,
                    };
                }
                return { ok: true, message: '' };
            },
            onTrimChanged: function () {
                renderAll();
            },
            onTrimPreview: function () {
                renderTimelineOnly(evaluateState());
            },
            onPlaybackModeChanged: function () {
                renderTimelineOnly(evaluateState());
            },
            onZoomChanged: function () {
                renderTimelineOnly(evaluateState());
            },
            onPlayerTimeUpdate: function (sec) {
                updatePlayhead(sec);
            },
            setDisabled: function (disabled) {
                isDisabled = Boolean(disabled);
                [addStartInput, addEndInput, addKindSelect, addLabelInput, addBtn, confirmPendingCheckbox].forEach(function (el) {
                    if (el) el.disabled = isDisabled;
                });
                document.querySelectorAll('[data-action="fill-cut-start-from-player"], [data-action="fill-cut-end-from-player"]').forEach(function (el) {
                    el.disabled = isDisabled;
                });
                if (isDisabled) editingCutIds.clear();
                renderAll();
            },
        };
    }

    window.VrcRecordingCuts = { createEditor: createEditor };
})();
