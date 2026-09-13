from django import forms

from .models import Source
from .scrapers import adapter_for_url


class SourceForm(forms.ModelForm):
    class Meta:
        model = Source
        fields = ["name", "url"]
        widgets = {
            "name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Audi Q8 · Mobile.bg"}
            ),
            "url": forms.URLInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "https://www.mobile.bg/obiavi/avtomobili-dzhipove/audi/q8",
                }
            ),
        }

    def clean_url(self):
        url = self.cleaned_data["url"].strip().rstrip("/")
        try:
            adapter_for_url(url)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc
        return url

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.adapter = adapter_for_url(instance.url)
        if commit:
            instance.save()
        return instance
