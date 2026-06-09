from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import Repository


class RegisterForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")

    # Bug 11: enforce email uniqueness — previously two accounts could share one email
    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip()
        if email and User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "An account with this email address already exists."
            )
        return email


class RepositoryUploadForm(forms.ModelForm):
    class Meta:
        model = Repository
        fields = ("name", "zip_file")
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "zip_file": forms.FileInput(attrs={"class": "form-control", "accept": ".zip"}),
        }

    def clean_zip_file(self):
        zip_file = self.cleaned_data.get("zip_file")
        if zip_file:
            if not zip_file.name.lower().endswith(".zip"):
                raise forms.ValidationError("Please upload a ZIP file.")
            # File-size guard: 100 MB ceiling. Safe because FILE_UPLOAD_MAX_MEMORY_SIZE=2 MB
            # means the ZIP streams to a temp file on disk — it never sits in RAM.
            # Processing memory is bounded by MAX_FILE_BYTES (500 KB/file), not ZIP size.
            max_bytes = 100 * 1024 * 1024  # 100 MB
            if zip_file.size > max_bytes:
                raise forms.ValidationError(
                    f"ZIP file is too large ({zip_file.size // (1024 * 1024)} MB). "
                    "Maximum allowed size is 100 MB."
                )
        return zip_file


class ChatForm(forms.Form):
    # Bug 15: add max_length to prevent unbounded questions hitting the Groq API
    question = forms.CharField(
        max_length=2000,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Ask about this repository...",
            }
        ),
    )

    # Bug 15: reject blank/whitespace-only questions
    def clean_question(self):
        question = self.cleaned_data.get("question", "").strip()
        if not question:
            raise forms.ValidationError("Please enter a question before submitting.")
        return question
