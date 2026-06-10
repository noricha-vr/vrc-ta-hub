"""notification_webhook_url を Fernet 暗号化 TextField に変更し、既存平文を暗号化する.

スキーマ変更 (URLField -> EncryptedTextField/TextField) と data migration
(平文 -> Fernet 暗号化) をワンセットで実施する。

実装上の注意:
    - data migration では `apps.get_model` 経由の save() を使うと
      EncryptedTextField.get_prep_value() で **暗号文が二重暗号化** されてしまう。
      これを避けるため、cursor で生 SQL を直接実行する。
    - reverse 時も同様で、平文 (Discord URL = 短い) を save() で書き戻すと
      二重暗号化されるため、生 SQL で書き戻す。

冪等性 (forward):
    - 既に Fernet 暗号化済み (= 復号成功) のレコードはスキップ
    - 平文 Discord URL は暗号化対象
    - 想定外の値 (Discord URL でも復号もできない) は **Fail Fast** (RuntimeError)。
      DB dump 保護の目的を満たすため、平文を残す skip 動作は許容しない。

ロールバック (reverse):
    - 復号できた暗号文 → 平文に戻す
    - 復号できない非空値 (鍵不一致 / 破損) は **Fail Fast** (RuntimeError)。
      ciphertext を旧 URLField に残すと URLField max_length 超過 / 不正データ放置に
      なるため、対象 id を明示してエラーにする。
"""
import logging

import django.core.validators
from django.db import migrations

import community.encrypted_fields


logger = logging.getLogger(__name__)

# Discord webhook URL の prefix。data migration の対象判定に使う。
DISCORD_WEBHOOK_PREFIX = "https://discord.com/api/webhooks/"


def _try_decrypt(fernet, raw: str) -> str | None:
    """Fernet で復号できれば平文を返す。失敗時は None."""
    from cryptography.fernet import InvalidToken

    try:
        return fernet.decrypt(raw.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def encrypt_existing_webhooks(apps, schema_editor):
    """既存の平文 webhook URL を Fernet 暗号化する (冪等).

    生 SQL で読み書きし、ORM の get_prep_value による二重暗号化を回避する。
    想定外データは Fail Fast (RuntimeError) する。

    対象データが 0 件なら Fernet 初期化もスキップ (CI / 空 DB / 新規セットアップ環境で
    FERNET_KEY 未設定でも migration が通るようにする)。
    """
    connection = schema_editor.connection

    encrypted_count = 0
    skipped_already_encrypted = 0
    unknown_ids: list[int] = []

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, notification_webhook_url FROM community "
            "WHERE notification_webhook_url IS NOT NULL "
            "AND notification_webhook_url != ''"
        )
        rows = cursor.fetchall()

        if not rows:
            logger.info("encrypt_existing_webhooks: 対象データなし、Fernet 初期化をスキップ")
            return

        fernet = community.encrypted_fields._get_fernet()

        for row_id, raw in rows:
            if raw is None or raw == "":
                continue
            decrypted = _try_decrypt(fernet, raw)
            if decrypted is not None:
                # 既に暗号化済み (鍵で復号成功) → スキップ
                skipped_already_encrypted += 1
                continue
            if not raw.startswith(DISCORD_WEBHOOK_PREFIX):
                # 想定外: 暗号文でも Discord URL でもない値が残っている。
                # 平文として残すと DB dump 保護目的を満たさないため Fail Fast。
                unknown_ids.append(row_id)
                continue
            # 平文確定 → 暗号化して書き込み (生 SQL)
            ciphertext = fernet.encrypt(raw.encode()).decode()
            cursor.execute(
                "UPDATE community SET notification_webhook_url = %s WHERE id = %s",
                [ciphertext, row_id],
            )
            encrypted_count += 1

    if unknown_ids:
        raise RuntimeError(
            "encrypt_existing_webhooks: 復号も Discord URL 判定もできない値が残っています。"
            f" 対象 community.id={unknown_ids}. 手動で値を確認してください "
            "(空文字にする / 削除する / 正しい平文に直すなど)"
        )

    logger.info(
        "encrypt_existing_webhooks: encrypted=%d, skipped_already_encrypted=%d",
        encrypted_count,
        skipped_already_encrypted,
    )


def decrypt_existing_webhooks(apps, schema_editor):
    """既存の暗号化 webhook URL を平文に戻す (rollback / 冪等).

    生 SQL で書き戻し、ORM の get_prep_value による二重暗号化を回避する。

    Fail Fast 方針:
        - 既に平文 (Discord URL) → スキップ (冪等)
        - 復号できない非空値 (鍵不一致 / Fernet token 形式だが復号失敗) → エラー。
          ciphertext を URLField (varchar 200) に残すと max_length 超過する上、
          ロールバック完了を主張すると DB 不整合を見逃す。
    """
    from cryptography.fernet import InvalidToken  # noqa: F401  # _try_decrypt 内で使用

    connection = schema_editor.connection

    decrypted_count = 0
    skipped_plain = 0
    undecryptable_ids: list[int] = []

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, notification_webhook_url FROM community "
            "WHERE notification_webhook_url IS NOT NULL "
            "AND notification_webhook_url != ''"
        )
        rows = cursor.fetchall()

        if not rows:
            logger.info("decrypt_existing_webhooks: 対象データなし、Fernet 初期化をスキップ")
            return

        fernet = community.encrypted_fields._get_fernet()

        for row_id, raw in rows:
            if raw is None or raw == "":
                continue
            decrypted = _try_decrypt(fernet, raw)
            if decrypted is not None:
                cursor.execute(
                    "UPDATE community SET notification_webhook_url = %s WHERE id = %s",
                    [decrypted, row_id],
                )
                decrypted_count += 1
                continue
            # 復号失敗。Discord URL (平文) なら冪等スキップ。それ以外は不整合データ。
            if raw.startswith(DISCORD_WEBHOOK_PREFIX):
                skipped_plain += 1
                continue
            undecryptable_ids.append(row_id)

    if undecryptable_ids:
        raise RuntimeError(
            "decrypt_existing_webhooks: 復号できず Discord URL でもない値が残っています。"
            f" 対象 community.id={undecryptable_ids}. 鍵不一致または破損データの可能性。"
            " 手動で対応してから rollback を再実行してください。"
        )

    logger.info(
        "decrypt_existing_webhooks: decrypted=%d, skipped_plain=%d",
        decrypted_count,
        skipped_plain,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("community", "0026_alter_communitymember_unique_together_and_more"),
    ]

    operations = [
        # 1) スキーマ変更: URLField -> EncryptedTextField (TextField サブクラス)
        migrations.AlterField(
            model_name="community",
            name="notification_webhook_url",
            field=community.encrypted_fields.EncryptedTextField(
                blank=True,
                default="",
                validators=[
                    django.core.validators.RegexValidator(
                        message="Discord Webhook URL は https://discord.com/api/webhooks/ で始まる必要があります。",
                        regex="^https://discord\\.com/api/webhooks/",
                    )
                ],
                verbose_name="Discord Webhook URL",
            ),
        ),
        # 2) data migration: 既存平文 -> 暗号化 (冪等、reverse で平文に戻す)
        migrations.RunPython(
            encrypt_existing_webhooks,
            reverse_code=decrypt_existing_webhooks,
        ),
    ]
