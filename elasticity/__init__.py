from elasticsearch import Elasticsearch, client, helpers
from functools import partial
import itertools
import logging
from math import ceil
import os
from Queue import Queue, Empty
import shutil
from threading import Lock, Thread
import tempfile
from time import gmtime, strftime, time
import yaml

logging.basicConfig(format='[%(levelname)s] %(message)s')
logger = logging.getLogger('elasticity')
logger.setLevel(logging.INFO)
info = logger.info
warn = logger.warning
error = logger.error

class ElasticityError(Exception):
    """ Thrown for various errors
    """
    pass

class AttributeDict(dict):
    """ Quick dictionary that allows for getting items
        as attributes in a convenient manner
    """
    __getattr__ = lambda self, key: self.get(key, require_value=True)
    __setattr__ = dict.__setitem__
    def get(self, key, default_val=None, require_value=False):
        """ Returns a dictionary value
        """
        val = dict.get(self, key, default_val)
        if val is None and require_value:
            raise KeyError('key "%s" not found' % key)
        return self.wrap(val)
    def wrap(self, val):
        if isinstance(val, dict):
            return AttributeDict(val)
        if isinstance(val, list):
            ret = list()
            for x in val:
                ret.append(self.wrap(x))
            return ret
        return val

class AtomicInt(object):
    """ A thread safe atomic integer
    """
    def __init__(self):
        self.value  = 0
        self.lock   = Lock()
    def set_value(self, value):
        with self.lock:
            self.value = value
            return self.value
    def get_value(self):
        return self.value
    def add_value(self, value):
        with self.lock:
            self.value += value
            return self.value
    def inc(self):
        return self.add_value(1)
    def dec(self):
        return self.add_value(-1)

def connect(host, port, user=None, password=None):
    """ Opens a new session to elastic search
    """
    info("Connecting to: %s", host)
    if user:
        return Elasticsearch([{
            'host':     host,
            'port':     int(port) }],
            http_auth=(user, password))
    else:
        return Elasticsearch([{
            'host':     host,
            'port':     int(port) }])

def read_file(file_name):
    with open(file_name, 'r') as f:
        return f.read()

def parse_config(file_name='elasticity.config'):
    """ Parses a elasticity configuration file
    """
    if not os.path.exists(file_name) or not os.path.isfile(file_name):
        raise ElasticityError('Config file not found: %s' % file_name)

    contents = read_file(file_name)

    try:
        contents_with_environment_variables_expanded = os.path.expandvars(contents)
        return AttributeDict(yaml.load(contents_with_environment_variables_expanded))
    except Exception as e:
        raise ElasticityError("Error paring config file: %s" % str(e))

def dated_name(name):
    """ Returns a name with a date
    """
    return name+"_"+strftime("%Y%m%d%H%M%S", gmtime())

def update(es, delete_old_index, close_old_index, config, num_threads, chunk_size):
    """ Updates all of the stuff
    """

    for index in config.get('indexes', []):
        index_name = dated_name(index.name)

        # don't create the index without the correct settings
        info("Creating index: %s as %s (%s)" % (index.name, index_name, index.alias))
        if index.has_key('creation_file'):
            info("Applying creation settings to %s (%s)" % (index_name, index.creation_file))
            es.indices.create(index=index_name, body=read_file(index.creation_file))
        else:
            es.indices.create(index=index_name)

        # wait for cluster health to return
        es.cluster.health(wait_for_status='yellow', request_timeout=100)

        if index.has_key('settings_file'):
            info("Applying settings to %s (%s)" % (index_name, index.settings_file))
            es.indices.close(index=index_name)
            es.indices.put_settings(body=read_file(index.settings_file), index=index_name)
            es.cluster.health(wait_for_status='yellow', request_timeout=100)
            es.indices.open(index=index_name)
            es.cluster.health(wait_for_status='yellow', request_timeout=100)

        for mapping in index.get('mappings', []):
            info("Applying mapping to %s:%s (%s)" % (index_name, mapping.doc_type, mapping.mapping_file))
            es.indices.put_mapping(body=read_file(mapping.mapping_file),
                doc_type=mapping.doc_type, index=index_name)
            es.cluster.health(wait_for_status='yellow', request_timeout=100)

        old_index = None
        if es.indices.exists_alias(index='_all', name=index.alias):

            doc_count   = int(es.count(index=index.alias)['count'])
            chunk_size  = float(chunk_size)
            chunks      = int(ceil(float(doc_count) / chunk_size))
            info("Reindexing %s documents in chunks of %s from %s:%s to %s"
                % (doc_count, int(chunk_size), index.alias, mapping.doc_type, index_name))
            reindex(es, index.alias, index_name, num_threads, chunk_size, doc_count)

            resp = es.indices.get_aliases(name=index.alias, index='_all')
            for idx in resp:
                if resp[idx]['aliases'].has_key(index.alias) and str(idx).startswith(index.name+"_"):
                    old_index = str(idx)
                    break
            info("Deleting existing alias %s" % (index.alias))
            es.indices.delete_alias(index='_all', name=index.alias)
            es.cluster.health(wait_for_status='yellow', request_timeout=100)
            
        info("Creating index alias %s for %s" % (index.alias, index_name))
        es.indices.put_alias(index=index_name, name=index.alias)
        es.cluster.health(wait_for_status='yellow', request_timeout=100)

        if close_old_index and old_index:
            warn("Closing old index %s" % (old_index,))
            es.indices.close(index=old_index)

        if delete_old_index and old_index:
            warn("Deleting old index %s" % (old_index,))
            es.indices.delete(index=old_index)
            info("Index alias %s now points at %s and  the old index %s has been deleted"
                % (index.alias, index_name, old_index))
        else:
            info("Index alias %s now points at %s - you can safely delete the old index: %s"
                % (index.alias, index_name, old_index))

