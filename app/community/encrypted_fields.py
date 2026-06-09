"""Django 用 Fernet 暗号化 TextField.

DB dump 流出時の webhook URL 漏洩を防ぐため、保存時に Fernet 対称鍵で暗号化、
取得時に復号する CharField/TextField のサブクラスを提供する。

設計判断:
    - 既存の django-cryptography パッケージは更新停止 (最終 2020 年) のため不採用。
      公式 maintained な `cryptography` ライブラリの Fernet を直接利用する。
    - SECRET_KEY とは別の FERNET_KEY を使い、鍵分離 (将来のローテーション容易化)
      と SECRET_KEY 漏洩時の二重防御を実現する。
    - 復号失敗時の取り扱い:
        - 値が Fernet token 形式 (`gAAAAA` で始まる) の場合: 鍵不一致や破損
          ciphertext の可能性。空文字を返す (Fail Safe: 不正な暗号文を平文として
          外部送信しないため)。warning ログを出す。
        - それ以外: 平文混在期間とみなして raw を返す (data migration 直後など)。
"""
from __future__ import annotations

import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)

# Fernet token は urlsafe base64 で先頭がバージョンバイト 0x80 → "gAAAAA" になる。
# この形式の値を復号失敗で返すと「暗号文のまま webhook 送信」など二次被害になるので、
# 形式検出して空文字に倒す。
_FERNET_TOKEN_PREFIX = "gAAAAA"


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
        """DB から読んだ生値を復号して Python 側の値にする.

        復号失敗時:
            - Fernet token 形式 (prefix=gAAAAA) → 鍵不一致 / 破損とみなし空文字
              (Fail Safe: 暗号文を webhook URL として外部送信させない)
            - それ以外 → 平文混在期間とみなして raw を返す
        """
        if value is None or value == "":
            return value
        try:
            return _get_fernet().decrypt(value.encode()).decode()
        except (InvalidToken, ValueError):
            if isinstance(value, str) and value.startswith(_FERNET_TOKEN_PREFIX):
                logger.warning(
                    "EncryptedTextField: Fernet token の復号に失敗。鍵不一致または破損データ。"
                    " 空文字を返します。"
                )
                return ""
            # 平文と推定される値はそのまま返す (data migration 直後の混在期間対応)
            return value

    def to_python(self, value):
        """deconstruct / form clean 時にも復号を試みる."""
        if value is None or value == "":
            return value
        if not isinstance(value, str):
            return value
        try:
            return _get_fernet().decrypt(value.encode()).decode()
        except (InvalidToken, ValueError):
            if value.startswith(_FERNET_TOKEN_PREFIX):
                # form 経由で偶発的に暗号文が入力された場合の保険
                return ""
            return value

    def get_prep_value(self, value):
        """Python の値を DB 書き込み用の暗号文に変換する."""
        if value is None or value == "":
            return value
        if not isinstance(value, str):
            value = str(value)
        return _get_fernet().encrypt(value.encode()).decode()
