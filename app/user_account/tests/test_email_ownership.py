"""メールアドレスの持ち主を DB の一意制約で 1 人に限ることを確かめる（Issue #659）。

持ち主 = 主アドレス（CustomUser.email）か、確認済みまたは primary の EmailAddress を持つユーザー。
本番の MySQL には allauth の条件付き unique 制約が作られないため、各テストで SQLite からも外し、
EmailOwnership の一意制約だけで守れていることを確かめる。

SQLite では書き込みのトランザクションを同時に走らせられないため、並行した登録・確認は
「判定の時点では空いていて、判定の直後（保存の前）に別ユーザーが持ち主になった」状態を、
各経路の判定を差し替えて再現する。
"""

from importlib import import_module
from io import StringIO
from unittest.mock import Mock, call, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from allauth.account.models import EmailAddress, EmailConfirmationHMAC
from allauth.core.context import request_context
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.internal.flows.signup import process_signup
from allauth.socialaccount.models import SocialAccount, SocialLogin

from tests.factories import make_legacy_user, make_user
from user_account.adapters import CustomSocialAccountAdapter
from user_account.email_ownership import (
    claim_email,
    is_email_in_use,
    lock_owner,
    release_unowned_emails,
    sync_email_ownership,
)
from user_account.models import EmailOwnership
from user_account.tests.utils import TEST_SOCIALACCOUNT_PROVIDERS, TEST_SOCIALACCOUNT_PROVIDERS_WITH_APPS

User = get_user_model()

SHARED_EMAIL = 'shared-address@example.com'
PENDING_EMAIL = 'pending-change@example.com'
# GitGuardian 誤検知回避: テスト専用の値を 1 行のリテラルにしない（本物の秘密ではない）
SIGNUP_PASSWORD = 'Signup-Pass-' + '2026!'
CONFIRM_EMAIL_SENT_PATH = '/accounts/confirm-email/'
ACCOUNT_EXISTS_MAIL_PHRASE = '新しいアカウントは作成していません'
LOCMEM_EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
# allauth が登録フォーム待ちの Discord ログインを置くセッションのキー
PENDING_DISCORD_SIGNUP_SESSION_KEY = 'socialaccount_sociallogin'
# 本番の MySQL には作られない allauth の条件付き unique 制約（SQLite のテスト DB にだけ存在する）
PARTIAL_UNIQUE_INDEXES = ('unique_verified_email', 'unique_primary_email')
# 0018 が依存する allauth の migration。過去の EmailAddress モデルを得るために状態へ含める
ALLAUTH_EMAIL_STATE = ('account', '0009_emailaddress_unique_primary_email')
# 持ち主の表だけがあり、0018 の埋め込みの前の状態
EMAIL_OWNERSHIP_TABLE_STATE = ('user_account', '0017_emailownership')


def drop_partial_unique_indexes():
    """MySQL と同じく allauth の条件付き unique 制約が無い状態にする（テストのロールバックで戻る）。"""
    if connection.vendor != 'sqlite':
        return
    with connection.cursor() as cursor:
        for name in PARTIAL_UNIQUE_INDEXES:
            cursor.execute(f'DROP INDEX IF EXISTS "{name}"')


def owners_of(email):
    """email を主アドレス、または確認済み・primary の EmailAddress として持つユーザーの ID。"""
    users = set(User.objects.filter(email__iexact=email).values_list('pk', flat=True))
    rows = EmailAddress.objects.filter(email__iexact=email).exclude(verified=False, primary=False)
    return users | set(rows.values_list('user_id', flat=True))


def recorded_owners(email):
    """EmailOwnership に記録された email の持ち主の ID。"""
    return set(EmailOwnership.objects.filter(email=email.lower()).values_list('user_id', flat=True))


def add_verified_secondary(user, email):
    return EmailAddress.objects.create(user=user, email=email, verified=True, primary=False)


def confirm_email(client, address):
    url = reverse('account_confirm_email', args=[EmailConfirmationHMAC(address).key])
    client.get(url)
    return client.post(url)


