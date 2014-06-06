#! /usr/bin/env python

#    Copyright 2011 OpenStack LLC
#    Copyright 2012 Hewlett-Packard Development Company, L.P.
#    Copyright 2014 Wulf C. Krueger
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#
# This file differs greatly from the original upstream version at 
# https://github.com/openstack-infra/gerritbot/blob/master/gerritbot/bot.py
#
# These modifications are done for use within Exherbo (Linux) and were done by
# Wulf C. Krueger. Any errors, mistakes, etc. are solely mine and not those of
# the original authors. Please report any bugs, etc. with respect to my
# additions to wk@mailstation.de. Thanks!
#
# This prominent statement is added to comply with clause 4b of the License.
#

# The configuration file should look like:
"""
[ircbot]
nick=NICKNAME
pass=PASSWORD
server=irc.freenode.net
port=6667
force_ssl=false
server_password=SERVERPASS
channel_config=/path/to/yaml/config
pid=/path/to/pid_file
use_mqtt=True

[gerrit]
user=gerrit2
key=/path/to/id_rsa
host=review.example.com
port=29418

[mqtt]
host=example.com
port=1883
websocket=False
"""

# The yaml channel config should look like:
"""
openstack-dev:
    events:
      - patchset-created
      - change-merged
    projects:
      - openstack/nova
      - openstack/swift
    branches:
      - master
"""

import ConfigParser
#import daemon
import datetime
import dateutil.relativedelta
import gerritlib.gerrit
import irc.bot
import json
import logging.config
import os
import re
import ssl
import subprocess
import sys
import threading
import time
import yaml

import paho.mqtt.client as mqtt

#try:
#    import daemon.pidlockfile
#    pid_file_module = daemon.pidlockfile
#except Exception:
    # as of python-daemon 1.6 it doesn't bundle pidlockfile anymore
    # instead it depends on lockfile-0.9.1
#    import daemon.pidfile
#    pid_file_module = daemon.pidfile

fg = 0
ANTI_FLOOD_DELAY = 1.0

# https://bitbucket.org/jaraco/irc/issue/34/
# irc-client-should-not-crash-on-failed
# ^ This is why pep8 is a bad idea.
irc.client.ServerConnection.buffer_class.errors = 'replace'


