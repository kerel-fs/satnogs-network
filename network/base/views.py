import urllib2
import ephem
import math
from operator import itemgetter
from datetime import datetime, timedelta
from StringIO import StringIO

from django.conf import settings
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404, render, redirect
from django.core.urlresolvers import reverse
from django.utils.timezone import now, make_aware, utc
from django.utils.text import slugify
from django.http import JsonResponse, HttpResponseNotFound, HttpResponseServerError, HttpResponse
from django.contrib.auth.decorators import login_required
from django.core.management import call_command

from rest_framework import serializers, viewsets

from network.base.models import (Station, Transmitter, Observation,
                                 Data, Satellite, Antenna, Rig)
from network.base.forms import StationForm
from network.base.decorators import admin_required


class StationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Station
        fields = ('name', 'lat', 'lng')


class StationAllView(viewsets.ReadOnlyModelViewSet):
    queryset = Station.objects.all()
    serializer_class = StationSerializer


def _resolve_overlaps(station, start, end):
    data = Data.objects.filter(ground_station=station)

    if data:
        for datum in data:
            if datum.is_past:
                continue
            if datum.start <= end and start <= datum.end:
                if datum.start <= start and datum.end >= end:
                    return False
                if start < datum.start and end > datum.end:
                    start1 = start
                    end1 = datum.start
                    start2 = datum.end
                    end2 = end
                    return start1, end1, start2, end2
                if datum.start <= start:
                    start = datum.end
                if datum.end >= end:
                    end = datum.start
    return start, end


def index(request):
    """View to render index page."""
    observations = Observation.objects.all()
    try:
        featured_station = Station.objects.filter(active=True).latest('featured_date')
    except Station.DoesNotExist:
        featured_station = None

    ctx = {
        'latest_observations': observations.filter(end__lt=now()),
        'scheduled_observations': observations.filter(end__gte=now()),
        'featured_station': featured_station,
        'mapbox_id': settings.MAPBOX_MAP_ID,
        'mapbox_token': settings.MAPBOX_TOKEN
    }

    return render(request, 'base/home.html', ctx)


def custom_404(request):
    """Custom 404 error handler."""
    return HttpResponseNotFound(render(request, '404.html'))


def custom_500(request):
    """Custom 500 error handler."""
    return HttpResponseServerError(render(request, '500.html'))


def robots(request):
    data = render(request, 'robots.txt', {'environment': settings.ENVIRONMENT})
    response = HttpResponse(data,
                            content_type='text/plain; charset=utf-8')
    return response


@admin_required
def settings_site(request):
    """View to render settings page."""
    if request.method == 'POST':
        if request.POST['fetch']:
            try:
                out = StringIO()
                call_command('fetch_data', stdout=out)
                request.session['fetch_out'] = out.getvalue()
            except:
                messages.error(request, 'fetch command failed.')
        return redirect(reverse('base:settings_site'))

    fetch_out = request.session.get('fetch_out', False)
    if fetch_out:
        del request.session['fetch_out']
        return render(request, 'base/settings_site.html', {'fetch_data': fetch_out})
    return render(request, 'base/settings_site.html')


def observations_list(request):
    """View to render Observations page."""
    observations = Observation.objects.all()

    return render(request, 'base/observations.html', {'observations': observations})


@login_required
def observation_new(request):
    """View for new observation"""
    me = request.user
    if request.method == 'POST':
        sat_id = request.POST.get('satellite')
        trans_id = request.POST.get('transmitter')
        start_time = datetime.strptime(request.POST.get('start-time'), '%Y-%m-%d %H:%M')
        start = make_aware(start_time, utc)
        end_time = datetime.strptime(request.POST.get('end-time'), '%Y-%m-%d %H:%M')
        end = make_aware(end_time, utc)
        sat = Satellite.objects.get(norad_cat_id=sat_id)
        trans = Transmitter.objects.get(id=trans_id)
        obs = Observation(satellite=sat, transmitter=trans,
                          author=me, start=start, end=end)
        obs.save()

        total = int(request.POST.get('total'))

        for item in range(total):
            start = datetime.strptime(
                request.POST.get('{0}-starting_time'.format(item)), '%Y-%m-%d %H:%M:%S.%f'
            )
            end = datetime.strptime(
                request.POST.get('{}-ending_time'.format(item)), '%Y-%m-%d %H:%M:%S.%f'
            )
            station_id = request.POST.get('{}-station'.format(item))
            ground_station = Station.objects.get(id=station_id)
            Data.objects.create(start=make_aware(start, utc), end=make_aware(end, utc),
                                ground_station=ground_station, observation=obs)

        return redirect(reverse('base:observation_view', kwargs={'id': obs.id}))

    satellites = Satellite.objects.filter(transmitters__alive=True).distinct()
    transmitters = Transmitter.objects.filter(alive=True)

    return render(request, 'base/observation_new.html',
                  {'satellites': satellites,
                   'transmitters': transmitters,
                   'date_min_start': settings.DATE_MIN_START,
                   'date_max_range': settings.DATE_MAX_RANGE})