class EmailOwnershipConstraintTests(TestCase):
    """どの書き込みでも、同じアドレス（大文字小文字は区別しない）の持ち主は 1 人に限られる。"""

    def setUp(self):
        super().setUp()
        drop_partial_unique_indexes()
        self.owner = make_user('owner', 'owner@example.com')

    def test_primary_address_is_recorded_in_lower_case(self):
        user = make_legacy_user('legacy', email='Legacy.Mixed@Example.com')

        self.assertEqual(recorded_owners('legacy.mixed@example.com'), {user.pk})

    def test_new_user_cannot_take_a_verified_secondary_address_in_another_case(self):
        add_verified_secondary(self.owner, SHARED_EMAIL)

        with self.assertRaises(IntegrityError):
            make_legacy_user('taker', email='Shared-Address@Example.COM')

        self.assertFalse(User.objects.filter(user_name='taker').exists())
        self.assertEqual(owners_of(SHARED_EMAIL), {self.owner.pk})

    def test_another_user_cannot_verify_a_primary_address(self):
        other = make_user('other', 'other@example.com')

        with self.assertRaises(IntegrityError):
            add_verified_secondary(other, 'OWNER@example.com')

        self.assertFalse(EmailAddress.objects.filter(user=other, email__iexact='owner@example.com').exists())
        self.assertEqual(owners_of('owner@example.com'), {self.owner.pk})

    def test_two_users_cannot_verify_the_same_secondary_address(self):
        other = make_user('other', 'other@example.com')
        add_verified_secondary(self.owner, SHARED_EMAIL)

        with self.assertRaises(IntegrityError):
            add_verified_secondary(other, SHARED_EMAIL)

        self.assertEqual(owners_of(SHARED_EMAIL), {self.owner.pk})

    def test_pending_change_does_not_make_an_owner(self):
        other = make_user('other', 'other@example.com')

        EmailAddress.objects.create(user=other, email='owner@example.com', verified=False, primary=False)

        self.assertEqual(recorded_owners('owner@example.com'), {self.owner.pk})

    def test_removed_address_can_be_verified_by_another_user(self):
        address = add_verified_secondary(self.owner, SHARED_EMAIL)
        other = make_user('other', 'other@example.com')

        address.delete()
        add_verified_secondary(other, SHARED_EMAIL)

        self.assertEqual(recorded_owners(SHARED_EMAIL), {other.pk})

    @override_settings(EMAIL_BACKEND=LOCMEM_EMAIL_BACKEND)
    def test_confirmed_email_change_moves_ownership_to_the_new_address(self):
        pending = EmailAddress.objects.create(
            user=self.owner, email='moved@example.com', verified=False, primary=False,
        )

        confirm_email(Client(), pending)

        self.owner.refresh_from_db()
        self.assertEqual(self.owner.email, 'moved@example.com')
        self.assertEqual(set(self.owner.email_ownerships.values_list('email', flat=True)), {'moved@example.com'})
        # 手放した旧アドレスは別ユーザーが使える
        make_user('next_owner', 'owner@example.com')

    def test_deleting_a_user_frees_its_addresses(self):
        add_verified_secondary(self.owner, SHARED_EMAIL)

        self.owner.delete()

        self.assertFalse(EmailOwnership.objects.exists())
        make_user('next_owner', 'owner@example.com')
        make_user('next_secondary_owner', SHARED_EMAIL)

    def test_same_user_recorded_meanwhile_is_not_a_conflict(self):
        """二重送信などで、判定の直後に同じユーザーの記録ができていても失敗にしない。"""
        with patch('user_account.email_ownership._is_recorded_owner', side_effect=[False, True]):
            claim_email(self.owner.pk, 'owner@example.com')

        self.assertEqual(recorded_owners('owner@example.com'), {self.owner.pk})

    def test_saving_other_fields_does_not_touch_ownership(self):
        """ログインごとの last_login の更新などに、持ち主の確認の問い合わせを足さない。"""
        with self.assertNumQueries(1):
            self.owner.save(update_fields=['last_login'])

    def test_moving_a_row_to_another_user_releases_the_old_owner(self):
        """管理画面などで確認済みの行の user を付け替えて確認を外すと、旧ユーザーの記録も消える。"""
        address = add_verified_secondary(self.owner, SHARED_EMAIL)
        other = make_user('other', 'other@example.com')

        address.user = other
        address.verified = False
        address.save()

        self.assertEqual(recorded_owners(SHARED_EMAIL), set())
        out = StringIO()
        call_command('audit_email_ownership', stdout=out)
        self.assertIn('missing=0 stale=0', out.getvalue())


