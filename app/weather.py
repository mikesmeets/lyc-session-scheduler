"""
weather.py — Open-Meteo weather helper for LYC Jr Sailing

No API key required. Results are cached in memory per process.
At most two API calls per page load: one forecast batch (future sessions)
and one archive batch (past sessions, contiguous date range).
"""
import json
import re
import time
import urllib.request
import urllib.parse
from datetime import date, datetime

# ── Location ──────────────────────────────────────────────────────────────────
LAT      = 40.9281       # Larchmont, NY
LON      = -73.7523
TIMEZONE = 'America/New_York'

# ── Cache ─────────────────────────────────────────────────────────────────────
_cache: dict = {}         # key -> (fetched_at, data)
FORECAST_TTL  = 1800      # 30 min — forecasts update frequently
HISTORICAL_TTL = 86400    # 24 h  — past data never changes

# ── WMO weather codes ─────────────────────────────────────────────────────────
# Maps WMO code -> (short description, Bootstrap Icons class)
_WMO: dict = {
    0:  ('Clear',          'bi-sun'),
    1:  ('Mainly clear',   'bi-sun'),
    2:  ('Partly cloudy',  'bi-cloud-sun'),
    3:  ('Overcast',       'bi-clouds'),
    45: ('Fog',            'bi-cloud'),
    48: ('Icy fog',        'bi-cloud'),
    51: ('Light drizzle',  'bi-cloud-drizzle'),
    53: ('Drizzle',        'bi-cloud-drizzle'),
    55: ('Heavy drizzle',  'bi-cloud-drizzle'),
    56: ('Frz drizzle',    'bi-cloud-drizzle'),
    57: ('Frz drizzle',    'bi-cloud-drizzle'),
    61: ('Light rain',     'bi-cloud-rain'),
    63: ('Rain',           'bi-cloud-rain'),
    65: ('Heavy rain',     'bi-cloud-rain-heavy'),
    66: ('Frz rain',       'bi-cloud-rain'),
    67: ('Heavy frz rain', 'bi-cloud-rain-heavy'),
    71: ('Light snow',     'bi-snow'),
    73: ('Snow',           'bi-snow'),
    75: ('Heavy snow',     'bi-snow2'),
    77: ('Snow grains',    'bi-snow'),
    80: ('Showers',        'bi-cloud-rain'),
    81: ('Showers',        'bi-cloud-rain'),
    82: ('Heavy showers',  'bi-cloud-rain-heavy'),
    85: ('Snow showers',   'bi-snow'),
    86: ('Heavy snow showers', 'bi-snow2'),
    95: ('Thunderstorm',   'bi-cloud-lightning-rain'),
    96: ('Thunderstorm',   'bi-cloud-lightning-rain'),
    99: ('Thunderstorm',   'bi-cloud-lightning-rain'),
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_hour(time_str) -> int:
    """Return integer hour (0-23) from a time string like '9:00 AM', '14:00', '9:30'."""
    if not time_str:
        return 10
    s = str(time_str).strip()
    for fmt in ('%I:%M %p', '%I:%M%p', '%H:%M', '%I %p', '%I%p'):
        try:
            return datetime.strptime(s.upper(), fmt.upper()).hour
        except ValueError:
            pass
    m = re.match(r'(\d+)', s)
    if m:
        h = int(m.group(1))
        if 'pm' in s.lower() and h != 12:
            h += 12
        elif 'am' in s.lower() and h == 12:
            h = 0
        return h % 24
    return 10


def _wind_dir(deg) -> str:
    if deg is None:
        return ''
    dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
            'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    return dirs[round(float(deg) / 22.5) % 16]


def _http_get(url: str, params: dict):
    full = url + '?' + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(full, timeout=6) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


# ── API fetchers (cached) ──────────────────────────────────────────────────────

def _fetch_forecast(days: int = 16):
    key = f'forecast_{days}'
    now = time.monotonic()
    if key in _cache and now - _cache[key][0] < FORECAST_TTL:
        return _cache[key][1]
    data = _http_get('https://api.open-meteo.com/v1/forecast', {
        'latitude': LAT, 'longitude': LON,
        'hourly': ('temperature_2m,windspeed_10m,windgusts_10m,'
                   'winddirection_10m,precipitation_probability,weathercode'),
        'wind_speed_unit': 'kn',
        'temperature_unit': 'fahrenheit',
        'timezone': TIMEZONE,
        'forecast_days': days,
    })
    _cache[key] = (now, data)
    return data


def _fetch_archive(start: date, end: date):
    key = f'archive_{start}_{end}'
    now = time.monotonic()
    if key in _cache and now - _cache[key][0] < HISTORICAL_TTL:
        return _cache[key][1]
    data = _http_get('https://archive-api.open-meteo.com/v1/archive', {
        'latitude': LAT, 'longitude': LON,
        'start_date': start.isoformat(),
        'end_date':   end.isoformat(),
        'hourly': ('temperature_2m,windspeed_10m,windgusts_10m,'
                   'winddirection_10m,precipitation,weathercode'),
        'wind_speed_unit': 'kn',
        'temperature_unit': 'fahrenheit',
        'timezone': TIMEZONE,
    })
    _cache[key] = (now, data)
    return data


# ── Extractor ──────────────────────────────────────────────────────────────────

def _extract(hourly: dict, session_date: date, hour: int, is_forecast: bool):
    times = hourly.get('time', [])
    target = f"{session_date.isoformat()}T{hour:02d}:00"
    if target in times:
        idx = times.index(target)
    else:
        prefix = session_date.isoformat()
        candidates = [i for i, t in enumerate(times) if t.startswith(prefix)]
        if not candidates:
            return None
        idx = min(candidates, key=lambda i: abs(int(times[i][11:13]) - hour))

    def v(key):
        lst = hourly.get(key, [])
        val = lst[idx] if idx < len(lst) else None
        return val

    code = int(v('weathercode') or 0)
    desc, icon = _WMO.get(code, ('Unknown', 'bi-cloud'))
    wind_deg   = v('winddirection_10m')
    wind_speed = v('windspeed_10m')
    wind_gust  = v('windgusts_10m')

    result = {
        'wind_speed':   round(wind_speed) if wind_speed is not None else None,
        'wind_gust':    round(wind_gust)  if wind_gust  is not None else None,
        'wind_dir':     _wind_dir(wind_deg),
        'temp':         round(v('temperature_2m')) if v('temperature_2m') is not None else None,
        'description':  desc,
        'icon':         icon,
        'is_forecast':  is_forecast,
    }
    if is_forecast:
        result['precip_prob'] = v('precipitation_probability')
    else:
        prec = v('precipitation')
        result['precip'] = round(prec, 2) if prec is not None else None
    return result


# ── Public API ─────────────────────────────────────────────────────────────────

def get_weather_for_sessions(sessions) -> dict:
    """
    Return {session_id: weather_dict} for a list of Session objects.
    Uses at most 2 HTTP calls: one forecast batch and one archive batch.
    Sessions more than 16 days in the future are skipped (no forecast data).
    """
    today = date.today()
    result: dict = {}

    future_sess = [s for s in sessions
                   if s.date >= today and (s.date - today).days <= 15]
    past_sess   = [s for s in sessions if s.date < today]

    # ── Forecast ──────────────────────────────────────────────────────────────
    if future_sess:
        max_days = max((s.date - today).days for s in future_sess) + 2
        data = _fetch_forecast(days=min(max_days, 16))
        if data and 'hourly' in data:
            for s in future_sess:
                hour = _parse_hour(getattr(s, 'start_time', None))
                w = _extract(data['hourly'], s.date, hour, is_forecast=True)
                if w:
                    result[s.id] = w

    # ── Historical ────────────────────────────────────────────────────────────
    if past_sess:
        min_d = min(s.date for s in past_sess)
        max_d = max(s.date for s in past_sess)
        data = _fetch_archive(min_d, max_d)
        if data and 'hourly' in data:
            for s in past_sess:
                hour = _parse_hour(getattr(s, 'start_time', None))
                w = _extract(data['hourly'], s.date, hour, is_forecast=False)
                if w:
                    result[s.id] = w

    return result
