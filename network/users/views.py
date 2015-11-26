from django.core.urlresolvers import reverse
from django.shortcuts import render
from django.views.generic import RedirectView
from django.views.generic import UpdateView

from braces.views import LoginRequiredMixin

from rest_framework.authtoken.models import Token

from network.users.forms import UserForm
from network.users.models import User
from network.base.forms import StationForm
from network.base.models import Station, Observation, Antenna, Rig


class UserRedirectView(LoginRequiredMixin, RedirectView):
    permanent = False

    def get_redirect_url(self):
        return reverse("users:view_user",
                       kwargs={"username": self.request.user.username})


class UserUpdateView(LoginRequiredMixin, UpdateView):

    form_class = UserForm

    model = User

    def get_success_url(self):
        return reverse("users:view_user",
                       kwargs={"username": self.request.user.username})

    def get_object(self):
        return User.objects.get(username=self.request.user.username)


def view_user(request, username):
    """View for user page."""
    user = User.objects.get(username=username)
    observations = Observation.objects.filter(author=user)[0:10]
    stations = Station.objects.filter(owner=user)
    try:
        token = Token.objects.get(user=user)
    except:
        token = Token.objects.create(user=user)
    form = StationForm()
    antennas = Antenna.objects.all()
    rigs = Rig.objects.all()

    return render(request, 'users/user_detail.html',
                  {'user': user,
                   'observations': observations,
                   'stations': stations,
                   'token': token,
                   'form': form,
                   'antennas': antennas,
                   'rigs': rigs})
