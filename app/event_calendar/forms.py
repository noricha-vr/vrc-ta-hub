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
        if self.instance and self.instance.pk:
            self.fields['event_genres'].initial = self.instance.event_genres