class EmailAddressWriteTransactionTests(TransactionTestCase):
    """外側のトランザクションが無くても、EmailAddress の書き込みと持ち主の記録は 1 つにまとまる。

    allauth は管理画面のアクションなどで atomic() の外から直接 save() を呼ぶため、その形で確かめる。
    """

    def setUp(self):
        super().setUp()
        self.owner = make_user('owner', 'owner@example.com')

    def test_failed_save_leaves_no_ownership_record(self):
        EmailAddress.objects.create(user=self.owner, email=PENDING_EMAIL, verified=False, primary=False)

        # pre_save で持ち主を記録した後、同じユーザーの確認待ちの行と (user, email) が重なって INSERT が失敗する
        with self.assertRaises(IntegrityError):
            EmailAddress.objects.create(user=self.owner, email=PENDING_EMAIL, verified=True, primary=False)

        self.assertFalse(EmailOwnership.objects.filter(email=PENDING_EMAIL).exists())
        self.assertFalse(is_email_in_use(PENDING_EMAIL))

    def test_locks_the_owner_inside_a_transaction_before_writing(self):
        """持ち主のロックは、トランザクションの中で、行を書く前に取る。

        MySQL はトランザクションの外の select_for_update をエラーにするが、SQLite は素通りするので、呼んだ時の状態を見る。
        行を書いた後に取ると、同じユーザーの並行した書き込み同士が MySQL でデッドロックする。
        """
        locks = []

        def spy(user_id):
            row_written = EmailAddress.objects.filter(email=PENDING_EMAIL).exists()
            locks.append((user_id, connection.in_atomic_block, row_written))
            lock_owner(user_id)

        with patch('user_account.email_ownership.lock_owner', side_effect=spy):
            EmailAddress.objects.create(user=self.owner, email=PENDING_EMAIL, verified=False, primary=False).delete()
            claim_email(self.owner.pk, SHARED_EMAIL)
            release_unowned_emails(self.owner.pk)
            sync_email_ownership(self.owner.pk)

        self.assertEqual(locks[0], (self.owner.pk, True, False))
        self.assertTrue(all(user_id == self.owner.pk and in_transaction for user_id, in_transaction, _ in locks))

    def test_bulk_delete_locks_the_owner_before_deleting(self):
        """管理画面の一括削除などの QuerySet.delete() も、行を消す前に持ち主をロックする（保存と同じ順番）。

        QuerySet.delete() とユーザー削除のカスケードはモデルの delete() を通らないので、包みではなく pre_delete で取る。
        """
        add_verified_secondary(self.owner, SHARED_EMAIL)
        locks = []

        def spy(user_id):
            row_present = EmailAddress.objects.filter(email=SHARED_EMAIL).exists()
            locks.append((user_id, connection.in_atomic_block, row_present))
            lock_owner(user_id)

        with patch('user_account.email_ownership.lock_owner', side_effect=spy):
            EmailAddress.objects.filter(email=SHARED_EMAIL).delete()

        self.assertEqual(locks[0], (self.owner.pk, True, True))
        self.assertFalse(EmailOwnership.objects.filter(email=SHARED_EMAIL).exists())

    def test_moving_a_row_locks_both_owners_in_id_order_before_writing(self):
        """user を付け替える保存は、書く前に旧ユーザーと新ユーザーを id の昇順でロックする（2 人をロックする書き込み同士で循環しない）。"""
        address = EmailAddress.objects.create(user=self.owner, email=PENDING_EMAIL, verified=False, primary=False)
        other = make_user('other', 'other@example.com')
        locks = []

        def spy(user_id):
            locks.append((user_id, EmailAddress.objects.get(pk=address.pk).user_id))
            lock_owner(user_id)

        address.user = other
        with patch('user_account.email_ownership.lock_owner', side_effect=spy):
            address.save()

        # id の小さい旧ユーザーから大きい新ユーザーへ付け替え、新ユーザーを先に取る実装と区別する。2 件とも行はまだ旧ユーザーのもの
        self.assertEqual(locks[:2], [(self.owner.pk, self.owner.pk), (other.pk, self.owner.pk)])


