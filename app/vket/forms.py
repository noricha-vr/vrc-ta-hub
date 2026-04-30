from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django import forms
from django.forms import formset_factory

from community.models import Community
from event.models import Event

from .models import VketCollaboration, VketParticipation


def _build_participation_date_choices(
    collaboration: VketCollaboration, community: Community
) -> list[tuple[str, str]]:
    """集会のEvent開催日からコラボ期間内の選択肢を生成する"""
    event_dates = list(
        Event.objects.filter(
            community=community,
            date__gte=collaboration.period_start,
            date__lte=collaboration.period_end,
        )
        .order_by('date')
        .values_list('date', flat=True)
        .distinct()
    )
    if not event_dates:
        return []

    weekday_jp = ['月', '火', '水', '木', '金', '土', '日']
    return [
        (d.isoformat(), f'{d.month}/{d.day}({weekday_jp[d.weekday()]})')
        for d in event_dates
    ]


def _build_duration_choices(default_minutes: int) -> list[tuple[int, str]]:
    """開催時間の選択肢を生成する（30/60/90分 + デフォルト値）"""
    base = [30, 60, 90]
    if default_minutes and default_minutes not in base:
        base.append(default_minutes)
    base = sorted(set(base))
    return [(m, f'{m}分') for m in base]


# 管理画面用の確定時間選択肢
CONFIRMED_DURATION_CHOICES = [
    (30, '30分'),
    (60, '60分'),
    (90, '90分'),
    (120, '120分'),
]


@dataclass(frozen=True)
class VketApplyPermissions:
    can_edit_schedule: bool
    can_edit_lt: bool


class VketPresentationForm(forms.Form):
    """LT情報の1行分"""

    speaker = forms.CharField(
        label='登壇者名',
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    theme = forms.CharField(
        label='テーマ',
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    lt_start_time = forms.TimeField(
        label='LT開始時刻',
        required=False,
        widget=forms.TimeInput(attrs={'type': 'time', 'step': 300, 'class': 'form-control'}),
    )

    def __init__(self, *args, lock_lt_start_time: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        if lock_lt_start_time:
            self.fields['lt_start_time'].disabled = True


VketPresentationFormSet = formset_factory(
    VketPresentationForm, extra=1, max_num=20, can_delete=True,
)


class VketApplyForm(forms.Form):
    """集会主催者向けのVketコラボ参加申請フォーム"""

    requested_date = forms.TypedChoiceField(
        label='参加希望日',
        coerce=date.fromisoformat,
        widget=forms.RadioSelect,
    )
    requested_start_time = forms.TimeField(
        label='開始希望時刻',
        widget=forms.TimeInput(attrs={'type': 'time', 'step': 300}),
    )
    requested_duration = forms.TypedChoiceField(
        label='希望開催時間（分）',
        coerce=int,
    )
    organizer_note = forms.CharField(
        label='備考',
        required=False,
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
    )

    def __init__(
        self,
        *args,
        collaboration: VketCollaboration,
        community: Community,
        participation: VketParticipation | None,
        permissions: VketApplyPermissions,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.collaboration = collaboration
        self.community = community
        self.participation = participation
        self.permissions = permissions

        self.fields['requested_date'].choices = _build_participation_date_choices(
            collaboration, community
        )
        self.fields['requested_duration'].choices = _build_duration_choices(
            default_minutes=community.duration
        )

        # 日程編集不可の場合はスケジュール関連フィールドを無効化
        if not permissions.can_edit_schedule:
            self.fields['requested_date'].disabled = True
            self.fields['requested_start_time'].disabled = True
            self.fields['requested_duration'].disabled = True

        # LT情報編集不可の場合は備考フィールドを無効化
        if not permissions.can_edit_lt:
            self.fields['organizer_note'].disabled = True

    def clean(self):
        cleaned = super().clean()

        # 日程編集不可の場合はバリデーションをスキップ
        if not self.permissions.can_edit_schedule:
            return cleaned

        requested_date: date | None = cleaned.get('requested_date')

        # 希望日が開催期間内かチェック
        if requested_date and (
            requested_date < self.collaboration.period_start
            or requested_date > self.collaboration.period_end
        ):
            raise forms.ValidationError('参加希望日は開催期間内の日付を選択してください。')

        requested_start_time = cleaned.get('requested_start_time')
        if requested_start_time is None:
            raise forms.ValidationError('開始希望時刻を入力してください。')

        requested_duration: int | None = cleaned.get('requested_duration')
        if requested_duration is None or requested_duration <= 0:
            raise forms.ValidationError('希望開催時間（分）は正の値を選択してください。')

        # 注: 確定前はEventを作らないため、イベント重複チェックは不要

        return cleaned


class VketManageParticipationForm(forms.Form):
    """運営向けの参加情報（日程確定・備考）更新フォーム"""

    confirmed_date = forms.DateField(
        label='確定日程',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    confirmed_start_time = forms.TimeField(
        label='確定開始時刻',
        widget=forms.TimeInput(attrs={'type': 'time', 'step': 300}),
    )
    confirmed_duration = forms.TypedChoiceField(
        label='確定開催時間（分）',
        coerce=int,
        choices=CONFIRMED_DURATION_CHOICES,
    )
    admin_note = forms.CharField(
        label='備考（運営）',
        required=False,
        widget=forms.Textarea(attrs={'rows': 2}),
    )

    def __init__(
        self,
        *args,
        participation: VketParticipation,
        collaboration: VketCollaboration,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.participation = participation
        self.collaboration = collaboration

    def clean_confirmed_date(self) -> date:
        d = self.cleaned_data['confirmed_date']
        # 確定日がコラボ開催期間内かチェック
        if d < self.collaboration.period_start or d > self.collaboration.period_end:
            raise forms.ValidationError('確定日程はコラボ開催期間内の日付を選択してください。')
        return d
