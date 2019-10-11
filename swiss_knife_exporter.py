#!/usr/bin/env python3

from prometheus_client.core import GaugeMetricFamily, REGISTRY
from prometheus_client import start_http_server
from yaml import load, FullLoader
import time
import subprocess
import socket
import struct
import syslog
import os

class Logging:
    def __init__(self, syslog=False):
        self.syslog = syslog

    def doLog(self, msg):
        if self.syslog:
            syslog.syslog(msg)
        else:
            print(msg)

class ZabbixAgent:
    def __init__(self, host, port, timeout, log, request = ''):
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self.request = request.encode('UTF-8')
        self.value = 0.0
        self.log = log

    def _unpack_answer(self, data):
        header = struct.Struct("<4sBQ")
        (prefix, version, length) = header.unpack(data[:13])
        payload = struct.Struct("<%ds" % length)
        self.value = float(payload.unpack(data[13:])[0])

    def runQuery(self):
        zsocket = None
        try:
            zsocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            zsocket.settimeout(self.timeout)
            zsocket.connect((self.host, self.port))
            zsocket.send(self.request)
            data = b''
            chunk = " "
            while chunk:
                chunk = zsocket.recv(1024)
                data = data + chunk
            if len(data) > 13:
                self._unpack_answer(data)
        except socket.timeout as err:
            self.log.doLog('Zabbix host: %s Error: %s' % (self.host, str(err)))
        except socket.gaierror as err:
            self.log.doLog('Zabbix host: %s Error: %s' % (self.host, str(err)))
        except ConnectionRefusedError as err:
            self.log.doLog('Zabbix Port: %s Error: %s' % (self.port, str(err)))
        except BlockingIOError as err:
            self.log.doLog('Please check timeout parameter in config file. Error: %s' % str(err))
        finally:
            zsocket.close()

class DirectShellExec:
    def __init__(self, log, request = ''):
        self.request = request
        self.value = 0.0
        self.log = log

    def runQuery(self):
        try:
            self.value = float(subprocess.check_output(self.request, shell=True).decode("utf-8"))
        except subprocess.CalledProcessError as err:
            self.log.doLog('Shell command execution error. Error: %s' % err)

class MainPromTread(object):
    def __init__(self, yml_obj):
        self.metricSource = None
        self.cfg = yml_obj
        if self.cfg['exporter_config']['useZabbix']:
            self.metricSource = ZabbixAgent(yml_obj['zabbix_config']['host'],
                                             yml_obj['zabbix_config']['port'],
                                             yml_obj['zabbix_config']['socket_timeout'],
                                             yml_obj['log_point'])
        else:
            self.metricSource = DirectShellExec(yml_obj['log_point'])

    def collect(self):
        for exp_metric in self.cfg['metrics']:
            self.metricSource.request = exp_metric['metric']
            self.metricSource.runQuery()
            try:
                metric = GaugeMetricFamily(
                    'skm_%s' % exp_metric['name'],
                    'metrics from zabbix',
                    labels=exp_metric['labels'].keys())
                metric.add_metric(exp_metric['labels'].values(), self.metricSource.value)
                yield metric
            except ValueError as err:
                self.cfg['log_point'].doLog('Zabbix exporter: Error: %s' % str(err))

if __name__ == "__main__":
        possible_path_config = [os.path.dirname(os.path.realpath(__file__)) + '/exporter_config.yml',
                                '~/.exporter_config.yml',
                                '/etc/prometheus_swiss_knife_exporter/exporter_config.yml']

        syslog.syslog('Zabbix exporter: INFO: looking for exporter_config.yml...')
        for cfg_path in possible_path_config:
            if not os.path.isfile(cfg_path):
                continue

            with open('./exporter_config.yml') as yml_file:
                ex_cfg = load(yml_file, Loader=FullLoader)
            syslog.openlog(logoption=syslog.LOG_PID, facility=syslog.LOG_LOCAL7)
            ex_cfg['log_point'] = Logging(ex_cfg['exporter_config']['syslog'])
            ex_cfg['log_point'].doLog('Zabbix exporter: INFO: %s found' % cfg_path)
            REGISTRY.register(MainPromTread(ex_cfg))
            start_http_server(ex_cfg['exporter_config']['exporter_port'])
            while True: time.sleep(1)
