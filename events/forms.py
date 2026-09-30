# events/forms.py
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

User = get_user_model()


class BootstrapFormMixin:
    """Add Bootstrap's `form-control` class to every widget so the templates render styled inputs."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = field.widget.attrs.get('class', '')
            field.widget.attrs['class'] = f'{css} form-control'.strip()


class SignupForm(BootstrapFormMixin, UserCreationForm):
    """Registration form for tenant users (attendees / job seekers)."""

    first_name = forms.CharField(max_length=150, required=True, label='First name')
    last_name = forms.CharField(max_length=150, required=True, label='Last name')
    email = forms.EmailField(
        required=True,
        help_text='Used for login confirmations and password resets.',
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'first_name', 'last_name', 'email')

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        # Queries run inside the current tenant schema, so uniqueness is per company.
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account with this email address already exists.')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.first_name = self.cleaned_data['first_name'].strip()
        user.last_name = self.cleaned_data['last_name'].strip()
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
        return user


class LoginForm(BootstrapFormMixin, AuthenticationForm):
    """Login form with Bootstrap styling."""

    error_messages = {
        **AuthenticationForm.error_messages,
        'keycloak_only': 'Password sign-in is no longer available for this account. '
                         'Please continue with your Skill.jobs account.',
    }

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        from identity import conf

        if not conf.password_login_allowed_for(user):
            raise forms.ValidationError(self.error_messages['keycloak_only'], code='keycloak_only')