def create(es, config):
    """ Creates all of the stuff
    """
    for index in config.get('indexes', []):
        index_name = dated_name(index.name)

        # don't create the index without the correct settings
        info("Creating index: %s as %s (%s)" % (index.name, index_name, index.alias))
        if index.has_key('creation_file'):
            info("Applying creation settings to %s (%s)" % (index_name, index.creation_file))
            es.indices.create(index=index_name, body=read_file(index.creation_file))
        else:
            es.indices.create(index=index_name)

        # wait for the cluster health to come back
        es.cluster.health(wait_for_status='yellow', request_timeout=100)

        if index.has_key('settings_file'):
            info("Applying settings to %s (%s)" % (index_name, index.settings_file))
            es.indices.close(index=index_name)
            es.indices.put_settings(body=read_file(index.settings_file), index=index_name)
            es.cluster.health(wait_for_status='yellow', request_timeout=100)
            es.indices.open(index=index_name)
            es.cluster.health(wait_for_status='yellow', request_timeout=100)

        for mapping in index.get('mappings', []):
            info("Applying mapping to %s:%s (%s)" % (index_name, mapping.doc_type, mapping.mapping_file))
            es.indices.put_mapping(body=read_file(mapping.mapping_file),
                                   doc_type=mapping.doc_type, index=index_name)
            es.cluster.health(wait_for_status='yellow', request_timeout=100)

        if es.indices.exists_alias(index='_all', name=index.alias):
            info("Deleting existing alias %s" % (index.alias))
            es.indices.delete_alias(index='_all', name=index.alias)
            es.cluster.health(wait_for_status='yellow', request_timeout=100)
        es.indices.put_alias(index=index_name, name=index.alias)
        es.cluster.health(wait_for_status='yellow', request_timeout=100)

def reindex(es, index_from, index_to, num_threads, chunk_size, doc_count):
    """ Reindexes all documents from index_from to index_to
        Using the given number of threads.
    """
    q = Queue(maxsize=10000)

    mutex = AtomicInt()
    mutex.set_value(0)

    counter = AtomicInt()
    counter.set_value(0)

    def _write_worker():
        prev_count  = 0
        start_time  = time()
        while mutex.get_value()==0:
            try:
                docs = q.get(False, 1000)
                docs = [{ 
                            '_index':       index_to,
                            '_type':        doc['_type'],
                            '_id':          doc['_id'],
                            '_source':      doc['_source']
                        } for doc in docs]
                helpers.bulk(es, docs)
                counter.add_value(len(docs))
                q.task_done()

                elapsed = time() - start_time
                c = counter.get_value()
                dps = 0
                remain = 0
                if elapsed > 0:
                    dps = (c - prev_count) / elapsed
                    prev_count = c
                    start_time = time()
                    remain = (doc_count - c) / dps

                info("Indexed %s of %s documents (%s dps, %ss remain)"
                    % (counter.add_value(len(docs)), doc_count, int(dps), int(remain)))

            except Empty:
                pass

    for i in range(num_threads):
        info("Starting thread: %s" % i)
        worker = Thread(target=_write_worker)
        worker.setDaemon(True)
        worker.start()

    docs = helpers.scan(es,
            query={"query" : {"match_all" : {}}},
            index=index_from,
            scroll='10m')

    chunk = []
    for doc in docs:
        chunk.append(doc)
        if len(chunk)>=chunk_size:
            q.put(chunk)
            chunk = []

    if len(chunk)>0:
        q.put(chunk)
        chunk = []

    q.join()
    info("Waiting for queue to drain, current size is: "+str(q.qsize()))
    mutex.set_value(1)


