from elasticsearch import Elasticsearch, client, helpers
from functools import partial
import itertools
import logging
from math import ceil
import os
import shutil
import tempfile
from time import gmtime, strftime
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

def update(es, delete_old_index, close_old_index, config):
    """ Updates all of the stuff
    """

    for index in config.get('indexes', []):
        index_name = dated_name(index.name)

        info("Creating index: %s as %s (%s)" % (index.name, index_name, index.alias))
        es.indices.create(index=index_name)
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

        old_bulk = es.bulk
        try:
            query       = {"query" : {"match_all" : {}}}
            chunk_size  = float(index.get('chunk_size', 500))
            doc_count   = int(es.count(index=index.alias)['count'])
            chunks      = int(ceil(float(doc_count) / chunk_size))
            chunk       = itertools.count()

            def _bulk(self, *args, **kwargs):
                chunks = kwargs.pop('__chunks')
                chunk = kwargs.pop('__chunk')
                info("Reindexed chunk %s of %s" % (chunk.next()+1, chunks))
                return old_bulk(self, *args, **kwargs)
            es.bulk = partial(_bulk, __chunks=chunks, __chunk=chunk)

            info("Reindexing %s documents in chunks of %s from %s:%s to %s"
                % (doc_count, int(chunk_size), index.alias, mapping.doc_type, index_name))
            response = helpers.reindex(es, index.alias, index_name,
                query=query,
                chunk_size=chunk_size,
                scroll=index.get('scroll', '10m'))
        finally:
            es.bulk = old_bulk

        old_index = None
        if es.indices.exists_alias(index='_all', name=index.alias):
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
                % (index.alias, index_name))

def create(es, config):
    """ Creates all of the stuff
    """
    for index in config.get('indexes', []):
        index_name = dated_name(index.name)

        info("Creating index: %s as %s (%s)" % (index.name, index_name, index.alias))
        es.indices.create(index=index_name)
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