@override_settings(EMAIL_BACKEND=LOCMEM_EMAIL_BACKEND, SOCIALACCOUNT_PROVIDERS=TEST_SOCIALACCOUNT_PROVIDERS_WITH_APPS)
class ConcurrentOwnershipTests(TestCase):
    """判定の直後に別ユーザーが同じアドレスの持ち主になっても、どの経路も持ち主を 2 人にしない。

    相手（rival）は主アドレスが別で、このアドレスを確認済みの副アドレスとして持つ。
    CustomUser.email の一意制約では止まらず、EmailOwnership の一意制約だけが止める形にしてある。
    """

    def setUp(self):
        super().setUp()
        cache.clear()
        drop_partial_unique_indexes()
        self.rival = make_user('rival', 'rival@example.com')

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _taken_right_after_check(self, stale_checks=1):
        """is_email_in_use の差し替え: 1 回目の判定の直後に rival が持ち主になり、stale_checks 回目まで空きと答える。"""
        calls = []

        def check(email, **kwargs):
            calls.append(email)
            if len(calls) == 1:
                add_verified_secondary(self.rival, email)
            if len(calls) <= stale_checks:
                return False
            return is_email_in_use(email, **kwargs)

        return check

    def _assert_only_rival_owns(self, email):
        self.assertEqual(owners_of(email), {self.rival.pk})
        self.assertEqual(recorded_owners(email), {self.rival.pk})

    def _assert_guide_mail_sent(self, email):
        self.assertEqual(mail.outbox[-1].to, [email])
        self.assertIn(ACCOUNT_EXISTS_MAIL_PHRASE, mail.outbox[-1].body)

    @staticmethod
    def _discord_callback_request():
        request = RequestFactory().get('/accounts/discord/login/callback/')
        request.user = AnonymousUser()
        SessionMiddleware(lambda request: None).process_request(request)
        request.session.save()
        MessageMiddleware(lambda request: None).process_request(request)
        return request

    @override_settings(SOCIALACCOUNT_PROVIDERS=TEST_SOCIALACCOUNT_PROVIDERS)
    def test_local_signup_gets_the_registered_response(self):
        email = 'race-local@example.com'
        data = {'user_name': 'race_local', 'email': email, 'password1': SIGNUP_PASSWORD, 'password2': SIGNUP_PASSWORD}

        with patch('user_account.forms.is_email_in_use', side_effect=self._taken_right_after_check()):
            with self.assertLogs('user_account.view_modules.session', level='WARNING'):
                response = Client().post(reverse('account:register'), data)

        self.assertRedirects(response, CONFIRM_EMAIL_SENT_PATH, fetch_redirect_response=False)
        self.assertFalse(User.objects.filter(user_name='race_local').exists())
        self._assert_only_rival_owns(email)
        self._assert_guide_mail_sent(email)

    def test_discord_signup_form_gets_the_registered_response(self):
        email = 'race-discord-form@example.com'
        provider = CustomSocialAccountAdapter().get_provider(RequestFactory().get('/'), 'discord')
        sociallogin = SocialLogin(
            provider=provider,
            user=User(user_name='race_form', display_name='race_form'),
            account=SocialAccount(provider='discord', uid='race-form-uid', extra_data={'username': 'race_form'}),
        )
        client = Client()
        session = client.session
        session[PENDING_DISCORD_SIGNUP_SESSION_KEY] = sociallogin.serialize()
        session.save()

        # フォームの判定と保存直前の確認の 2 回とも、rival が持ち主になる前の結果を返す
        with patch('user_account.forms.is_email_in_use', side_effect=self._taken_right_after_check(stale_checks=2)):
            with self.assertLogs('user_account.forms', level='WARNING'):
                response = client.post(reverse('socialaccount_signup'), {'email': email, 'user_name': 'race_form'})

        self.assertRedirects(response, CONFIRM_EMAIL_SENT_PATH, fetch_redirect_response=False)
        self.assertFalse(User.objects.filter(user_name='race_form').exists())
        self.assertFalse(SocialAccount.objects.filter(uid='race-form-uid').exists())
        self._assert_only_rival_owns(email)
        self._assert_guide_mail_sent(email)

    def test_discord_auto_signup_gets_the_registered_response(self):
        email = 'race-discord-auto@example.com'
        request = self._discord_callback_request()
        sociallogin = SocialLogin(
            provider=CustomSocialAccountAdapter().get_provider(request, 'discord'),
            user=User(user_name='race_auto', display_name='race_auto', email=email),
            account=SocialAccount(
                provider='discord',
                uid='race-auto-uid',
                extra_data={'email': email, 'verified': True, 'username': 'race_auto'},
            ),
            email_addresses=[EmailAddress(email=email, verified=True, primary=True)],
        )

        def unique_until_taken(checked_email):
            # allauth の自動登録の判定（assess_unique_email）の直後に rival が持ち主になる
            add_verified_secondary(self.rival, checked_email)
            return True

        with request_context(request), patch(
            'allauth.socialaccount.internal.flows.signup.assess_unique_email', side_effect=unique_until_taken,
        ):
            sociallogin.state = SocialLogin.state_from_request(request)
            with self.assertLogs('user_account.adapters', level='WARNING'), self.assertRaises(
                ImmediateHttpResponse,
            ) as raised:
                process_signup(request, sociallogin)

        self.assertEqual(raised.exception.response.headers['Location'], CONFIRM_EMAIL_SENT_PATH)
        self.assertFalse(User.objects.filter(user_name='race_auto').exists())
        self.assertFalse(SocialAccount.objects.filter(uid='race-auto-uid').exists())
        self._assert_only_rival_owns(email)
        self._assert_guide_mail_sent(email)

    def test_secondary_address_confirmation_is_rejected(self):
        email = 'race-confirm@example.com'
        claimant = make_user('claimant', 'claimant@example.com')
        pending = EmailAddress.objects.create(user=claimant, email=email, verified=False, primary=False)

        # 自前の判定と allauth の判定（can_set_verified）の時点では、rival はまだ持ち主でなかった状態を再現する
        with patch('user_account.adapters.is_email_in_use', side_effect=self._taken_right_after_check()), \
                patch.object(EmailAddress, 'can_set_verified', return_value=True), \
                self.assertLogs('user_account.adapters', level='WARNING'):
            response = confirm_email(Client(), pending)

        self.assertEqual(response.status_code, 302)
        pending.refresh_from_db()
        claimant.refresh_from_db()
        self.assertFalse(pending.verified)
        self.assertEqual(claimant.email, 'claimant@example.com')
        self._assert_only_rival_owns(email)


