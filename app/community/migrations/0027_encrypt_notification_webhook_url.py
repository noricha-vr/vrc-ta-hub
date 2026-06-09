"""notification_webhook_url を Fernet 暗号化 TextField に変更し、既存平文を暗号化する.

スキーマ変更 (URLField -> EncryptedTextField/TextField) と data migration
(平文 -> Fernet 暗号化) をワンセットで実施する。

実装上の注意:
    - data migration では `apps.get_model` 経由の save() を使うと
      EncryptedTextField.get_prep_value() で **暗号文が二重暗号化** されてしまう。
      これを避けるため、cursor で生 SQL を直接実行する。
    - reverse 時も同様で、平文 (Discord URL = 短い) を save() で書き戻すと
      二重暗号化されるため、生 SQL で書き戻す。

冪等性:
    - 既に Fernet 暗号化済み (= 復号成功) のレコードはスキップ
    - 復号失敗 ＆ Discord URL パターンに合致するレコードのみ暗号化対象
    - 鍵不一致や別形式の暗号文はスキップ + 警告ログ

ロールバック (reverse):
    - 復号した平文を書き戻す
    - 既に平文 (＝復号失敗) のレコードはスキップ
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
    """
    fernet = community.encrypted_fields._get_fernet()
    connection = schema_editor.connection

    encrypted_count = 0
    skipped_already_encrypted = 0
    skipped_unknown = 0

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, notification_webhook_url FROM community "
            "WHERE notification_webhook_url IS NOT NULL "
            "AND notification_webhook_url != ''"
        )
        rows = cursor.fetchall()

        for row_id, raw in rows:
            if raw is None or raw == "":
                continue
            decrypted = _try_decrypt(fernet, raw)
            if decrypted is not None:
                # 既に暗号化済み (鍵で復号成功) → スキップ
                skipped_already_encrypted += 1
                continue
            if not raw.startswith(DISCORD_WEBHOOK_PREFIX):
                logger.warning(
                    "Community id=%s notification_webhook_url が想定外の形式のためスキップ",
                    row_id,
                )
                skipped_unknown += 1
                continue
            # 平文確定 → 暗号化して書き込み (生 SQL)
            ciphertext = fernet.encrypt(raw.encode()).decode()
            cursor.execute(
                "UPDATE community SET notification_webhook_url = %s WHERE id = %s",
                [ciphertext, row_id],
            )
            encrypted_count += 1

    logger.info(
        "encrypt_existing_webhooks: encrypted=%d, skipped_already_encrypted=%d, skipped_unknown=%d",
        encrypted_count,
        skipped_already_encrypted,
        skipped_unknown,
    )


def decrypt_existing_webhooks(apps, schema_editor):
    """既存の暗号化 webhook URL を平文に戻す (rollback / 冪等).

    生 SQL で書き戻し、ORM の get_prep_value による二重暗号化を回避する。
    """
    fernet = community.encrypted_fields._get_fernet()
    connection = schema_editor.connection

    decrypted_count = 0
    skipped_plain = 0

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, notification_webhook_url FROM community "
            "WHERE notification_webhook_url IS NOT NULL "
            "AND notification_webhook_url != ''"
        )
        rows = cursor.fetchall()

        for row_id, raw in rows:
            if raw is None or raw == "":
                continue
            decrypted = _try_decrypt(fernet, raw)
            if decrypted is None:
                # 既に平文 → スキップ
                skipped_plain += 1
                continue
            cursor.execute(
                "UPDATE community SET notification_webhook_url = %s WHERE id = %s",
                [decrypted, row_id],
            )
            decrypted_count += 1

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
