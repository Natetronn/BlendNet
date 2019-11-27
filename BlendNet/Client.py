#!/usr/bin/python3
# -*- coding: UTF-8 -*-
'''BlendNet Client

Description: REST client
'''

import os
import time
import json # Used to parse response
import urllib # To request API
import hashlib
from . import providers

class Client:
    def info(self):
        '''Get information about the environment'''
        return self._engine.get('info')

    def status(self):
        '''Get information about the current status'''
        return self._engine.get('status')

    def tasks(self):
        '''Get the tasks information'''
        return self._engine.get('task')

    def taskFileStreamPut(self, task, rel_path, stream, size, checksum):
        '''Send stream to the task file'''
        path = 'task/%s/file/%s' % (task, rel_path)
        return self._engine.put(path, stream, size, checksum)

    def taskConfigPut(self, task, config_data):
        '''Send task configuration'''
        from io import StringIO

        path = 'task/%s/config' % task
        data = json.dumps(config_data)
        size = len(data)
        stream = StringIO(data)

        return self._engine.put(path, stream, size)

    def taskRun(self, task):
        '''Run the prepared task'''
        return self._engine.get('task/%s/run' % task)

    def taskInfo(self, task):
        '''Return the task current info'''
        return self._engine.get('task/%s' % task)

    def taskStatus(self, task):
        '''Return the task current status'''
        return self._engine.get('task/%s/status' % task)

    def taskMessages(self, task):
        '''Return the task execution messages'''
        return self._engine.get('task/%s/messages' % task)

    def taskDetails(self, task):
        '''Return the task execution details'''
        return self._engine.get('task/%s/details' % task)

    def taskStop(self, task):
        '''Stop the task execution'''
        return self._engine.get('task/%s/stop' % task)

    def taskRemove(self, task):
        '''Remove the task from the manager'''
        return self._engine.delete('task/%s' % task)

    def taskResultDownloadStream(self, task, result, stream_func):
        '''Will download result name (preview/render) into the function-processor of stream'''
        return self._engine.download('task/%s/status/result/%s' % (task, result), stream_func)

class ClientEngine:
    def __init__(self, address, cfg):
        self._address = address
        self._cfg = cfg
        self._initSSL()

    def _initSSL(self):
        import ssl
        self._context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._context.check_hostname = False
        self._ca = None

    def _getCA(self):
        '''For trusted communication use generated by Manager CA certificate'''
        if self._ca:
            return True

        print('DEBUG: Trying to get CA certificate from the bucket')
        self._ca = providers.downloadDataFromBucket(self._cfg['bucket'], 'ca.crt')
        if not self._ca:
            return False

        self._context.load_verify_locations(cadata=self._ca.decode())
        return True

    def _request(self, path, data = None, method = 'GET'):
        '''Creates request to execute'''
        if not self._getCA() or not self._address:
            return None

        url = 'https://%s:%d/api/v1/%s' % (self._address, self._cfg['listen_port'], path)
        req = urllib.request.Request(url, data=data, method=method)

        creds = '%s:%s' % (self._cfg['auth_user'], self._cfg['auth_password'])
        if creds != ':':
            from base64 import b64encode
            req.add_header('Authorization', 'Basic %s' % b64encode(bytes(creds, 'utf-8')).decode('ascii'))

        return req

    def _requestExecute(self, req, run_func):
        '''Executes the request'''
        for repeat in range(3):
            try:
                return run_func(req)
            except urllib.error.HTTPError as e:
                print('WARN: Communication issue with request to "%s": HTTP %d %s: %s' % (req.full_url, e.getcode(), e.reason, e.read(1024)))
            except urllib.error.URLError as e:
                if 'CERTIFICATE_VERIFY_FAILED' in str(e.reason):
                    print('WARN: Seems like wrong (or old) CA is loaded, reinit SSL context and repeat.')
                    self._initSSL()
                else:
                    if isinstance(e.reason, BrokenPipeError) and req.data:
                        # Ignore error "Broken pipe" for PUT requests - server checks sha1
                        return True
                    print('WARN: Communication issue with request to "%s": %s' % (req.full_url, e.reason))
                    return None
            except:
                import sys
                print('ERROR: Communication exception - check the remote service for errors "%s": %s' % (req.full_url, sys.exc_info()[0]))
                return None

            print('INFO: Retry request "%s" in 1 sec' % req.full_url)
            time.sleep(1.0)

    def _requestExecuteRun(self, req):
        '''Executes the API request'''
        with urllib.request.urlopen(req, timeout=3 if req.data else 10, context=self._context) as res:
            data = json.load(res)
            if not data.get('success', False):
                # Something went wrong
                print('ERROR: Execution issue from API for "%s": %s' % (data.get('message')))
                return None

            return data.get('data', True)

    def _requestDownloadRun(self, req):
        '''Executes the download request, uses req._out_path to store file or req._out_func as processing function'''
        with urllib.request.urlopen(req, timeout=3, context=self._context) as res:
            length = res.headers['content-length']
            sha1 = res.headers['x-checksum-sha1']
            if not length or not sha1:
                print('ERROR: Unable to download stream without Content-Length and X-Checksum-Sha1 headers')
                return False
            size = int(length)

            if hasattr(req, '_out_func'):
                return req._out_func(res, size, sha1)

            try:
                sha1_calc = hashlib.sha1()
                size_left = size
                with open(req._out_path, 'wb') as f:
                    for chunk in iter(lambda: res.read(min(1048576, size_left)), b''):
                        sha1_calc.update(chunk)
                        f.write(chunk)
                        size_left -= len(chunk)
                    if sha1 != sha1_calc.hexdigest():
                        raise urllib.error.URLError('Incorrect sha1 signature')
                    return sha1_calc.hexdigest(), req._out_path
            except:
                os.remove(req._out_path)
                raise

    def get(self, path):
        req = self._request(path)

        if not req:
            return None

        return self._requestExecute(req, self._requestExecuteRun)

    def delete(self, path):
        req = self._request(path, None, 'DELETE')

        if not req:
            return None

        return self._requestExecute(req, self._requestExecuteRun)

    def put(self, path, stream, size, checksum = None):
        req = self._request(path, stream, 'PUT')

        req.add_header('Content-Length', str(size))
        req.add_header('Content-Type', 'application/octet-stream')
        if checksum:
            req.add_header('X-Checksum-Sha1', checksum)

        return self._requestExecute(req, self._requestExecuteRun)

    def download(self, path, out):
        req = self._request(path)

        if not req:
            return None

        if callable(out):
            req._out_func = out
        elif isinstance(out, str):
            req._out_path = out

        return self._requestExecute(req, self._requestDownloadRun)
