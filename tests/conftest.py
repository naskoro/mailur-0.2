from unittest.mock import patch

from pytest import fixture

from core import Env


@fixture
def env():
    '''Test Environment'''
    with patch.object(Env, 'db_connect'):
        return Env('test', conf={
            'pg_username': 'postgres',
            'pg_password': '',
            'google_id': '',
            'google_secret': '',
            'cookie_secret': 'secret',
            'path_attachments': '/tmp/attachments',
            'search_lang': ['simple']
        })