def prediction_windows(request, sat_id, start_date, end_date):
    try:
        sat = Satellite.objects.filter(transmitters__alive=True). \
            distinct().get(norad_cat_id=sat_id)
    except:
        data = {
            'error': 'You should select a Satellite first.'
        }
        return JsonResponse(data, safe=False)
    satellite = ephem.readtle(str(sat.tle0), str(sat.tle1), str(sat.tle2))

    end_date = datetime.strptime(end_date, '%Y-%m-%d %H:%M')

    data = []

    stations = Station.objects.all()
    for station in stations:
        if not station.online:
            continue
        observer = ephem.Observer()
        observer.lon = str(station.lng)
        observer.lat = str(station.lat)
        observer.elevation = station.alt
        observer.date = str(start_date)
        station_match = False
        keep_digging = True
        while keep_digging:
            try:
                tr, azr, tt, altt, ts, azs = observer.next_pass(satellite)
            except ValueError:
                data = {
                    'error': 'That satellite seems to stay always below your horizon.'
                }
                break

            if ephem.Date(tr).datetime() < end_date:
                if ephem.Date(ts).datetime() > end_date:
                    ts = end_date
                    keep_digging = False
                else:
                    time_start_new = ephem.Date(ts).datetime() + timedelta(minutes=1)
                    observer.date = time_start_new.strftime("%Y-%m-%d %H:%M:%S.%f")

                # Adjust or discard window if overlaps exist
                window_start = make_aware(ephem.Date(tr).datetime(), utc)
                window_end = make_aware(ephem.Date(ts).datetime(), utc)
                window = _resolve_overlaps(station, window_start, window_end)
                if window:
                    if not station_match:
                        station_windows = {
                            'id': station.id,
                            'name': station.name,
                            'window': []
                        }
                        station_match = True
                    window_start = window[0]
                    window_end = window[1]
                    station_windows['window'].append(
                        {
                            'start': window_start.strftime("%Y-%m-%d %H:%M:%S.%f"),
                            'end': window_end.strftime("%Y-%m-%d %H:%M:%S.%f"),
                            'az_start': azr
                        })
                    # In case our window was split in two
                    try:
                        window_start = window[2]
                        window_end = window[3]
                        station_windows['window'].append(
                            {
                                'start': window_start.strftime("%Y-%m-%d %H:%M:%S.%f"),
                                'end': window_end.strftime("%Y-%m-%d %H:%M:%S.%f"),
                                'az_start': azr
                            })
                    except:
                        pass

            else:
                # window start outside of window bounds
                break

        if station_match:
            data.append(station_windows)

    return JsonResponse(data, safe=False)


def observation_view(request, id):
    """View for single observation page."""
    observation = get_object_or_404(Observation, id=id)
    data = Data.objects.filter(observation=observation)

    if settings.ENVIRONMENT == 'production':
        discuss_slug = 'https://community.satnogs.org/t/observation-{0}-{1}-{2}' \
            .format(observation.id, slugify(observation.satellite.name),
                    observation.satellite.norad_cat_id)
        discuss_url = ('https://community.satnogs.org/new-topic?title=Observation {0}: {1} ({2})'
                       '&body=Regarding [Observation {3}](http://{4}{5}) ...&category=observations') \
            .format(observation.id, observation.satellite.name,
                    observation.satellite.norad_cat_id, observation.id,
                    request.get_host(), request.path)
        try:
            apiurl = '{0}.json'.format(discuss_slug)
            urllib2.urlopen(apiurl).read()
            has_comments = True
        except:
            has_comments = False

        return render(request, 'base/observation_view.html',
                      {'observation': observation, 'data': data, 'has_comments': has_comments,
                       'discuss_url': discuss_url, 'discuss_slug': discuss_slug})

    return render(request, 'base/observation_view.html',
                  {'observation': observation, 'data': data})


