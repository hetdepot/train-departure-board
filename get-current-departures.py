import csv
from os import path, environ
from datetime import datetime, timedelta
import pytz
import requests
from google.transit import gtfs_realtime_pb2
import json
import math

def read_csv_file(path):
  with open(path) as f:
    reader = csv.DictReader(f)
    lines = [l for l in reader]
  return lines


class ScheduleTool():
  CURDIR = path.dirname(__file__)
  DATA_DIR = path.join(CURDIR, 'rawdata')
  PAGES_DIR = path.join(CURDIR, 'pages')
  DATE_FORMAT = '%Y%m%d'
  TIME_FORMAT = '%H:%M:%S'
  UTC = pytz.timezone('UTC')
  TZ = pytz.timezone('Europe/Brussels')

  def get_current_time(self):
    now = self.UTC.localize(datetime.utcnow())
    local_time = now.astimezone(self.TZ)
    return local_time

  def read_stop_times_by_trip_id(self):
    stop_times_list = read_csv_file(path.join(self.DATA_DIR, 'stop_times.txt'))
    stop_times = {}
    for entry in stop_times_list:
      if 'trip_id' not in entry:
        print('ERRRRR: Could not read trip_id:')
        print(json.dumps(entry, indent=2))
        continue
      if entry['trip_id'] not in stop_times:
        stop_times[entry['trip_id']] = []
      trip_id = entry['trip_id']
      del entry['trip_id']
      stop_times[trip_id].append(entry)

    for k, v in stop_times.items():
      v.sort(key=lambda x: int(x['stop_sequence']))

    return stop_times

  def read_trips_by_trip_id(self):
    trips_list = read_csv_file(path.join(self.DATA_DIR, 'trips.txt'))
    trips = {}
    for trip in trips_list:
      k = trip['trip_id']
      del trip['trip_id']
      trips[k] = trip
    return trips

  def read_calendar_dates_by_service_id(self):
    calendar_dates_list = read_csv_file(path.join(self.DATA_DIR, 'calendar_dates.txt'))
    calendar_dates = {}
    for entry in calendar_dates_list:
      if entry['service_id'] not in calendar_dates:
        calendar_dates[entry['service_id']] = set()
      calendar_dates[entry['service_id']].add(entry['date'])
    return calendar_dates

  def read_stop_translations(self):
    stop_translations_list = list(filter(lambda x: x['table_name'] == 'stops' and x['field_name'] == 'stop_name', read_csv_file(path.join(self.DATA_DIR, 'translations.txt'))))
    stop_translations = {}
    for entry in stop_translations_list:
      k = entry['field_value']
      if k not in stop_translations:
        stop_translations[k] = {}
      stop_translations[k][entry['language']] = entry['translation']
    return stop_translations

  def get_datetime_from_string(self, date_string, time_string):
    fixed_time_string = self.fix_24h_time(time_string)
    time = datetime.strptime(date_string + ' ' + fixed_time_string, self.DATE_FORMAT + ' ' + self.TIME_FORMAT)
    if time_string != fixed_time_string:
      time += timedelta(days=1)
    return self.TZ.localize(time)


  def fix_24h_time(self, time_string):
    if int(time_string[:2]) >= 24:
      hour = str(int(time_string[:2]) - 24)
      return hour.zfill(2) + time_string[2:]


  def get_departures_for_stop_and_date(self, stop, date):
    result = []
    for k, v in self.stop_times.items():
      available_stops = list(filter(lambda x: int(x.get('pickup_type', 0)) == 0 or int(x.get('drop_off_type', 0)) == 0, v))
      possible_departures = list(filter(lambda x: x['stop_id'] == stop and self.trip_runs_on_date(k, date) and int(x.get('pickup_type', 0)) == 0, available_stops[:-1]))
      if len(possible_departures) > 0:
        entry = possible_departures[0]
        entry['trip_id'] = k
        entry['trip_headsign_nl'] = self.translate_stop(self.trips[k]['trip_headsign'], 'nl')
        entry['departure_datetime'] = self.get_datetime_from_string(date, entry['departure_time'])
        result.append(entry)
      elif len(possible_departures) > 1:
        print('SAME STOP MULTIPLE TIMES ON SINGLE ROUTE!', stop)
    result.sort(key=lambda x: x['departure_time'])
    return result

  def get_service_id_from_trip_id(self, trip_id):
    return self.trips[trip_id]['service_id']

  def trip_runs_on_date(self, trip_id, date):
    service_id = self.get_service_id_from_trip_id(trip_id)
    return date in self.calendar_dates[service_id]

  def translate_stop(self, stop_name, language):
    if language not in {'fr', 'nl', 'de', 'en'}:
      print('ERRRRR: Requested incorrect language.')
      return None
    if stop_name not in self.stop_translations:
      print('ERRRRR: No translation found for stop:', stop_name)
      return None
    return self.stop_translations[stop_name][language]

  def get_upcoming_departures(self, stop, max_results=0):
    now = self.get_current_time()
    cur_date = now.strftime(self.DATE_FORMAT)
    possible_departures = self.get_departures_for_stop_and_date(stop, cur_date)
    departures = list(filter(lambda x: x['departure_datetime'] >= now - timedelta(hours=1), possible_departures))  # check 1 hour back to also include delayed trains
    departures_with_delays = self.get_current_delays_for_departures(stop, departures)
    departures_with_delays = list(filter(lambda x: x['departure_datetime'] + timedelta(seconds=x['delay']) >= now, departures_with_delays))  # filter to only display trains that will still depart
    if max_results and max_results > 1:
      departures_with_delays = departures_with_delays[:max_results]
    return departures_with_delays

  def get_current_delays_for_departures(self, stop, departures):
    resp = requests.get(environ.get('REAL_TIME_UPDATES_URL'))
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(resp.content)
    for departure in departures:
      departure['delay'] = 0

    for entity in feed.entity:
      if not entity.HasField('trip_update'):  # or not entity.trip_update.HasField('stop_time_update'):
        continue
      for stop_time_update in entity.trip_update.stop_time_update:
        if not stop_time_update.HasField('departure') or not stop_time_update.departure.HasField('delay') or stop != stop_time_update.stop_id:
          continue
        if stop_time_update.departure.delay >= 0:
          # print("Found one with delay ", stop_time_update.departure.delay, ' at ', stop_time_update.stop_id, ' with id= ', entity.id)
          for departure in departures:
            if departure['trip_id'] != entity.id:
              continue
            departure['delay'] = stop_time_update.departure.delay
            break

    return departures

  def get_html_from_entry(self, entry):
    time = self.fix_24h_time(':'.join(entry['departure_time'].split(':')[:2]))
    delay = ''
    if entry['delay'] > 0:
      delay = '+{mins}min'.format(mins=math.floor(entry['delay']/60))
    return '''
        <div class="entry">
          <div class="time-info">
            <div class="time">{time}<span class="delay">{delay}</span></div>
          </div>
          <p class="destination">{destination}</p>
        </div>
        '''.format(time=time, destination=entry['trip_headsign_nl'], delay=delay)

  def generate_html(self, stop):
    with open(path.join(self.CURDIR, 'template.html')) as f:
      template = f.read()
    entries = self.get_upcoming_departures(stop, max_results=10)
    mid = math.ceil(len(entries)/2)
    out = template.replace('__ENTRIES_ROW_1__', ''.join(map(self.get_html_from_entry, entries[:mid]))) \
          .replace('__ENTRIES_ROW_2__', ''.join(map(self.get_html_from_entry, entries[mid:])))
    with open(path.join(self.PAGES_DIR, f'{stop}.html'), 'w') as f:
      f.write(out)


  def __init__(self):
    self.routes = read_csv_file(path.join(self.DATA_DIR, 'routes.txt'))
    self.stop_times = self.read_stop_times_by_trip_id()
    self.trips = self.read_trips_by_trip_id()
    self.calendar_dates = self.read_calendar_dates_by_service_id()
    self.stop_translations = self.read_stop_translations()


st = ScheduleTool()
# Leuven: 8833001
st.generate_html('8833001')
