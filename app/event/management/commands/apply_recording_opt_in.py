"""撮影をオプトインに移行し、過去の既定値の公開を許可に戻す（冪等）。"""

import json
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from community.models import Community
from event.models import Event, EventDetail


def _parse_iso_datetime(value):
    """タイムゾーン無しの日時は、設定のタイムゾーンとみなす。"""
    try:
        parsed = parse_datetime(value)
    except ValueError:
        parsed = None
    if parsed is None:
        raise CommandError(f"日時の形式が正しくありません（ISO 8601）: {value}")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


class Command(BaseCommand):
    help = (
        "指定した集会以外の撮影許可をオフにし、URL のない発表を禁止にします。"
        "過去の既定値のままの公開は、集会や URL によらず許可（公開しない）にします。"
        "変更前の値を JSON で出力します。--dry-run では報告だけで書き換えません。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--keep-community-id', type=int, action='append', required=True,
            help='撮影の許可を残す集会の ID（複数回指定可）。',
        )
        parser.add_argument('--dry-run', action='store_true', help='変更せず、対象件数と変更前の値を表示します。')
        parser.add_argument(
            '--defaults-before', type=_parse_iso_datetime, default='2026-09-28T16:57:12+00:00',
            help='この日時より前に作られ、動画撮影の回答がない public を既定値とみなします（ISO 8601）。',
        )

    def handle(self, *args, **options):
        self._apply(options)
        # 本番では Job のログから結果を読む。取り込み途中のログで判断しないよう、最後まで出たことを示す。
        self.stdout.write(f'RECORDING_OPT_IN_DONE dry_run={options["dry_run"]}')

    def _apply(self, options):
        keep_ids = set(options['keep_community_id'])
        communities = Community._base_manager.all()
        kept_communities = list(communities.filter(pk__in=keep_ids).order_by('pk').values(
            'pk', 'name', 'recording_allowed',
        ))
        missing_ids = keep_ids - {row['pk'] for row in kept_communities}
        if missing_ids:
            raise CommandError(f'指定した集会 ID が存在しません: {sorted(missing_ids)}')

        today = timezone.localdate()
        cutoff = options['defaults_before']
        policy = EventDetail.RecordingPolicy
        empty_youtube = Q(youtube_url__isnull=True) | Q(youtube_url='')
        # 0031 で public に埋まり、登壇者の回答で当て直されなかった発表。b で allowed にした後も
        # c の対象から外すため、方針によらない条件（default_origin）を別に持つ。
        default_origin = Q(created_at__lt=cutoff) & (
            Q(additional_info__isnull=True) | ~Q(additional_info__contains='動画撮影')
        )
        default_public = Q(recording_policy=policy.PUBLIC) & default_origin
        outside_keep = ~Q(event__community_id__in=keep_ids)
        not_forbidden = ~Q(recording_policy=policy.FORBIDDEN)
        # a → b → c の優先順。b と c は明示的に排他的にし、b の結果を再実行の c で上書きしない。
        rules = {
            'a': outside_keep & Q(event__date__gte=today) & empty_youtube & not_forbidden,
            'b': Q(event__date__lt=today) & default_public,
            'c': outside_keep & Q(event__date__lt=today) & empty_youtube & not_forbidden & ~default_origin,
        }
        other_events = Event.objects.exclude(community_id__in=keep_ids)
        # MySQL の関連テーブルを JOIN する update は SELECT と UPDATE に分かれる。
        # Event の副問い合わせにし、旧値の確認を一つの UPDATE の条件に残す。
        update_rules = {
            'a': Q(event_id__in=other_events.filter(date__gte=today).values('pk')) & empty_youtube & not_forbidden,
            'b': Q(event_id__in=Event.objects.filter(date__lt=today).values('pk')) & default_public,
            'c': Q(event_id__in=other_events.filter(date__lt=today).values('pk')) & empty_youtube & not_forbidden & ~default_origin,
        }
        details = EventDetail.all_objects.all()
        fields = (
            'pk', 'recording_policy', 'event_id', 'event__date', 'event__community_id',
            'youtube_url', 'created_at', 'additional_info',
        )
        rule_rows = {}
        seen_ids = set()
        for name, condition in rules.items():
            # 規則ごとの集計の合間に値が変わると、同じ発表が 2 つの規則に入り得る。優先順で最初の規則だけに残す。
            rows = details.filter(condition).order_by('pk').values(*fields)
            rule_rows[name] = [row for row in rows if row['pk'] not in seen_ids]
            seen_ids.update(row['pk'] for row in rule_rows[name])
        community_rows = list(communities.exclude(pk__in=keep_ids).filter(recording_allowed=True).order_by('pk').values_list(
            'pk', 'recording_allowed',
        ))
        changed_ids = {row['pk'] for rows in rule_rows.values() for row in rows}
        unchanged_with_url = list(details.filter(outside_keep & not_forbidden).exclude(empty_youtube).exclude(
            pk__in=changed_ids,
        ).order_by('pk').values(*fields[:5]))
        future_with_url_ids = list(details.filter(outside_keep, event__date__gte=today).exclude(
            empty_youtube,
        ).order_by('pk').values_list('pk', flat=True))
        backup = {
            'communities': {str(pk): allowed for pk, allowed in community_rows},
            'event_details': {
                str(row['pk']): row['recording_policy'] for rows in rule_rows.values() for row in rows
            },
        }
        self.stdout.write('RECORDING_OPT_IN_BACKUP ' + json.dumps(backup, sort_keys=True))
        self.stdout.write(f'対象 Community（規則 1）: {len(community_rows)}件 / dry_run={options["dry_run"]}')
        for name in ('a', 'c'):
            old_counts = Counter(row['recording_policy'] for row in rule_rows[name])
            self.stdout.write(
                f'規則 {name} → forbidden: {len(rule_rows[name])}件 '
                f'(旧 public={old_counts[policy.PUBLIC]}件 / allowed={old_counts[policy.ALLOWED]}件)'
            )
        b_rows = rule_rows['b']
        b_keep_count = sum(row['event__community_id'] in keep_ids for row in b_rows)
        b_url_count = sum(bool(row['youtube_url']) for row in b_rows)
        self.stdout.write(
            f'規則 b → allowed: {len(b_rows)}件 '
            f'(K の集会={b_keep_count}件 / K 以外={len(b_rows) - b_keep_count}件, '
            f'URL あり={b_url_count}件 / なし={len(b_rows) - b_url_count}件)'
        )
        for row in rule_rows['a']:
            self._write_detail(row, old=True)
        self.stdout.write(f'YouTube URL があり変更しない EventDetail（規則 3）: {len(unchanged_with_url)}件')
        for row in unchanged_with_url:
            self._write_detail(row)
        self.stdout.write(
            f'K 以外のこれからの発表で YouTube URL あり: {len(future_with_url_ids)}件 ids={future_with_url_ids}'
        )

        for community in kept_communities:
            keep_id = community['pk']
            kept_details = details.filter(event__community_id=keep_id)
            default_count = kept_details.filter(default_public).count()
            converted_count = sum(row['event__community_id'] == keep_id for row in b_rows)
            remaining_public_count = kept_details.filter(recording_policy=policy.PUBLIC).count() - converted_count
            self.stdout.write(
                # 集会名は主催者が入力した値。改行や U+2028 などで偽の行を作らせないよう、ASCII の JSON 文字列で出す。
                f'残す Community: id={keep_id} 名前={json.dumps(community["name"])} recording_allowed={community["recording_allowed"]} / '
                f'既定値のまま public（書き換え前）={default_count}件 / '
                f'書き換え後に public のまま（予定）={remaining_public_count}件 '
                f'(defaults_before={cutoff.isoformat()})'
            )
            future_kept_rows = list(kept_details.filter(event__date__gte=today).order_by('event__date', 'pk').values(*fields[:5]))
            self.stdout.write(f'  これからの発表（変更なし）: {len(future_kept_rows)}件')
            for row in future_kept_rows:
                self._write_detail(row)
            next_event = Event.objects.filter(community_id=keep_id, date__gte=today).order_by('date').values_list(
                'date', flat=True,
            ).first()
            self.stdout.write(f'  次の開催日={next_event or "-"}')

        if not community_rows and not changed_ids:
            self.stdout.write('変更はありません。')
            return
        if options['dry_run']:
            self.stdout.write('dry-run のため変更していません。')
            return

        # 集計後に変わった行は条件付き UPDATE が飛ばす。控えで戻す時に新しい値を上書きしないよう、
        # 1 行ずつ更新して、実際に変えた ID と飛ばした ID を分けて出す。
        # APPLIED は「ID: 実際に更新できた時の旧値」。戻す時はこの行を正本にする。
        applied = {'communities': {}, 'event_details': {}}
        skipped = {'communities': [], 'event_details': []}
        with transaction.atomic():
            for pk, _ in community_rows:
                updated = communities.exclude(pk__in=keep_ids).filter(pk=pk, recording_allowed=True).update(
                    recording_allowed=False,
                )
                if updated:
                    applied['communities'][str(pk)] = True
                else:
                    skipped['communities'].append(pk)
            for name, rows in rule_rows.items():
                target_policy = policy.ALLOWED if name == 'b' else policy.FORBIDDEN
                for row in rows:
                    old_values = {field: row[field] for field in fields[1:]}
                    unchanged_event = Event.objects.filter(
                        pk=old_values['event_id'], date=old_values.pop('event__date'),
                        community_id=old_values.pop('event__community_id'),
                    ).values('pk')
                    updated = details.filter(
                        update_rules[name], pk=row['pk'], event_id__in=unchanged_event, **old_values,
                    ).update(recording_policy=target_policy)
                    if updated:
                        applied['event_details'][str(row['pk'])] = row['recording_policy']
                    else:
                        skipped['event_details'].append(row['pk'])
        self.stdout.write('RECORDING_OPT_IN_APPLIED ' + json.dumps(applied, sort_keys=True))
        if skipped['communities'] or skipped['event_details']:
            self.stdout.write('RECORDING_OPT_IN_SKIPPED ' + json.dumps(skipped, sort_keys=True))
        community_count, detail_count = len(applied['communities']), len(applied['event_details'])
        self.stdout.write(self.style.SUCCESS(
            f'Community: {community_count}件 / EventDetail: {detail_count}件 を更新しました。'
            f'（集計後に変わったため飛ばした行: Community {len(skipped["communities"])}件 / '
            f'EventDetail {len(skipped["event_details"])}件）'
        ))
        if community_count == 0 and detail_count == 0:
            self.stdout.write('変更はありません。')

    def _write_detail(self, row, *, old=False):
        label = '旧 recording_policy' if old else 'recording_policy'
        self.stdout.write(
            f'  id={row["pk"]} 開催日={row["event__date"].isoformat()} '
            f'{label}={row["recording_policy"]} community_id={row["event__community_id"]}'
        )
