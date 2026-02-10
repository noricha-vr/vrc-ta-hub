from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django import forms
from django.utils import timezone

from community.models import Community
from event.models import Event

from .models import VketCollaboration, VketParticipation


def _daterange(start: date, end: date) -> list[date]:
    if start > end:
        return []
    days = (end - start).days
    return [start + timedelta(days=i) for i in range(days + 1)]


def _weekday_code(d: date) -> str:
    # Python: Monday=0 ... Sunday=6
    mapping = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    return mapping[d.weekday()]


def _build_participation_date_choices(
    collaboration: VketCollaboration, community: Community
) -> list[tuple[str, str]]:
    all_dates = _daterange(collaboration.period_start, collaboration.period_end)
    if not all_dates:
        return []

    weekdays = set(community.weekdays or [])
    if not weekdays or 'Other' in weekdays:
        selected = all_dates
    else:
        selected = [d for d in all_dates if _weekday_code(d) in weekdays]
        if not selected:
            selected = all_dates

    weekday_jp = ['月', '火', '水', '木', '金', '土', '日']
    labels = []
    for d in selected:
        # ex: 7/13(日)
        labels.append((d.isoformat(), f'{d.month}/{d.day}({weekday_jp[d.weekday()]})'))
    return labels


def _build_duration_choices(default_minutes: int) -> list[tuple[int, str]]:
    base = [30, 60, 90]
    if default_minutes and default_minutes not in base:
        base.append(default_minutes)
    base = sorted(set(base))
    return [(m, f'{m}分') for m in base]


@dataclass(frozen=True)
class VketApplyPermissions:
    can_edit_schedule: bool
    can_edit_lt: bool


class VketApplyForm(forms.Form):
    participation_date = forms.TypedChoiceField(
        label='参加日',
        coerce=date.fromisoformat,
        widget=forms.RadioSelect,
    )
    start_time = forms.TimeField(
        label='開始時刻',
        widget=forms.TimeInput(attrs={'type': 'time', 'step': 300}),
    )
    duration = forms.TypedChoiceField(
        label='開催時間（分）',
        coerce=int,
    )
    speaker = forms.CharField(label='登壇者名', max_length=200, required=False)
    theme = forms.CharField(label='テーマ', max_length=100, required=False)
    note = forms.CharField(label='備考', required=False, widget=forms.Textarea(attrs={'rows': 3}))

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

        self.fields['participation_date'].choices = _build_participation_date_choices(collaboration, community)
        self.fields['duration'].choices = _build_duration_choices(default_minutes=community.duration)

        if not permissions.can_edit_schedule:
            self.fields['participation_date'].disabled = True
            self.fields['start_time'].disabled = True
            self.fields['duration'].disabled = True

        if not permissions.can_edit_lt:
            self.fields['speaker'].disabled = True
            self.fields['theme'].disabled = True
            self.fields['note'].disabled = True

    def clean(self):
        cleaned = super().clean()
        if not self.permissions.can_edit_schedule:
            return cleaned

        participation_date: date | None = cleaned.get('participation_date')
        start_time: time | None = cleaned.get('start_time')

        if participation_date and (
            participation_date < self.collaboration.period_start
            or participation_date > self.collaboration.period_end
        ):
            raise forms.ValidationError('参加日は開催期間内の日付を選択してください。')

        if start_time is None:
            raise forms.ValidationError('開始時刻を入力してください。')

        duration: int | None = cleaned.get('duration')
        if duration is None or duration <= 0:
            raise forms.ValidationError('開催時間（分）は正の値を選択してください。')

        # Event uniqueness check (community, date, start_time)
        current_event_id = self.participation.event_id if self.participation and self.participation.event_id else None
        if Event.objects.filter(
            community=self.community,
            date=participation_date,
            start_time=start_time,
        ).exclude(id=current_event_id).exists():
            raise forms.ValidationError('同じ日付・開始時刻のイベントが既に存在します。別の時間を選んでください。')

        return cleaned


class VketManageEventEditForm(forms.Form):
    date = forms.DateField(label='日付', widget=forms.DateInput(attrs={'type': 'date'}))
    start_time = forms.TimeField(label='時刻', widget=forms.TimeInput(attrs={'type': 'time', 'step': 300}))
    admin_note = forms.CharField(label='備考（運営）', required=False, widget=forms.Textarea(attrs={'rows': 2}))

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

    def clean_date(self) -> date:
        d = self.cleaned_data['date']
        if d < self.collaboration.period_start or d > self.collaboration.period_end:
            raise forms.ValidationError('開催期間内の日付を選択してください。')
        return d

    def clean(self):
        cleaned = super().clean()

        if not self.participation.event_id:
            raise forms.ValidationError('日程が未登録のため編集できません。')

        d: date | None = cleaned.get('date')
        t: time | None = cleaned.get('start_time')
        if not d or not t:
            return cleaned

        event = self.participation.event
        if Event.objects.filter(
            community=event.community,
            date=d,
            start_time=t,
        ).exclude(id=event.id).exists():
            raise forms.ValidationError('同じ日付・開始時刻のイベントが既に存在します。')

        return cleaned
