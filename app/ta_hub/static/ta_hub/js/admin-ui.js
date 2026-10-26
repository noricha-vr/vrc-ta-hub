/* 管理画面専用。フォームの入力値はメモリ内で比較し、外部へ送信・保存しない。 */
(function () {
    'use strict';

    function init(root) {
        const openers = new Map();
        root.addEventListener('click', function (event) {
            const trigger = event.target.closest('[data-confirm-panel], [data-cancel-panel]');
            if (!trigger || !root.contains(trigger)) return;
            const opening = trigger.hasAttribute('data-confirm-panel');
            const id = opening ? trigger.dataset.confirmPanel : trigger.dataset.cancelPanel;
            const panel = document.getElementById(id);
            if (!panel || !root.contains(panel)) return;
            panel.hidden = !opening;
            if (opening) {
                openers.set(panel, trigger);
                trigger.setAttribute('aria-expanded', 'true');
                panel.focus();
            } else {
                restoreFocus(panel);
            }
        });

        function restoreFocus(panel) {
            const trigger = openers.get(panel);
            if (!trigger) return; // 直接開いた確認画面には起点がない。
            trigger.setAttribute('aria-expanded', 'false');
            const dropdown = trigger.closest('.dropdown');
            const menu = trigger.closest('.dropdown-menu');
            const dropdownToggle = dropdown && dropdown.querySelector('[data-bs-toggle="dropdown"]');
            // Bootstrap が閉じたメニュー内のボタンにはフォーカスを戻さない。
            (menu && !menu.classList.contains('show') && dropdownToggle ? dropdownToggle : trigger).focus();
            openers.delete(panel);
        }

        root.addEventListener('keydown', function (event) {
            if (event.key !== 'Escape') return;
            const panel = event.target.closest('.confirm-panel');
            if (!panel || panel.hidden || !openers.has(panel)) return;
            event.preventDefault();
            panel.hidden = true;
            restoreFocus(panel);
        });

        root.querySelectorAll('[data-admin-secret-edit]').forEach(function (details) {
            const input = details.querySelector('input');
            function sync() {
                const disabled = !details.open || input.hasAttribute('data-admin-readonly');
                if (input.disabled !== disabled) {
                    input.disabled = disabled;
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                }
            }
            sync();
            details.addEventListener('toggle', sync);
        });

        function snapshot(form) {
            return JSON.stringify(Array.from(form.elements).filter(function (field) {
                return field.name && field.name !== 'csrfmiddlewaretoken' &&
                    !['submit', 'button', 'reset'].includes(field.type) && field.tagName !== 'FIELDSET';
            }).map(function (field) {
                if (field.type === 'file') {
                    return field.disabled ? [] : Array.from(field.files).map(file => [file.name, file.size, file.lastModified, file.type]);
                }
                if (field.type === 'checkbox' || field.type === 'radio') return [field.value, field.disabled ? field.defaultChecked : field.checked];
                if (field.tagName === 'SELECT') return Array.from(field.options)
                    .filter(option => field.disabled ? option.defaultSelected : option.selected).map(option => option.value);
                return field.disabled ? field.defaultValue : field.value;
            }));
        }

        root.querySelectorAll('form[data-admin-dirty]').forEach(function (form) {
            let initial = snapshot(form);
            function update() {
                const dirty = snapshot(form) !== initial;
                form.dataset.adminDirtyState = dirty ? 'dirty' : 'clean';
                root.querySelectorAll('[data-admin-form-status]').forEach(function (status) {
                    if (status.dataset.adminFormStatus !== form.id) return;
                    const scope = status.dataset.adminScope;
                    status.textContent = (scope ? scope + ': ' : '') +
                        (dirty ? '未保存の変更があります' : '変更はありません');
                    status.classList.toggle('is-dirty', dirty);
                });
            }
            // form 属性でフォーム外に置いた入力も対象にする。
            ['input', 'change'].forEach(function (type) {
                root.addEventListener(type, function (event) {
                    if (event.target.form === form) update();
                });
            });
            form.addEventListener('reset', function () { window.setTimeout(update, 0); });
            // Ajax 保存を既に持つ画面は、保存成功時だけこのイベントを明示的に発火できる。
            form.addEventListener('admin:saved', function () { initial = snapshot(form); update(); });
            update();
        });

        const bar = root.querySelector('[data-admin-action-bar]');
        if (bar) {
            root.classList.add('has-action-bar');
            function resize() {
                root.style.setProperty('--admin-action-bar-height', bar.getBoundingClientRect().height + 'px');
            }
            resize();
            if (typeof ResizeObserver !== 'undefined') new ResizeObserver(resize).observe(bar);
            else window.addEventListener('resize', resize);
        }
    }

    function ready() { document.querySelectorAll('.hub-admin').forEach(init); }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready);
    else ready();
})();