class GerritBot(irc.bot.SingleServerIRCBot):
    def __init__(self, channels, nickname, password, server, port=6667,
                 force_ssl=False, server_password=None):
        if force_ssl or port == 6697:
            factory = irc.connection.Factory(wrapper=ssl.wrap_socket)
            super(GerritBot, self).__init__([(server, port, server_password)],
                                            nickname, nickname,
                                            connect_factory=factory)
        else:
            super(GerritBot, self).__init__([(server, port, server_password)],
                                            nickname, nickname)
        self.channel_list = channels
        self.nickname = nickname
        self.password = password
        self.log = logging.getLogger('gerritbot')

    def on_nicknameinuse(self, c, e):
        self.log.info('Nick previously in use, recovering.')
        c.nick(c.get_nickname() + "_")
        c.privmsg("nickserv", "identify %s " % self.password)
        time.sleep(ANTI_FLOOD_DELAY)
        c.privmsg("nickserv", "ghost %s %s" % (self.nickname, self.password))
        time.sleep(ANTI_FLOOD_DELAY)
        c.privmsg("nickserv", "release %s %s" % (self.nickname, self.password))
        time.sleep(ANTI_FLOOD_DELAY)
        c.nick(self.nickname)
        self.log.info('Nick previously in use, recovered.')

    def on_welcome(self, c, e):
        self.log.info('Identifying with IRC server.')
        c.privmsg("nickserv", "identify %s " % self.password)
        self.log.info('Identified with IRC server.')
        for channel in self.channel_list:
            c.join(channel)
            self.log.info('Joined channel %s' % channel)
            time.sleep(ANTI_FLOOD_DELAY)

    def send(self, channel, msg):
        self.log.info('Sending "%s" to %s' % (msg, channel))
        try:
            self.connection.privmsg(channel, msg)
            time.sleep(ANTI_FLOOD_DELAY)
        except Exception:
            self.log.exception('Exception sending message:')
            self.connection.reconnect()

    def on_privmsg(self, c, e):
        self.do_command(e, e.arguments[0], 1)

    def on_pubmsg(self, c, e):
        if re.search(r'^!',  e.arguments[0]):
            a = e.arguments[0].split("!")
            self.do_command(e, a[1].strip(), 0)
        return

    def do_command(self, e, cmd, private):
        nick = e.source.nick
        c = self.connection
        match = ""
        resp = ""
        error = 0

        pattern = cmd.split()
        if len(pattern) > 1:
            match = pattern[1]

        if re.search(r'^pl\b', cmd) or re.search(r'^patchlist\b', cmd):
            count = 0
            if match:
                kindcount = 0
                # matchKind = [ "message", "project", "owner", "change", "topic" ]
                matchKind = [ "message", "project", "owner" ]

                if match.split(":",1)[0] in matchKind:
                    try:
                        resp = gerritlib.gerrit.Gerrit.bulk_query(fg, "status:open AND %s" % match)
                    except Exception:
                        c.notice(nick, "Illegal expression")
                        error = 1
                else:
                    while True:
                        try:
                            resp = gerritlib.gerrit.Gerrit.bulk_query(fg, "status:open AND %s:%s" % (matchKind[kindcount], match))
                        except Exception:
                            c.notice(nick, "Illegal expression")
                            error = 1
                            break
                        if resp[-1]['type'] == "error" or int(resp[-1]['rowCount']) == 0:
                            if kindcount < len(matchKind)-1:
                                kindcount = kindcount + 1
                            else:
                                break
                        else:
                            break
            else:
                resp = gerritlib.gerrit.Gerrit.bulk_query(fg, "status:open")

            if error == 0 and resp[-1]['type'] != "error" and ( private == 1 or int(resp[-1]['rowCount']) < 4 ):
                while True:
                    if count < int(resp[-1]['rowCount']):
                        dt1 = datetime.datetime.now()
                        dt2 = datetime.datetime.fromtimestamp(resp[count]['lastUpdated'])
                        rd = dateutil.relativedelta.relativedelta (dt1, dt2)
                        submtime = "%d days and %d hours" % (rd.days, rd.hours)
                        msg = '%s ::%s (submitted by %s %s ago): %s' % (
                            resp[count]['url'],
                            resp[count]['project'],
                            resp[count]['owner']['username'],
                            submtime,
                            resp[count]['subject'])
                        if private == 1:
                            c.notice(nick, msg)
                        else:
                            c.notice(e.target, msg)
                    else:
                        break
                    count = count + 1
            elif error == 0 and resp[-1]['type'] != "error" and int(resp[-1]['rowCount']) > 4:
                msg = "%s matching patches in queue. Use pl in private for a full list." % resp[-1]['rowCount']
                c.notice(e.target, msg)

        # http://bpaste.net/raw/159498/ ::arbor

        elif re.search(r'^pq\b', cmd) or re.search(r'^patchqueue\b', cmd):
            error = 0
            repo = ""
            respfile = ""
            cmd = ""
            result = ""
            NOTIFY_SINNER = 1

            #if re.search(r'freeedrich', nick):
            #    message = "Please use Gerrit directly: https://galileo.mailstation.de/gerrit.html"
            #    c.notice(nick, message)
            #    error = 1

            if error == 0 and len(pattern) > 2:
                repo = pattern[2]
                repo = repo.split(":")
                if len(repo) != 3:
                    error = 1

            if error == 0:
                cmd = "/usr/local/bin/pq.bash" + " " + match + " " + repo[2]
                try:
                    respfile = subprocess.Popen(cmd, stderr=subprocess.STDOUT, stdout=subprocess.PIPE, shell=True).stdout.read()
                except Exception:
                    self.log.error('pq.bash failed.')
                    error = 1

                if error == 0:
                    cmd = re.findall(r'WKRESPONSE:.*', respfile)
                    result = cmd[0].split(":")
                    try:
                        with open (result[1], "r") as myfile:
                            data=myfile.read().replace('\n', '')
                    except Exception:
                        self.log.error('reading the response file failed')
                        error = 1

                    if not "Repository not in Gerrit" in data and error == 0:
                        try:
                            with open("/srv/www/localhost/htdocs/patch/p_result.html", "w") as text_file:
                                text_file.write("%s" % data)
                        except Exception:
                            self.log.error('writing the result file failed.')
                            error = 1

                        if NOTIFY_SINNER == 1 and error == 0:
                            message = "Please read https://galileo.mailstation.de/gerrit.html"
                            c.notice(nick, message)

                        if error == 0:
                            try:
                                os.remove(result[1])
                            except Exception:
                                self.log.error('deleting the result file failed.')
                                error = 1

        elif re.search(r'^pd\b', cmd) or re.search(r'^patchdone\b', cmd):
            error = 0
            pdresult = 0
            pdaction = { 'abandon': True }

            if match:
                try:
                    resp = gerritlib.gerrit.Gerrit.bulk_query(fg, "status:open AND message:{Patch-URL: %s}" % match)
                except Exception:
                    self.log.error('querying Gerrit for pd failed')
                    error = 1

                if resp[-1]['type'] == "error" or int(resp[-1]['rowCount']) == 0:
                    self.log.error('result of type error or empty.')
                    error = 1
                else:
                    if error == 0:
                        try:
                            pdresult = gerritlib.gerrit.Gerrit.review(fg, resp[0]['project'], resp[0]['number']+",1", cmd, pdaction)
                        except Exception:
                            self.log.error('pd failed.')
                            error = 1

