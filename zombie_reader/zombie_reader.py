import argparse
import datetime
import itertools
import json
import logging
import operator
import os.path
import socket
import sys
import time
import webbrowser

import third_party.web as web

import api_handlers
import base.api
import base.log
import base.middleware
import base.paths
import base.tag_helper

_READER_STATIC_PATH_PREFIX = '/reader/ui/'
_BASE_DIRECTORY = os.path.dirname(os.path.abspath(__file__))
_STATIC_DIRECTORY = os.path.abspath(os.path.join(_BASE_DIRECTORY, 'static'))

urls = (
    '/', 'RedirectToMain',
    '/reader/view', 'RedirectToMain',
    '/reader/view/', 'Main',

    # HTML handlers
    '/reader/overview', 'Overview',
    '/reader/embediframe', 'EmbedIframe',
    '/reader/trends', 'Trends',

    # API handlers
    '/reader/api/0/subscription/list', 'api_handlers.SubscriptionList',
    '/reader/api/0/tag/list', 'api_handlers.TagList',
    '/reader/api/0/recommendation/list', 'api_handlers.RecommendationList',
    '/reader/api/0/preference/list', 'api_handlers.PreferenceList',
    '/reader/api/0/preference/stream/list', 'api_handlers.StreamPreferenceList',
    '/reader/api/0/unread-count', 'api_handlers.UnreadCount',
    '/reader/api/0/stream/contents/(.+)', 'api_handlers.StreamContents',
    '/reader/api/0/stream/items/ids', 'api_handlers.StreamItemsIds',
    '/reader/api/0/stream/items/contents', 'api_handlers.StreamItemsContents',

    # Stubbed-out handlers
    '/reader/directory', 'StubbedOut',
    '/reader/logging', 'StubbedOut',
    '/reader/js-load-error', 'StubbedOut',
    '/reader/api/0/edit-tag', 'StubbedOut',
    '/reader/api/0/preference/stream/set', 'StubbedOut',
    '/reader/api/0/preference/stream/set', 'StubbedOut',
    '/reader/api/0/preference/set', 'StubbedOut',
    '/reader/api/0/token', 'StubbedOut',
)

render = web.template.render(
    os.path.join(_BASE_DIRECTORY, 'templates'),
    globals={
      'js_escape': json.dumps,
    })

class RedirectToMain:
  def GET(self):
    raise web.redirect('/reader/view/')

class Main:
  def GET(self):
    return render.main(user_info=web.config.reader_user_info)

class Overview:
  def GET(self):
    user_id = web.config.reader_user_info.user_id
    stream_items_by_stream_id = web.config.reader_stream_items_by_stream_id

    def state_stream_id(state_tag_name):
      return base.tag_helper.TagHelper(
          user_id).state_tag(state_tag_name).stream_id

    def load_item_entries(state_tag_name, start_index, end_index):
      stream_id = state_stream_id(state_tag_name)
      if not stream_id in stream_items_by_stream_id:
        logging.info('%s %s had no entries', state_tag_name, stream_id)
        return []

      stream_item_ids = stream_items_by_stream_id[stream_id][0]
      item_ids = [
          base.api.ItemId(int_form=i)
          for i in stream_item_ids[start_index:end_index]
      ]
      item_timestamps = stream_items_by_stream_id[stream_id][1][start_index:end_index]
      item_entries = []
      for item_id, item_timestamp_usec in \
          itertools.izip(item_ids, item_timestamps):
        item_entry = base.atom.load_item_entry(
            web.config.reader_archive_directory, item_id)
        if item_entry:
          item_entry.display_timestamp = datetime.datetime.utcfromtimestamp(
              item_timestamp_usec/1000000).strftime('%B %d, %Y')
          item_entries.append(item_entry)
      return item_entries

    def load_recent_item_entries(state_tag_name):
      return load_item_entries(state_tag_name, 0, 2)

    def load_first_item_entry(state_tag_name):
      entries = load_item_entries(state_tag_name, -1, None)
      return entries[0] if entries else None

    def item_count(state_tag_name):
      stream_id = state_stream_id(state_tag_name)
      if stream_id in stream_items_by_stream_id:
        return len(stream_items_by_stream_id[stream_id][0])
      return 0

    followed_friends = [
        f for f in web.config.reader_friends
        if f.is_following and not f.is_current_user and
            stream_items_by_stream_id.get(f.stream_id, ([], []))[0]
    ]
    for friend in followed_friends:
      friend.item_count = len(stream_items_by_stream_id[friend.stream_id][0])
    followed_friends.sort(key=lambda f: f.display_name)

    return render.overview(
      user_id=user_id,
      recent_read_items=load_recent_item_entries('read'),
      recent_kept_unread_items=load_recent_item_entries('kept-unread'),
      recent_starred_items=load_recent_item_entries('starred'),
      recent_broadcast_items=load_recent_item_entries('broadcast'),

      first_read_item=load_first_item_entry('read'),
      read_item_count=item_count('read'),
      first_starred_item=load_first_item_entry('starred'),
      starred_item_count=item_count('starred'),
      first_broadcast_item=load_first_item_entry('broadcast'),
      broadcast_item_count=item_count('broadcast'),

      followed_friends=followed_friends,
      broadcast_friends_item_count=item_count('broadcast-friends'))

