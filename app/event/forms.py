# forms.py

from django import forms
from community.models import WEEKDAY_CHOICES, TAGS

# forms.py

from django import forms
from community.models import WEEKDAY_CHOICES, TAGS


class EventSearchForm(forms.Form):
    community_name = forms.CharField(label='コミュニティ名', max_length=100, required=False)
    weekday = forms.ChoiceField(label='曜日', choices=[('', '曜日を選択')] + list(WEEKDAY_CHOICES), required=False)
    # tags = forms.MultipleChoiceField(label='タグ', choices=TAGS, required=False, widget=forms.CheckboxSelectMultiple)