class Gerritw(threading.Thread):
    def __init__(self, ircbot, channel_config, server,
                 username, port=29418, keyfile=None):
        super(Gerrit, self).__init__()
        self.ircbot = ircbot
        self.channel_config = channel_config
        self.log = logging.getLogger('gerritbot')
        self.server = server
        self.username = username
        self.port = port
        self.keyfile = keyfile
        self.connected = False

    def connect(self):
        try:
            self.gerrit = gerritlib.gerrit.Gerrit(
                self.server, self.username, self.port, self.keyfile)
            self.gerrit.startWatching()
            self.log.info('Start watching Gerrit event stream.')
            self.connected = True
        except Exception:
            self.log.exception('Exception while connecting to gerrit')
            self.connected = False
            # Delay before attempting again.
            time.sleep(1)

    def patchset_created(self, channel, data):
        msg = '%s proposed %s/%s: %s  %s patchset: %s' % (
            data['patchSet']['uploader']['name'],
            data['change']['project'],
            data['change']['branch'],
            data['change']['subject'],
            data['change']['url'],
            data['patchSet']['number'])
        self.log.info('Compiled Message %s: %s' % (channel, msg))
        self.ircbot.send(channel, msg)

    def ref_updated(self, channel, data):
        refName = data['refUpdate']['refName']
        m = re.match(r'(refs/tags)/(.*)', refName)

        if m:
            tag = m.group(2)
            msg = '%s tagged project %s with %s' % (
                data['submitter']['username'],
                data['refUpdate']['project'],
                tag
            )
            self.log.info('Compiled Message %s: %s' % (channel, msg))
            self.ircbot.send(channel, msg)

    def comment_added(self, channel, data):
        msg = '%s commented on %s/%s: %s  %s' % (
            data['author']['name'],
            data['change']['project'],
            data['change']['branch'],
            data['change']['subject'],
            data['change']['url'])
        self.log.info('Compiled Message %s: %s' % (channel, msg))
        self.ircbot.send(channel, msg)

        for approval in data.get('approvals', []):
            if (approval['type'] == 'VRIF' and approval['value'] == '-2'
                and channel in self.channel_config.events.get(
                    'x-vrif-minus-2', set())):
                msg = 'Verification of a change to %s failed: %s  %s' % (
                    data['change']['project'],
                    data['change']['subject'],
                    data['change']['url'])
                self.log.info('Compiled Message %s: %s' % (channel, msg))
                self.ircbot.send(channel, msg)

            if (approval['type'] == 'VRIF' and approval['value'] == '2'
                and channel in self.channel_config.events.get(
                    'x-vrif-plus-2', set())):
                msg = 'Verification of a change to %s succeeded: %s  %s' % (
                    data['change']['project'],
                    data['change']['subject'],
                    data['change']['url'])
                self.log.info('Compiled Message %s: %s' % (channel, msg))
                self.ircbot.send(channel, msg)

            if (approval['type'] == 'CRVW' and approval['value'] == '-2'
                and channel in self.channel_config.events.get(
                    'x-crvw-minus-2', set())):
                msg = 'A change to %s has been rejected: %s  %s' % (
                    data['change']['project'],
                    data['change']['subject'],
                    data['change']['url'])
                self.log.info('Compiled Message %s: %s' % (channel, msg))
                self.ircbot.send(channel, msg)

            if (approval['type'] == 'CRVW' and approval['value'] == '2'
                and channel in self.channel_config.events.get(
                    'x-crvw-plus-2', set())):
                msg = 'A change to %s has been approved: %s  %s' % (
                    data['change']['project'],
                    data['change']['subject'],
                    data['change']['url'])
                self.log.info('Compiled Message %s: %s' % (channel, msg))
                self.ircbot.send(channel, msg)

    def change_merged(self, channel, data):
        msg = 'Merged %s: %s  %s' % (
            data['change']['project'],
            data['change']['subject'],
            data['change']['url'])
        self.log.info('Compiled Message %s: %s' % (channel, msg))
        self.ircbot.send(channel, msg)

    def _read(self, data):
        try:
            if data['type'] == 'ref-updated':
                channel_set = self.channel_config.events.get('ref-updated')
            else:
                channel_set = (self.channel_config.projects.get(
                    data['change']['project'], set()) &
                    self.channel_config.events.get(
                        data['type'], set()) &
                    self.channel_config.branches.get(
                        data['change']['branch'], set()))
        except KeyError:
            # The data we care about was not present, no channels want
            # this event.
            channel_set = set()
        if not channel_set:
            channel_set = set()
        self.log.info('Potential channels to receive event notification: %s' %
                      channel_set)
        for channel in channel_set:
            if data['type'] == 'comment-added':
                self.comment_added(channel, data)
            elif data['type'] == 'patchset-created':
                self.patchset_created(channel, data)
            elif data['type'] == 'change-merged':
                self.change_merged(channel, data)
            elif data['type'] == 'ref-updated':
                self.ref_updated(channel, data)

    def run(self):
        while True:
            while not self.connected:
                self.connect()
            try:
                event = self.gerrit.getEvent()
                self.log.info('Received event: %s' % event)
                self._read(event)
            except Exception:
                self.log.exception('Exception encountered in event loop')
                if not self.gerrit.watcher_thread.is_alive():
                    # Start new gerrit connection. Don't need to restart IRC
                    # bot, it will reconnect on its own.
                    self.connected = False


