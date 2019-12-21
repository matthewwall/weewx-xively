# $Id: xively.py 1631 2016-12-24 03:11:52Z mwall $
# Copyright 2013 Matthew Wall
"""
Upload data to Xively (aka COSM, aka Pachube)
  https://xively.com/

[StdRESTful]
    [[Xively]]
        token = TOKEN
        feed = FEED_ID
"""

import Queue
import sys
import syslog
import time
import urllib
import urllib2

try:
    import cjson as json
    setattr(json, 'dumps', json.encode)
    setattr(json, 'loads', json.decode)
except (ImportError, AttributeError):
    try:
        import simplejson as json
    except ImportError:
        import json

import weewx
import weewx.restx
from weeutil.weeutil import to_bool, accumulateLeaves

VERSION = "X"

if weewx.__version__ < "3":
    raise weewx.UnsupportedFeature("weewx 3 is required, found %s" %
                                   weewx.__version__)

def logmsg(level, msg):
    syslog.syslog(level, 'restx: Xively: %s' % msg)

def logdbg(msg):
    logmsg(syslog.LOG_DEBUG, msg)

def loginf(msg):
    logmsg(syslog.LOG_INFO, msg)

def logerr(msg):
    logmsg(syslog.LOG_ERR, msg)

def _compat(d, old_label, new_label):
    if old_label in d and not new_label in d:
        d.setdefault(new_label, d[old_label])
        d.pop(old_label)

# some unit labels are rather lengthy.  this reduces them to something shorter.
UNIT_REDUCTIONS = {
    'degree_F': 'F',
    'degree_C': 'C',
    'inch': 'in',
    'mile_per_hour': 'mph',
    'mile_per_hour2': 'mph',
    'km_per_hour': 'kph',
    'km_per_hour2': 'kph',
    'meter_per_second': 'mps',
    'meter_per_second2': 'mps',
    'degree_compass': None,
    'watt_per_meter_squared': 'Wpm2',
    'uv_index': None,
    'percent': None,
    'unix_epoch': None,
    }

# return the units label for an observation
def _get_units_label(obs, unit_system):
    (unit_type, _) = weewx.units.getStandardUnitType(unit_system, obs)
    return UNIT_REDUCTIONS.get(unit_type, unit_type)

# get the template for an observation based on the observation key
def _get_template(obs_key, overrides, append_units_label, unit_system):
    tmpl_dict = dict()
    if append_units_label:
        label = _get_units_label(obs_key, unit_system)
        if label is not None:
            tmpl_dict['name'] = "%s_%s" % (obs_key, label)
    for x in ['name', 'format', 'units']:
        if x in overrides:
            tmpl_dict[x] = overrides[x]
    return tmpl_dict


class Xively(weewx.restx.StdRESTbase):
    def __init__(self, engine, config_dict):
        """This service recognizes standard restful options plus the following:

        Required parameters:

        token: unique token

        feed: the feed name

        Optional parameters:

        prefix: if specified it will be prepended to data names
        Default is None

        append_units_label: should units label be appended to name
        Default is True

        obs_to_upload: Which observations to upload.  Possible values are
        none or all.  When none is specified, only items in the channels list
        will be uploaded.  When all is specified, all observations will be
        uploaded, subject to overrides in the channels list.
        Default is all

        channels: dictionary of weewx observation names with optional upload
        name, format, and units
        Default is None
        """
        super(Xively, self).__init__(engine, config_dict)
        loginf('service version is %s' % VERSION)
        try:
            site_dict = config_dict['StdRESTful']['Xively']
            site_dict = accumulateLeaves(site_dict, max_level=1)
            site_dict['feed']
            site_dict['token']
        except KeyError, e:
            logerr("Data will not be posted: Missing option %s" % e)
            return

        # for backward compatibility: 'station' is now 'prefix'
        _compat(site_dict, 'station', 'prefix')

        site_dict.setdefault('append_units_label', True)
        site_dict.setdefault('augment_record', True)
        site_dict.setdefault('obs_to_upload', 'all')
        site_dict['append_units_label'] = to_bool(site_dict.get('append_units_label'))
        site_dict['augment_record'] = to_bool(site_dict.get('augment_record'))

        usn = site_dict.get('unit_system', None)
        if usn is not None:
            site_dict['unit_system'] = weewx.units.unit_constants[usn]

        if 'channels' in config_dict['StdRESTful']['Xively']:
            site_dict['channels'] = dict(config_dict['StdRESTful']['Xively']['channels'])

        # if we are supposed to augment the record with data from weather
        # tables, then get the manager dict to do it.  there may be no weather
        # tables, so be prepared to fail.
        try:
            if site_dict.get('augment_record'):
                _manager_dict = weewx.manager.get_manager_dict_from_config(
                    config_dict, 'wx_binding')
                site_dict['manager_dict'] = _manager_dict
        except weewx.UnknownBinding:
            pass

        self.archive_queue = Queue.Queue()
        self.archive_thread = XivelyThread(self.archive_queue, **site_dict)
        self.archive_thread.start()
        self.bind(weewx.NEW_ARCHIVE_RECORD, self.new_archive_record)

        if usn is not None:
            loginf("desired unit system is %s" % usn)
        loginf("Data will be uploaded to feed %s" % site_dict['feed'])

    def new_archive_record(self, event):
        self.archive_queue.put(event.record)

