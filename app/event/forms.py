# forms.py

from django import forms
from community.models import WEEKDAY_CHOICES, TAGS

# forms.py

from django import forms
from community.models import WEEKDAY_CHOICES, TAGS

from django import forms
from community.models import WEEKDAY_CHOICES


class EventSearchForm(forms.Form):
    name = forms.CharField(label='集会名', required=False)
    weekday = forms.MultipleChoiceField(label='曜日', choices=WEEKDAY_CHOICES, required=False,
                                        widget=forms.CheckboxSelectMultiple)