class GerritMQTT(Gerrit):
    def __init__(self, ircbot, channel_config, server, base_topic='gerrit',
                 port=1883, websocket=False):
        threading.Thread.__init__(self)
        self.ircbot = ircbot
        self.channel_config = channel_config
        self.log = logging.getLogger('gerritbot')
        self.server = server
        self.port = port
        self.websocket = websocket
        self.base_topic = base_topic
        self.connected = False

    def connect(self):
        try:
            self.client.connect(self.server, port=self.port)

            self.log.info('Start watching Gerrit event stream via mqtt!.')
            self.connected = True
        except Exception:
            self.log.exception('Exception while connecting to mqtt')
            self.client.reinitialise()
            self.connected = False
            # Delay before attempting again.
            time.sleep(1)

    def run(self):
        def _on_connect(client, userdata, flags, rc):
            client.subscribe(self.base_topic + '/#')

        def _on_message(client, userdata, msg):
            data = json.loads(msg.payload)
            if data:
                self._read(data)

        if self.websocket:
            self.client = mqtt.Client(transport='websockets')
        else:
            self.client = mqtt.Client()
        self.client.on_connect = _on_connect
        self.client.on_message = _on_message

        while True:
            while not self.connected:
                self.connect()
            try:
                self.client.loop()
            except Exception:
                self.log.exception('Exception encountered in event loop')
                time.sleep(5)


