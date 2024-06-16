from django import forms
from django.contrib.auth.forms import UserCreationForm, PasswordChangeForm
from .models import CustomUser
from community.models import Community


class CustomUserCreationForm(UserCreationForm):
    community_name = forms.CharField(max_length=100, label='集会名',
                                     widget=forms.TextInput(attrs={'class': 'form-control'}))
    community_description = forms.CharField(widget=forms.Textarea(attrs={'class': 'form-control'}),
                                            label='イベント紹介')

    class Meta(UserCreationForm.Meta):
        model = CustomUser
        fields = ('user_name', 'email', 'password1', 'password2', 'community_name', 'community_description')
        widgets = {
            'user_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'password1': forms.PasswordInput(attrs={'class': 'form-control'}),
            'password2': forms.PasswordInput(attrs={'class': 'form-control'}),
        }

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

from django import forms
from django.contrib.auth.forms import AuthenticationForm


class BootstrapAuthenticationForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})


class BootstrapPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})


class CustomUserChangeForm(forms.ModelForm):
    class Meta:
        model = CustomUser
        fields = ('user_name', 'email')
        widgets = {
            'user_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.TextInput(attrs={'class': 'form-control'}),
        }
