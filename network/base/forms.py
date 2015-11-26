from django import forms

from network.base.models import Station


class StationForm(forms.ModelForm):
    class Meta:
        model = Station
        fields = ['name', 'image', 'alt', 'rig', 'uuid',
                  'lat', 'lng', 'qthlocator', 'antenna', 'active']
        image = forms.ImageField(required=False)
