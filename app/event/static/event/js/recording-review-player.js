/* スタッフ向け録画プロキシ動画プレーヤー制御・プレビュー境界強制。 */
(function () {
    'use strict';

    function createPlayerController(opts) {
        const video = opts.video;
        const safeMin = opts.safeMin;
        const safeMax = opts.safeMax;
        const formatSec = opts.formatSec;
        const loadingEl = opts.loadingEl;
        const errorEl = opts.errorEl;
        const currentTimeEl = opts.currentTimeEl;

        let segmentStopAt = null;
        let pendingSeek = null;
        let allowBeforeStartPreview = false;
        let allowCutThroughPreview = false;
        let programmaticSeekTarget = null;
        let metadataApplied = false;

        function getCurrentPlayerSec() {
            if (video && video.readyState >= 1 && Number.isFinite(video.currentTime)) {
                return video.currentTime;
            }
            return opts.getStartSec();
        }

        function tryPlayVideo() {
            if (!video) return;
            const playPromise = video.play();
            if (playPromise && typeof playPromise.catch === 'function') {
                playPromise.catch(function (err) {
                    if (err && err.name === 'AbortError') return;
                    opts.showFeedback('自動再生が制限されました。動画の再生ボタンを押してください。');
                });
            }
        }

        function enforcePreviewStartBounds() {
            if (!video || video.readyState < 1) return false;
            if (opts.getPlaybackMode() !== 'preview' || allowBeforeStartPreview) return false;
            const start = opts.getStartSec();
            if (video.currentTime < start - 0.05) {
                programmaticSeekTarget = start;
                video.currentTime = start;
                opts.showFeedback('プレビュー中のため開始位置（' + formatSec(start) + '）へ移動しました');
                return true;
            }
            return false;
        }

        function seekAndPlay(targetSec, stopAtSec, options) {
            if (!video) return;
            const bounded = Math.max(safeMin, Math.min(safeMax, targetSec));
            allowBeforeStartPreview = Boolean(options && options.allowBeforeStart);
            allowCutThroughPreview = Boolean(options && options.allowCutThrough);
            programmaticSeekTarget = bounded;
            segmentStopAt = stopAtSec !== null && stopAtSec !== undefined
                ? Math.max(safeMin, Math.min(safeMax, stopAtSec))
                : null;
            if (video.readyState >= 1) {
                video.currentTime = bounded;
                tryPlayVideo();
            } else {
                pendingSeek = bounded;
                tryPlayVideo();
            }
        }

        function seekFromTimeline(targetSec) {
            if (!video) return;
            const mode = opts.getPlaybackMode();
            let dest = targetSec;
            if (mode === 'full') {
                allowBeforeStartPreview = true;
                allowCutThroughPreview = true;
                segmentStopAt = null;
                opts.showFeedback('再生位置を ' + formatSec(dest) + ' へ移動しました');
            } else {
                const s = opts.getStartSec();
                const e = opts.getEndSec();
                const clamped = Math.max(s, Math.min(Math.max(s, e - 0.1), dest));
                if (dest < s || dest >= e) {
                    opts.showFeedback('プレビュー中のため開始〜終了範囲内へ移動しました（余白を見るには「余白込み全体」）');
                } else {
                    opts.showFeedback('再生位置を ' + formatSec(clamped) + ' へ移動しました');
                }
                dest = clamped;
                allowBeforeStartPreview = false;
                allowCutThroughPreview = false;
                segmentStopAt = e;
            }
            if (video.readyState >= 1) {
                programmaticSeekTarget = dest;
                video.currentTime = dest;
            } else {
                pendingSeek = dest;
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
            if (!metadataApplied && opts.getPlaybackMode() === 'preview') {
                const start = opts.getStartSec();
                if (start > 0 && video.currentTime < start) {
                    programmaticSeekTarget = start;
                    video.currentTime = start;
                    opts.showFeedback('プレビュー中のため開始位置（' + formatSec(start) + '）へ移動しました');
                }
            }
            metadataApplied = true;
            opts.onTimeChanged(video.currentTime);
        }

        function onStartTrimChanged(next, seekPausedStart) {
            allowBeforeStartPreview = false;
            if (!enforcePreviewStartBounds() && seekPausedStart && video && video.readyState >= 1 && video.paused && opts.getPlaybackMode() === 'preview') {
                programmaticSeekTarget = next;
                video.currentTime = next;
            }
        }

        function onEndTrimChanged(next) {
            if (segmentStopAt !== null) segmentStopAt = next;
        }

        function onPlaybackModeChanged() {
            if (!video) return;
            allowBeforeStartPreview = false;
            allowCutThroughPreview = false;
            if (opts.getPlaybackMode() === 'preview') {
                const start = opts.getStartSec();
                const end = opts.getEndSec();
                segmentStopAt = end;
                if (video.currentTime < start || video.currentTime >= end) {
                    if (video.readyState >= 1) {
                        programmaticSeekTarget = start;
                        video.currentTime = start;
                    }
                    opts.showFeedback('開始位置（' + formatSec(start) + '）へ移動しました');
                } else {
                    const cutSpan = opts.findCutContaining(video.currentTime);
                    if (cutSpan && video.readyState >= 1) {
                        programmaticSeekTarget = cutSpan[1];
                        video.currentTime = cutSpan[1];
                        opts.showFeedback('採用カット区間をスキップしました（' + formatSec(cutSpan[1]) + '）');
                    } else {
                        opts.showFeedback('トリミング後プレビューに切り替えました');
                    }
                }
            } else {
                segmentStopAt = null;
                opts.showFeedback('余白込み全体モードに切り替えました（先頭や余白も自由に再生できます）');
            }
        }

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
                opts.onTimeChanged(video.currentTime);
                enforcePreviewStartBounds();
            });
            video.addEventListener('play', function () {
                if (allowBeforeStartPreview && video.currentTime >= opts.getStartSec() - 0.05) {
                    allowBeforeStartPreview = false;
                }
                enforcePreviewStartBounds();
            });
            video.addEventListener('timeupdate', function () {
                if (currentTimeEl) currentTimeEl.textContent = formatSec(video.currentTime);
                opts.onTimeChanged(video.currentTime);
                if (allowBeforeStartPreview && video.currentTime >= opts.getStartSec() - 0.05) {
                    allowBeforeStartPreview = false;
                }
                if (enforcePreviewStartBounds()) return;
                if (segmentStopAt !== null && video.currentTime >= segmentStopAt) {
                    video.pause();
                    video.currentTime = segmentStopAt;
                    opts.showFeedback('終了位置（' + formatSec(segmentStopAt) + '）で停止しました');
                    segmentStopAt = null;
                    allowBeforeStartPreview = false;
                    allowCutThroughPreview = false;
                } else if (opts.getPlaybackMode() === 'preview' && !video.paused) {
                    if (!allowCutThroughPreview) {
                        const skipSpan = opts.findCutContaining(video.currentTime);
                        if (skipSpan) {
                            programmaticSeekTarget = skipSpan[1];
                            video.currentTime = skipSpan[1];
                            opts.showFeedback('採用カットをスキップしました（' + formatSec(skipSpan[0]) + ' → ' + formatSec(skipSpan[1]) + '）');
                            return;
                        }
                    }
                    const end = opts.getEndSec();
                    if (video.currentTime >= end) {
                        video.pause();
                        video.currentTime = end;
                        opts.showFeedback('終了位置（' + formatSec(end) + '）で停止しました');
                    }
                }
            });
        }

        return {
            getCurrentPlayerSec: getCurrentPlayerSec,
            seekAndPlay: seekAndPlay,
            seekFromTimeline: seekFromTimeline,
            onMetadataReady: onMetadataReady,
            onStartTrimChanged: onStartTrimChanged,
            onEndTrimChanged: onEndTrimChanged,
            onPlaybackModeChanged: onPlaybackModeChanged,
        };
    }

    window.VrcRecordingPlayerController = {
        createPlayerController: createPlayerController,
    };
})();
