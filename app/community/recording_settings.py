"""集会の申請・設定保存で共有する撮影設定の解釈。"""

from .constants import RecordingPolicy


# 撮影許可の 2 択（ラジオ）の値。'on' は切り替えスイッチだった頃の画面から送られる値。
RECORDING_ALLOWED_TRUE_VALUES = ('true', 'on')


def _parse_recording_allowed(value: str | None) -> bool:
    """撮影許可の送信値を bool にする（未送信はスイッチがオフだった頃と同じく False）。"""
    return value in RECORDING_ALLOWED_TRUE_VALUES


def _parse_default_recording_policy(value: str | None, current: str) -> str:
    """撮影ステータスの初期値の送信値を検証する。選択肢外・未送信は今の値を保つ。"""
    if value in RecordingPolicy.values:
        return value
    return current


def _resolve_default_recording_policy(
    value: str | None, current: str, *, was_allowed: bool, allowed: bool, chosen: bool,
) -> str:
    """保存する撮影ステータスの初期値を決める。

    撮影を「許可しない」から「許可する」に切り替えた時、値が未送信・選択肢外、または画面で選ばれていない
    「禁止」（JS の無い送信で、許可しない間に入っていた値のまま）なら、今の値に関わらず「公開」にする。
    画面で選ばれていた値（chosen。切り替え時は JS が「公開」を選ぶ）と、すでに許可している集会で保存する値はそのまま使う。
    """
    if allowed and not was_allowed:
        if value not in RecordingPolicy.values:
            return RecordingPolicy.PUBLIC
        if value == RecordingPolicy.FORBIDDEN and not chosen:
            return RecordingPolicy.PUBLIC
    return _parse_default_recording_policy(value, current)


