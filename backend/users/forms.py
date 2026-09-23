from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

class ForcedPasswordChangeForm(forms.Form):
    new_password1 = forms.CharField(
        label="New Password",
        widget=forms.PasswordInput(attrs={
            'autocomplete': 'new-password',
            'placeholder': 'Enter new password',
            'required': True,
            'id': 'new_password1'
        }),
        strip=False,
        help_text="Your password must contain at least 8 characters, combining uppercase and lowercase letters, numbers, and special characters."
    )
    new_password2 = forms.CharField(
        label="Confirm New Password",
        widget=forms.PasswordInput(attrs={
            'autocomplete': 'new-password',
            'placeholder': 'Confirm new password',
            'required': True,
            'id': 'new_password2'
        }),
        strip=False,
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_new_password1(self):
        password = self.cleaned_data.get('new_password1')
        if password:
            validate_password(password, self.user)
        return password

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get('new_password1')
        p2 = cleaned_data.get('new_password2')
        if p1 and p2 and p1 != p2:
            self.add_error('new_password2', ValidationError("The two password fields did not match."))
        return cleaned_data

    def save(self, commit=True):
        password = self.cleaned_data['new_password1']
        self.user.set_password(password)
        self.user.must_change_password = False
        if commit:
            self.user.save()
        return self.user