class AuditEmailOwnershipCommandTests(TestCase):
    """持ち主の重複と記録のずれを件数だけ表示し、--repair で記録を合わせ直す。"""

    def setUp(self):
        super().setUp()
        drop_partial_unique_indexes()
        self.owner = make_user('owner', 'owner@example.com')

    @staticmethod
    def _audit(*args):
        """監査を実行し、(出力, 監査に失敗したか) を返す。"""
        out = StringIO()
        try:
            call_command('audit_email_ownership', *args, stdout=out)
        except CommandError:
            return out.getvalue(), True
        return out.getvalue(), False

    def test_passes_when_every_owner_is_recorded(self):
        add_verified_secondary(self.owner, SHARED_EMAIL)

        output, failed = self._audit()

        self.assertFalse(failed)
        self.assertIn('addresses=2 conflicts=0 missing=0 stale=0', output)

    def test_reports_conflicts_without_printing_addresses(self):
        other = make_user('other', 'other@example.com')
        # シグナルを通らない一括作成で、移行前のデータに残りうる重複を作る
        EmailAddress.objects.bulk_create([
            EmailAddress(user=other, email='owner@example.com', verified=True, primary=False),
        ])

        output, failed = self._audit()

        self.assertTrue(failed)
        self.assertIn('conflicts=1', output)
        self.assertNotIn('owner@example.com', output)

    def test_reports_only_conflicts_before_the_table_exists(self):
        with patch.object(connection.introspection, 'table_names', return_value=[]):
            output, failed = self._audit()

        self.assertFalse(failed)
        self.assertIn('addresses=1 conflicts=0', output)
        self.assertNotIn('missing', output)

    def test_repair_records_missing_owners_and_drops_stale_records(self):
        EmailOwnership.objects.filter(user=self.owner).delete()
        EmailOwnership.objects.create(email='gone@example.com', user=self.owner)
        output, failed = self._audit()
        self.assertTrue(failed)
        self.assertIn('missing=1 stale=1', output)

        output, failed = self._audit('--repair')

        self.assertFalse(failed)
        self.assertIn('repair_conflicted_users=0', output)
        self.assertIn('missing=0 stale=0', output)
        self.assertEqual(
            set(EmailOwnership.objects.values_list('email', 'user_id')),
            {('owner@example.com', self.owner.pk)},
        )


