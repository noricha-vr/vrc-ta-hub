from django import forms

from .models import CalendarEntry


class CalendarEntryForm(forms.ModelForm):
    event_genres = forms.MultipleChoiceField(
        label='イベントジャンル',
        choices=CalendarEntry.EVENT_GENRE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False
    )

    class Meta:
        model = CalendarEntry
        fields = ['join_condition', 'event_detail', 'how_to_join', 'note', 'is_overseas_user', 'event_genres']
        widgets = {
            'join_condition': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'event_detail': forms.Textarea(attrs={'class': 'form-control', 'rows': 5}),
            'how_to_join': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'note': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'is_overseas_user': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'event_genres': forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['event_genres'].choices = CalendarEntry.EVENT_GENRE_CHOICES
        calendar_entry = self.instance.calendar_entry
        self.fields['join_condition'].initial = calendar_entry.join_condition
        self.fields['event_detail'].initial = calendar_entry.event_detail
        self.fields['how_to_join'].initial = calendar_entry.how_to_join
        self.fields['note'].initial = calendar_entry.note
        self.fields['is_overseas_user'].initial = calendar_entry.is_overseas_user
        self.fields['event_genres'].initial = calendar_entry.event_genres
