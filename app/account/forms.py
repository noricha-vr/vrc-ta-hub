from django import forms
from django.contrib.auth.forms import UserCreationForm
from .models import CustomUser
from community.models import Community


class CustomUserCreationForm(UserCreationForm):
    community_name = forms.CharField(max_length=100, label='集会名')
    community_description = forms.CharField(widget=forms.Textarea, label='イベント紹介')

    class Meta(UserCreationForm.Meta):
        model = CustomUser
        fields = ('user_name', 'email', 'password1', 'password2', 'community_name', 'community_description')

    def save(self, commit=True):
        user = super().save(commit=False)
        community_name = self.cleaned_data.get('community_name')
        community_description = self.cleaned_data.get('community_description')
        if commit:
            user.save()
            Community.objects.create(name=community_name, description=community_description, user=user)
        return user


from django import forms
from .models import CustomUser


class CustomUserChangeForm(forms.ModelForm):
    class Meta:
        model = CustomUser
        fields = ('user_name',)