class EmbedIframe:
  def GET(self):
    input = web.input()
    return render.embed_iframe(
        src=input.src, width=input.width, height=input.height)

class Trends:
  def GET(self):
    return render.trends()

class StubbedOut:
  '''No-op handler, just avoids a 404.'''
  def GET(self):
    return 'Not implemented.'

  def POST(self):
    return 'Not implemented.'

def main():
  base.log.init()

  parser = argparse.ArgumentParser(
      description='Reanimated Google Reader\'s corpse to allow the Reader UI '
                  'to be used to browse a reader_archive-generated directory')

  parser.add_argument('archive_directory',
                      help='Directory to load archive data from.')
  parser.add_argument('--port', type=int, default=8074,
                      help='Port that the HTTP server will run on.')
  parser.add_argument('--disable_launch_in_browser' ,action='store_true',
                      help='Don\'t open the server in the local browser. Mainly '
                            'meant for use during development')

  args = parser.parse_args()

  archive_directory = base.paths.normalize(args.archive_directory)
  if not os.path.exists(archive_directory):
    logging.error('Could not find archive directory %s', archive_directory)
    syst.exit(1)
  web.config.reader_archive_directory = archive_directory

  _load_archive_data(archive_directory)

  app = web.application(urls, globals())

  homepage_url = 'http://%s:%d/reader/view/' % (socket.gethostname(), args.port)
  logging.info('Serving at %s', homepage_url)
  if not args.disable_launch_in_browser:
    webbrowser.open_new_tab(homepage_url)

  _run_app(app, args.port)

def _load_archive_data(archive_directory):
  _load_user_info()
  user_info = web.config.reader_user_info
  logging.info('Loading archive for %s', user_info.email or user_info.user_name)
  _load_friends()
  _load_streams(archive_directory)

def _load_friends():
  friends = [base.api.Friend.from_json(t) for t in _data_json('friends.json')]
  friends_by_stream_id = {f.stream_id: f for f in friends}
  web.config.reader_friends = friends
  web.config.reader_friends_by_stream_id = friends_by_stream_id

