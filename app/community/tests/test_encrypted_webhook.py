"""notification_webhook_url の Fernet 暗号化に関するテスト.

検証項目:
    - DB には暗号文 (Fernet token) が保存され、平文の URL は含まれない
    - Python 側からは復号済みの平文として取得できる (round-trip)
    - 3 回 set→get の round-trip で値が崩れない
    - FERNET_KEY 未設定時は RuntimeError を送出する
    - 復号失敗時の Fail Safe (Fernet token 形式 → 空文字、他 → raw)
"""
from cryptography.fernet import Fernet
from django.db import connection
from django.test import TestCase, override_settings

from community.encrypted_fields import EncryptedTextField, _get_fernet
from community.models import Community

WEBHOOK_URL = "https://discord.com/api/webhooks/123456789/abcdefghijklmnop_qrstuvwxyz"

# テスト用固定鍵。本番の FERNET_KEY とは独立させ、テストが環境設定に依存しないようにする。
TEST_FERNET_KEY = Fernet.generate_key().decode()


@override_settings(FERNET_KEY=TEST_FERNET_KEY)
class EncryptedWebhookStorageTest(TestCase):
    """DB 保存時に暗号化されることを確認する."""

    def setUp(self):
        self.community = Community.objects.create(
            name="暗号化テスト集会",
            frequency="毎週",
            organizers="テスト主催者",
            notification_webhook_url=WEBHOOK_URL,
        )

    def test_db_value_is_encrypted(self):
        """生 SQL で取得した値が暗号文 (平文 URL を含まない) であること."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT notification_webhook_url FROM community WHERE id = %s",
                [self.community.id],
            )
            raw = cursor.fetchone()[0]

        # 暗号文は平文 URL と一致しない
        self.assertNotEqual(raw, WEBHOOK_URL)
        # 平文 URL の特徴的部分が漏れていない
        self.assertNotIn("discord.com/api/webhooks", raw)
        # Fernet 復号で平文に戻せる
        fernet = _get_fernet()
        decrypted = fernet.decrypt(raw.encode()).decode()
        self.assertEqual(decrypted, WEBHOOK_URL)

    def test_orm_returns_plaintext(self):
        """ORM 経由では平文として取得できる (復号される)."""
        fetched = Community.objects.get(pk=self.community.pk)
        self.assertEqual(fetched.notification_webhook_url, WEBHOOK_URL)

    def test_empty_value_is_stored_as_is(self):
        """空文字は暗号化されず空のまま保存される (検索性 / 互換性のため)."""
        c = Community.objects.create(
            name="空 webhook 集会",
            frequency="毎週",
            organizers="テスト主催者",
            notification_webhook_url="",
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT notification_webhook_url FROM community WHERE id = %s",
                [c.id],
            )
            raw = cursor.fetchone()[0]
        self.assertEqual(raw, "")
        self.assertEqual(
            Community.objects.get(pk=c.pk).notification_webhook_url, ""
        )


@override_settings(FERNET_KEY=TEST_FERNET_KEY)
class EncryptedWebhookRoundTripTest(TestCase):
    """3 回 set→get round-trip で値が崩れないことを確認する."""

    def test_triple_round_trip_preserves_value(self):
        c = Community.objects.create(
            name="round-trip 集会",
            frequency="毎週",
            organizers="テスト主催者",
            notification_webhook_url=WEBHOOK_URL,
        )

        for _ in range(3):
            fetched = Community.objects.get(pk=c.pk)
            self.assertEqual(fetched.notification_webhook_url, WEBHOOK_URL)
            # 同じ値で再保存 (再暗号化される)
            fetched.notification_webhook_url = fetched.notification_webhook_url
            fetched.save(update_fields=["notification_webhook_url"])

        # 最終確認: 3 回再保存しても平文として取得できる
        self.assertEqual(
            Community.objects.get(pk=c.pk).notification_webhook_url, WEBHOOK_URL
        )

    def test_save_produces_different_ciphertext_each_time(self):
        """Fernet は nonce を内包するため、同じ平文でも暗号文が毎回変わる.

        これにより DB 上で同じ webhook を使う集会同士が暗号文比較で識別されない。
        """
        c = Community.objects.create(
            name="nonce テスト集会",
            frequency="毎週",
            organizers="テスト主催者",
            notification_webhook_url=WEBHOOK_URL,
        )

        ciphertexts = []
        for _ in range(3):
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT notification_webhook_url FROM community WHERE id = %s",
                    [c.id],
                )
                ciphertexts.append(cursor.fetchone()[0])
            c.notification_webhook_url = WEBHOOK_URL
            c.save(update_fields=["notification_webhook_url"])

        # 3 つの暗号文が全て異なる
        self.assertEqual(len(set(ciphertexts)), 3)


class FernetKeyMissingTest(TestCase):
    """FERNET_KEY 未設定時に明示的に失敗することを確認する."""

    @override_settings(FERNET_KEY="")
    def test_empty_key_raises_runtime_error(self):
        """空文字の FERNET_KEY では RuntimeError を送出する."""
        field = EncryptedTextField()
        with self.assertRaises(RuntimeError):
            field.get_prep_value("https://discord.com/api/webhooks/x/y")


@override_settings(FERNET_KEY=TEST_FERNET_KEY)
class DecryptFailureFailSafeTest(TestCase):
    """復号失敗時の挙動 (Fail Safe) を確認する.

    - Fernet token 形式 (gAAAAA prefix) が復号失敗 → 空文字を返す
      (鍵不一致 / 破損 ciphertext が webhook URL として外部送信されるのを防ぐ)
    - それ以外の値 (平文混在期間) → そのまま返す
    """

    def test_wrong_key_ciphertext_returns_empty(self):
        """別鍵で暗号化された Fernet token は復号失敗 → 空文字."""
        other_key = Fernet.generate_key()
        ciphertext = Fernet(other_key).encrypt(WEBHOOK_URL.encode()).decode()

        # DB から from_db_value を経由させるため、生 SQL で書き込み
        c = Community.objects.create(
            name="別鍵テスト集会",
            frequency="毎週",
            organizers="テスト主催者",
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE community SET notification_webhook_url = %s WHERE id = %s",
                [ciphertext, c.id],
            )

        # ORM で読むと復号失敗 → 空文字 (Fail Safe)
        fetched = Community.objects.get(pk=c.pk)
        self.assertEqual(fetched.notification_webhook_url, "")

    def test_plaintext_url_in_db_is_passed_through(self):
        """DB に平文 URL が残っているケース (migration 中) → そのまま返す."""
        c = Community.objects.create(
            name="平文混在テスト集会",
            frequency="毎週",
            organizers="テスト主催者",
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE community SET notification_webhook_url = %s WHERE id = %s",
                [WEBHOOK_URL, c.id],
            )

        fetched = Community.objects.get(pk=c.pk)
        self.assertEqual(fetched.notification_webhook_url, WEBHOOK_URL)