@login_required
def observation_delete(request, id):
    """View for deleting observation."""
    me = request.user
    observation = get_object_or_404(Observation, id=id)
    if observation.author == me and observation.is_deletable:
        observation.delete()
        messages.success(request, 'Observation deleted successfully.')
    else:
        messages.error(request, 'Permission denied.')
    return redirect(reverse('base:observations_list'))


def stations_list(request):
    """View to render Stations page."""
    stations = Station.objects.all()
    form = StationForm()
    antennas = Antenna.objects.all()

    return render(request, 'base/stations.html',
                  {'stations': stations, 'form': form, 'antennas': antennas})


def station_view(request, id):
    """View for single station page."""
    station = get_object_or_404(Station, id=id)
    form = StationForm(instance=station)
    antennas = Antenna.objects.all()
    rigs = Rig.objects.all()

    try:
        satellites = Satellite.objects.filter(transmitters__alive=True).distinct()
    except:
        pass  # we won't have any next passes to display

    # Load the station information and invoke ephem so we can
    # calculate upcoming passes for the station
    observer = ephem.Observer()
    observer.lon = str(station.lng)
    observer.lat = str(station.lat)
    observer.elevation = station.alt

    nextpasses = []
    passid = 0

    for satellite in satellites:
        observer.date = ephem.date(datetime.today())

        try:
            sat_ephem = ephem.readtle(str(satellite.tle0),
                                      str(satellite.tle1),
                                      str(satellite.tle2))

            # Here we are going to iterate over each satellite to
            # find its appropriate passes within a given time constraint
            keep_digging = True
            while keep_digging:
                try:
                    tr, azr, tt, altt, ts, azs = observer.next_pass(sat_ephem)

                    if tr is None:
                        break

                    # using the angles module convert the sexagesimal degree into
                    # something more easily read by a human
                    elevation = format(math.degrees(altt), '.0f')
                    azimuth = format(math.degrees(azr), '.0f')
                    passid += 1

                    # show only if >= 10 degrees and in next 6 hours
                    if tr < ephem.date(datetime.today() + timedelta(hours=6)):
                        if float(elevation) >= 10:
                            sat_pass = {'passid': passid,
                                        'mytime': str(observer.date),
                                        'debug': observer.next_pass(sat_ephem),
                                        'name': str(satellite.name),
                                        'id': str(satellite.id),
                                        'tr': tr,           # Rise time
                                        'azr': azimuth,     # Rise Azimuth
                                        'tt': tt,           # Max altitude time
                                        'altt': elevation,  # Max altitude
                                        'ts': ts,           # Set time
                                        'azs': azs}         # Set azimuth
                            nextpasses.append(sat_pass)
                        observer.date = ephem.Date(ts).datetime() + timedelta(minutes=1)
                        continue
                    else:
                        keep_digging = False
                    continue
                except ValueError:
                    break  # there will be sats in our list that fall below horizon, skip
                except TypeError:
                    break  # if there happens to be a non-EarthSatellite object in the list
                except Exception:
                    break
        except ValueError:
            pass  # TODO: if something does not have a proper TLE line we need to know/fix

    return render(request, 'base/station_view.html',
                  {'station': station, 'form': form, 'antennas': antennas,
                   'mapbox_id': settings.MAPBOX_MAP_ID,
                   'mapbox_token': settings.MAPBOX_TOKEN,
                   'nextpasses': sorted(nextpasses, key=itemgetter('tr')),
                   'rigs': rigs})


@require_POST
def station_edit(request):
    """Edit or add a single station."""
    if request.POST['id']:
        pk = request.POST.get('id')
        station = get_object_or_404(Station, id=pk, owner=request.user)
        form = StationForm(request.POST, request.FILES, instance=station)
    else:
        form = StationForm(request.POST, request.FILES)
    if form.is_valid():
        f = form.save(commit=False)
        f.owner = request.user
        f.save()
        form.save_m2m()
        if f.online:
            messages.success(request, 'Successfully saved Ground Station.')
        else:
            messages.success(request, ('Successfully saved Ground Station. It will appear online '
                                       'as soon as it connects with our API.'))

        return redirect(reverse('base:station_view', kwargs={'id': f.id}))
    else:
        messages.error(request, 'Some fields missing on the form')
        return redirect(reverse('users:view_user', kwargs={'username': request.user.username}))


@login_required
def station_delete(request, id):
    """View for deleting a station."""
    me = request.user
    station = get_object_or_404(Station, id=id, owner=request.user)
    station.delete()
    messages.success(request, 'Ground Station deleted successfully.')
    return redirect(reverse('users:view_user', kwargs={'username': me}))