def _load_streams(archive_directory):
  stream_items_by_stream_id = {}
  stream_ids_by_item_id = {}
  streams_directory = os.path.join(archive_directory, 'streams')
  stream_file_names = os.listdir(streams_directory)
  logging.info('Loading item refs for %d streams', len(stream_file_names))
  start_time = time.time()
  for i, stream_file_name in enumerate(stream_file_names):
    with open(os.path.join(streams_directory, stream_file_name)) as stream_file:
      try:
        stream_json = json.load(stream_file)
      except ValueError, e:
        logging.warning(
            'Could not parse JSON in stream file %s: %s', stream_file_name, e)
        continue
      stream_id = stream_json['stream_id']
      stream_items = tuple(
          (timestamp_usec, int(item_id_json, 16))
          for item_id_json, timestamp_usec
              in stream_json['item_refs'].iteritems()
      )
      stream_items = sorted(
          stream_items, key=operator.itemgetter(0), reverse=True)

      # We store the timestamps and item IDs in parallel tuples to reduce the
      # overhead of having a tuple per item.
      stream_items_by_stream_id[stream_id] = (
          tuple(si[1] for si in stream_items),
          tuple(si[0] for si in stream_items)
      )
      # Don't care about non-user streams (for labeling as categories), or
      # about the reading-list stream (applied to most items, not used by the
      # UI).
      if stream_id.startswith('user/') and \
          not stream_id.endswith('/reading-list'):
        for _, item_id_int_form in stream_items:
          stream_ids_by_item_id.setdefault(
              item_id_int_form, []).append(stream_id)
      if i % 25 == 0:
        logging.debug('  %d/%d streams loaded', i + 1, len(stream_file_names))
  web.config.reader_stream_items_by_stream_id= stream_items_by_stream_id
  web.config.reader_stream_ids_by_item_id = stream_ids_by_item_id
  logging.info('Loaded item refs from %d streams in %g seconds',
      len(stream_items_by_stream_id), time.time() - start_time)

def _data_json(file_name):
  file_path = os.path.join(
      web.config.reader_archive_directory, 'data', file_name)
  with open(file_path) as data_file:
    return json.load(data_file)

def _load_user_info():
  try:
      web.config.reader_user_info = \
          base.api.UserInfo.from_json(_data_json('user-info.json'))
      return
  except:
    pass

  # Synthesize a UserInfo object for the archives created before
  # b7993c5f91c1856d98d4dd702d09424e099b47a7.
  user_id = None
  email = None
  profile_id = None
  user_name = None
  public_user_name = None
  is_blogger_user = False
  signup_time_sec = 0
  is_multi_login_enabled = False

  tags = [base.api.Tag.from_json(t) for t in _data_json('tags.json')]
  for tag in tags:
    stream_id = tag.stream_id
    stream_id_pieces = tag.stream_id.split('/', 2)
    if len(stream_id_pieces) == 3 and \
        stream_id_pieces[0] == 'user' and \
        stream_id_pieces[2] == 'state/com.google/reading-list':
      user_id = stream_id_pieces[1]

  friends = [base.api.Friend.from_json(t) for t in _data_json('friends.json')]
  for friend in friends:
    if friend.is_current_user:
      if friend.email_addresses:
        email = friend.email_addresses[0]
      for i, friend_user_id in enumerate(friend.user_ids):
        if friend_user_id == user_id:
          profile_id = friend.profile_ids[i]
          break
      user_name = friend.given_name
      break

  web.config.reader_user_info = base.api.UserInfo(
      user_id=user_id, email=email, profile_id=profile_id, user_name=user_name,
      public_user_name=public_user_name, is_blogger_user=is_blogger_user,
      signup_time_sec=signup_time_sec,
      is_multi_login_enabled=is_multi_login_enabled)


def _run_app(app, port):
    func = app.wsgifunc()
    func = base.middleware.StaticMiddleware(
        func,
        url_path_prefix=_READER_STATIC_PATH_PREFIX,
        static_directory=_STATIC_DIRECTORY)
    func = base.middleware.LogMiddleware(func)

    web.httpserver.server = web.httpserver.WSGIServer(('0.0.0.0', port), func)

    try:
        web.httpserver.server.start()
    except (KeyboardInterrupt, SystemExit):
        logging.info('Shutting down the server')
        web.httpserver.server.stop()
        web.httpserver.server = None

if __name__ == '__main__':
  main()
