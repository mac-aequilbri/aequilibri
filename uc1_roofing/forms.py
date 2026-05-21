from django import forms
from .models import Quote, Contact, QuoteItem, RateCard, PITCH_CHOICES, MATERIAL_CHOICES


class ContactForm(forms.ModelForm):
    class Meta:
        model = Contact
        fields = ['name', 'email', 'phone', 'company', 'address']
        widgets = {
            'address': forms.Textarea(attrs={'rows': 2}),
        }


class QuoteForm(forms.ModelForm):
    # Contact fields inline
    client_name    = forms.CharField(max_length=200, label='Client Name')
    client_email   = forms.EmailField(required=False, label='Client Email')
    client_phone   = forms.CharField(max_length=30, required=False, label='Phone')
    client_company = forms.CharField(max_length=200, required=False, label='Company')

    class Meta:
        model = Quote
        fields = ['property_address', 'flat_area_sqm', 'pitch_type',
                  'material', 'notes']
        widgets = {
            'property_address': forms.Textarea(attrs={'rows': 2}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }
        labels = {
            'flat_area_sqm': 'Flat Roof Area (m²)',
            'waste_factor_pct': 'Waste Factor (%)',
        }


class QuoteItemForm(forms.ModelForm):
    class Meta:
        model = QuoteItem
        fields = ['description', 'quantity', 'unit', 'unit_price_ex_gst']
        labels = {
            'unit_price_ex_gst': 'Unit Price (ex GST)',
        }


class RateCardForm(forms.ModelForm):
    class Meta:
        model = RateCard
        fields = ['material', 'pitch_type', 'description', 'unit', 'rate_ex_gst', 'is_active']
        labels = {
            'rate_ex_gst': 'Rate (ex GST, AUD)',
        }
