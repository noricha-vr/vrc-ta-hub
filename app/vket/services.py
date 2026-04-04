"""Vketコラボに関するビジネスロジック"""

from vket.models import VketParticipation


def get_vket_lock_info(event) -> tuple[bool, str]:
    """Vketコラボ期間中のイベントかどうかを判定し、ロックメッセージを返す。

    イベントの所属Communityがアクティブな VketParticipation を持ち、
    かつイベント日がそのコラボの開催期間内（period_start〜period_end）であれば
    ロック中と判定する。1クエリで判定とメッセージ取得を行う。

    Args:
        event: Event インスタンス

    Returns:
        (ロック中か, メッセージ) のタプル。ロックされていない場合は (False, "")
    """
    # ロック判定に不要な列まで読むと、列追加直後の古いDBスキーマで 500 になりうるため、
    # メッセージ生成に必要な情報だけを取得する。
    participation = (
        VketParticipation.objects.filter(
            community=event.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
            collaboration__period_start__lte=event.date,
            collaboration__period_end__gte=event.date,
        )
        .values_list(
            "collaboration__name",
            "collaboration__period_start",
            "collaboration__period_end",
        )
        .first()
    )
    if not participation:
        return False, ""
    collab_name, period_start, period_end = participation
    message = (
        f"「{collab_name}」期間中（{period_start}〜{period_end}）"
        f"のため、日時の変更は運営のみ可能です。"
    )
    return True, message


def is_event_locked_by_vket(event) -> bool:
    """Vketコラボ期間中のイベントかどうかを判定する。

    Args:
        event: Event インスタンス

    Returns:
        True の場合、集会管理者からの日時変更・削除をブロックすべき
    """
    locked, _ = get_vket_lock_info(event)
    return locked


def get_vket_lock_message(event) -> str:
    """ロック中のイベントに対する表示メッセージを返す。

    Args:
        event: Event インスタンス

    Returns:
        ロックメッセージ文字列。ロックされていない場合は空文字列
    """
    _, message = get_vket_lock_info(event)
    return message
