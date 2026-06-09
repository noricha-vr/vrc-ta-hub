"""Django 用 Fernet 暗号化 TextField.

DB dump 流出時の webhook URL 漏洩を防ぐため、保存時に Fernet 対称鍵で暗号化、
取得時に復号する CharField/TextField のサブクラスを提供する。

設計判断:
    - 既存の django-cryptography パッケージは更新停止 (最終 2020 年) のため不採用。
      公式 maintained な `cryptography` ライブラリの Fernet を直接利用する。
    - SECRET_KEY とは別の FERNET_KEY を使い、鍵分離 (将来のローテーション容易化)
      と SECRET_KEY 漏洩時の二重防御を実現する。
    - 復号失敗時は値を「そのまま」返す。これにより data migration の途中で平文と
      暗号文が共存する期間でもアプリが落ちず、再実行による収束が可能。
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models


def _get_fernet() -> Fernet:
    """settings.FERNET_KEY から Fernet インスタンスを構築する.

    Raises:
        RuntimeError: FERNET_KEY が未設定または空文字の場合。

    Returns:
        cryptography.fernet.Fernet: 暗号化/復号インスタンス。
    """
    key = getattr(settings, "FERNET_KEY", "") or ""
    if not key:
        raise RuntimeError(
            "FERNET_KEY is not configured. "
            "Generate one with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
        )
    if isinstance(key, str):
        key = key.encode()
    return Fernet(key)


class EncryptedTextField(models.TextField):
    """Fernet で対称暗号化して DB 保存し、取得時に復号する TextField.

    暗号文は ASCII (urlsafe base64) なので TextField で十分。空文字 / None は
    そのままパススルーする (検索性は失われるため等値検索のみ unique=False 前提)。

    復号に失敗した場合は raw value を返す。data migration 直後など平文と暗号文が
    混在する期間でアプリが 500 を返さないようにするためのフォールバック。
    """

    description = "Fernet-encrypted text field"

    def from_db_value(self, value, expression, connection):
        """DB から読んだ生値を復号して Python 側の値にする."""
        if value is None or value == "":
            return value
        try:
            return _get_fernet().decrypt(value.encode()).decode()
        except (InvalidToken, ValueError):
            # 平文混在期間 / 別鍵で暗号化された値 → そのまま返す (Fail soft)
            return value

    def to_python(self, value):
        """deconstruct / form clean 時にも復号を試みる."""
        if value is None or value == "":
            return value
        # 既に復号済み (Python 文字列) の可能性があるため、暗号化検出を試みる。
        # urlsafe base64 でない / 長さが Fernet の最小長 (88 文字) 未満 → 平文とみなす。
        if not isinstance(value, str):
            return value
        try:
            return _get_fernet().decrypt(value.encode()).decode()
        except (InvalidToken, ValueError):
            return value

    def get_prep_value(self, value):
        """Python の値を DB 書き込み用の暗号文に変換する."""
        if value is None or value == "":
            return value
        if not isinstance(value, str):
            value = str(value)
        return _get_fernet().encrypt(value.encode()).decode()