class EmailOwnershipBackfillMigrationTests(TransactionTestCase):
    """既存のアカウントから持ち主の記録を作り、2 人が持つアドレスがあれば値を出さずに止まる。"""

    migrate_from = [('user_account', '0016_login_rate_limit_cache')]
    migrate_to = [('user_account', '0018_backfill_email_ownership')]

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from + [ALLAUTH_EMAIL_STATE]).apps
        # 今のモデルのシグナルは EmailOwnership の表を使うため、表の無い状態では過去のモデルで作る
        self.OldUser = old_apps.get_model('user_account', 'CustomUser')
        self.OldEmailAddress = old_apps.get_model('account', 'EmailAddress')

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def _migrate_forward(self):
        MigrationExecutor(connection).migrate(self.migrate_to)

    def test_records_primary_and_owned_addresses_in_lower_case(self):
        user = self.OldUser.objects.create(user_name='legacy', email='Legacy@Example.com')
        self.OldUser.objects.create(user_name='blank', email='')
        for email, verified, primary in (
            ('legacy@example.com', True, True),
            ('second@example.com', True, False),
            ('pending@example.com', False, False),
        ):
            self.OldEmailAddress.objects.create(user_id=user.pk, email=email, verified=verified, primary=primary)

        self._migrate_forward()

        self.assertEqual(
            set(EmailOwnership.objects.values_list('email', 'user_id')),
            {('legacy@example.com', user.pk), ('second@example.com', user.pk)},
        )

    def test_stops_when_two_users_own_the_same_address(self):
        self.OldUser.objects.create(user_name='owner', email='Owner@Example.com')
        other = self.OldUser.objects.create(user_name='other', email='other@example.com')
        conflict = self.OldEmailAddress.objects.create(
            user_id=other.pk, email='owner@example.com', verified=True, primary=False,
        )

        with self.assertRaises(RuntimeError) as raised:
            self._migrate_forward()

        message = str(raised.exception)
        self.assertIn('ownership conflict', message)
        self.assertIn('addresses=1', message)
        self.assertNotIn('owner@example.com', message.lower())
        conflict.delete()

    def test_hides_the_database_error_that_names_the_address(self):
        """表に 0018 より前の記録があって一意制約に当たった時も、元の例外（MySQL の 1062 は値を含む）を出さずに止まる。"""
        user = self.OldUser.objects.create(user_name='legacy', email='legacy@example.com')
        executor = MigrationExecutor(connection)
        executor.migrate([EMAIL_OWNERSHIP_TABLE_STATE])
        table_apps = executor.loader.project_state([EMAIL_OWNERSHIP_TABLE_STATE, ALLAUTH_EMAIL_STATE]).apps
        early = table_apps.get_model('user_account', 'EmailOwnership').objects.create(
            email='legacy@example.com', user_id=user.pk,
        )

        with self.assertRaises(RuntimeError) as raised:
            self._migrate_forward()

        self.assertIsNone(raised.exception.__cause__)
        self.assertTrue(raised.exception.__suppress_context__)
        self.assertNotIn('legacy@example.com', str(raised.exception).lower())
        early.delete()


class EmailOwnershipCollationMigrationTests(SimpleTestCase):
    """0017 は MySQL の時だけ email 列を完全一致の照合順序にする（SQLite には utf8mb4_bin が無い）。"""

    def test_alters_the_column_only_on_mysql(self):
        migration = import_module('user_account.migrations.0017_emailownership')
        for vendor, expected in (('mysql', [call(migration.EXACT_MATCH_COLUMN_SQL)]), ('sqlite', [])):
            schema_editor = Mock()
            schema_editor.connection.vendor = vendor

            migration.use_exact_match_collation_on_mysql(None, schema_editor)

            self.assertEqual(schema_editor.execute.call_args_list, expected)
        self.assertIn('COLLATE utf8mb4_bin', migration.EXACT_MATCH_COLUMN_SQL)
        # MySQL ではトランザクションの中の DDL を Django が拒むので、この操作は atomic にしない
        self.assertIs(migration.Migration.operations[-1].atomic, False)