class ChannelConfig(object):
    def __init__(self, data):
        self.data = data
        keys = data.keys()
        for key in keys:
            if key[0] != '#':
                data['#' + key] = data.pop(key)
        self.channels = data.keys()
        self.projects = {}
        self.events = {}
        self.branches = {}
        for channel, val in iter(self.data.items()):
            for event in val['events']:
                event_set = self.events.get(event, set())
                event_set.add(channel)
                self.events[event] = event_set
            for project in val['projects']:
                project_set = self.projects.get(project, set())
                project_set.add(channel)
                self.projects[project] = project_set
            for branch in val['branches']:
                branch_set = self.branches.get(branch, set())
                branch_set.add(channel)
                self.branches[branch] = branch_set


def _main(config):
    setup_logging(config)

    fp = config.get('ircbot', 'channel_config')
    if fp:
        fp = os.path.expanduser(fp)
        if not os.path.exists(fp):
            raise Exception("Unable to read layout config file at %s" % fp)
    else:
        raise Exception("Channel Config must be specified in config file.")

    try:
        channel_config = ChannelConfig(yaml.load(open(fp)))
    except Exception:
        log = logging.getLogger('gerritbot')
        log.exception("Syntax error in chanel config file")
        raise

    bot = GerritBot(channel_config.channels,
                    config.get('ircbot', 'nick'),
                    config.get('ircbot', 'pass'),
                    config.get('ircbot', 'server'),
                    config.getint('ircbot', 'port'),
                    config.getboolean('ircbot', 'force_ssl'),
                    config.get('ircbot', 'server_password'))
<<<<<<< HEAD
    if config.has_option('ircbot', 'use_mqtt'):
        use_mqtt = config.getboolean('ircbot', 'use_mqtt')
    else:
        use_mqtt = False

    if use_mqtt:
        g = GerritMQTT(bot,
                       channel_config,
                       config.get('mqtt', 'host'),
                       config.get('mqtt', 'base_topic'),
                       config.getint('mqtt', 'port'),
                       config.getboolean('mqtt', 'websocket'))
    else:
        g = Gerrit(bot,
                   channel_config,
                   config.get('gerrit', 'host'),
                   config.get('gerrit', 'user'),
                   config.getint('gerrit', 'port'),
                   config.get('gerrit', 'key'))
=======
    g = Gerritw(bot,
               channel_config,
               config.get('gerrit', 'host'),
               config.get('gerrit', 'user'),
               config.getint('gerrit', 'port'),
               config.get('gerrit', 'key'))

    fg = gerritlib.gerrit.Gerrit(
               config.get('gerrit', 'host'),
               config.get('gerrit', 'user'),
               config.getint('gerrit', 'port'),
               config.get('gerrit', 'key'))

>>>>>>> Add the pl command
    g.start()
    bot.start()


def main():
    if len(sys.argv) != 2:
        print("Usage: %s CONFIGFILE" % sys.argv[0])
        sys.exit(1)

<<<<<<< HEAD
    config = ConfigParser.ConfigParser({'force_ssl': 'false',
                                        'server_password': None})
    config.read(sys.argv[1])

    pid_path = ""
    if config.has_option('ircbot', 'pid'):
        pid_path = config.get('ircbot', 'pid')
    else:
        pid_path = "/var/run/gerritbot/gerritbot.pid"

    pid = pid_file_module.TimeoutPIDLockFile(pid_path, 10)
    with daemon.DaemonContext(pidfile=pid):
        _main(config)
=======
#    pid = pid_file_module.TimeoutPIDLockFile(
#        "/tmp/gerritbot.pid", 10)
#    with daemon.DaemonContext(pidfile=pid):
    _main()
>>>>>>> Stop daemonisation for debugging


def setup_logging(config):
    if config.has_option('ircbot', 'log_config'):
        log_config = config.get('ircbot', 'log_config')
        fp = os.path.expanduser(log_config)
        if not os.path.exists(fp):
            raise Exception("Unable to read logging config file at %s" % fp)
        logging.config.fileConfig(fp)
    else:
        logging.basicConfig(level=logging.ERROR)


if __name__ == "__main__":
    main()