class XivelyThread(weewx.restx.RESTThread):

    _SERVER_URL = 'http://api.xively.com/v2/feeds'

    def __init__(self, queue, feed, token,
                 prefix=None, unit_system=None, augment_record=True,
                 channels={}, obs_to_upload='all', append_units_label=True,
                 server_url=_SERVER_URL, skip_upload=False,
                 manager_dict=None,
                 post_interval=None, max_backlog=sys.maxint, stale=None,
                 log_success=True, log_failure=True,
                 timeout=60, max_tries=3, retry_wait=5):
        super(XivelyThread, self).__init__(queue,
                                           protocol_name='Xively',
                                           manager_dict=manager_dict,
                                           post_interval=post_interval,
                                           max_backlog=max_backlog,
                                           stale=stale,
                                           log_success=log_success,
                                           log_failure=log_failure,
                                           max_tries=max_tries,
                                           timeout=timeout,
                                           retry_wait=retry_wait)
        self.feed = feed
        self.token = token
        self.prefix = prefix
        self.upload_all = True if obs_to_upload.lower() == 'all' else False
        self.append_units_label = append_units_label
        self.channels = channels
        self.server_url = server_url
        self.skip_upload = to_bool(skip_upload)
        self.unit_system = unit_system
        self.augment_record = augment_record
        self.templates = dict()

    def process_record(self, record, dbm):
        if self.augment_record and dbm:
            record = self.get_record(record, dbm)
        if self.unit_system is not None:
            record = weewx.units.to_std_system(record, self.unit_system)
        data = self.get_data(record)
        if self.skip_upload:
            loginf("skipping upload")
            return
        url = self.get_url()
        req = urllib2.Request(url, data)
        req.add_header("User-Agent", "weewx/%s" % weewx.__version__)
        req.add_header("X-PachubeApiKey", self.token)
        req.get_method = lambda: 'PUT'
        self.post_with_retries(req)

    def check_response(self, response):
        txt = response.read()
        if txt != '':
            raise weewx.restx.FailedPost(txt)

    def get_url(self):
        url = '%s/%s' % (self.server_url, self.feed)
        logdbg('url: %s' % url)
        return url
        
    def get_data(self, record):
        # if uploading everything, we must check the upload variables list
        # every time since variables may come and go in a record.  use the
        # channels to override any generic template generation.
        if self.upload_all:
            for f in record:
                if f not in self.templates:
                    self.templates[f] = _get_template(f,
                                                      self.channels.get(f, {}),
                                                      self.append_units_label,
                                                      record['usUnits'])

        # otherwise, create the list of upload variables once, based on the
        # user-specified list of channels.
        elif not self.templates:
            for f in self.channels:
                self.templates[f] = _get_template(f, self.channels[f],
                                                  self.append_units_label,
                                                  record['usUnits'])

        prefix = urllib.quote_plus(self.prefix) \
            if self.prefix is not None else None
        tstr = time.strftime('%Y-%m-%dT%H:%M:%SZ',
                             time.gmtime(record['dateTime']))
        streams = {}
        for k in self.templates:
            v = record.get(k)
            if v is not None:
                name = self.templates[k].get('name', k)
                dskey = '%s_%s' % (prefix, name) if prefix is not None else name
                to_units = self.templates[k].get('units')
                if to_units is not None:
                    (from_unit, from_group) = weewx.units.getStandardUnitType(
                        record['usUnits'], k)
                    from_t = (v, from_unit, from_group)
                    v = weewx.units.convert(from_t, to_units)[0]
                if not dskey in streams:
                    streams[dskey] = {'id':dskey, 'datapoints':[]}

                dp = {'at':tstr, 'value':v}
                streams[dskey]['datapoints'].append(dp)
        if len(streams.keys()) == 0:
            return None
        data = {
            'version':'1.0.0',
            'datastreams':[]
            }
        for k in streams.keys():
            data['datastreams'].append(streams[k])
        data = json.dumps(data)
        logdbg('data: %s' % data)
        return data
