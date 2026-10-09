"""vket アプリの画面で使う定数."""

from .models import VketNotice

# 配信対象の表示名。モデルの choices の表示名を変えると migration が要るため、
# 画面（作成フォームの選択肢・管理一覧）はここを唯一の出どころにする。
TARGET_SCOPE_LABELS = {
    VketNotice.TargetScope.ALL_PARTICIPANTS: '全参加者',
    VketNotice.TargetScope.UNACKED: 'まだ一度も確認していない参加者',
    VketNotice.TargetScope.MANUAL: '手動選択',
}

# お知らせ作成フォームで選べる配信対象（手動選択は未実装のため出さない）
CREATABLE_TARGET_SCOPES = (
    VketNotice.TargetScope.ALL_PARTICIPANTS,
    VketNotice.TargetScope.UNACKED,
)


def target_scope_label(value: str) -> str:
    return TARGET_SCOPE_LABELS.get(value, dict(VketNotice.TargetScope.choices).get(value, value))
