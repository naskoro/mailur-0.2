from collections import OrderedDict
from unittest.mock import patch

from pytest import mark, fixture

from . import read_file
from core import gmail, imap, imap_utf7


@fixture
@patch('imaplib.IMAP4_SSL')
def client(mok, env):
    '''IMAP client with some patches'''
    with patch.object(env, 'storage'):
        return gmail.imap_connect(env, 'test@pusto.org')


def gen_response(filename, query):
    import imaplib
    import pickle
    from conf import username, password
    from tests import open_file

    im = imaplib.IMAP4_SSL('imap.gmail.com')
    im.login(username, password)
    im.select('&BEIENQRBBEI-')

    ids = im.uid('search', None, 'all')[1][0].decode().split()
    res = im.uid('fetch', ','.join(ids), '(%s)' % query)
    with open_file(filename, mode='bw') as f:
        f.write(pickle.dumps((ids, res)))


def test_fetch_header_and_other(client):
    filename = 'files_imap/fetch-header-and-other.pickle'
    query = 'UID X-GM-MSGID FLAGS X-GM-LABELS RFC822.HEADER RFC822.HEADER'
    # gen_response(filename, query)

    ids, data = read_file(filename)

    client.uid = lambda *a, **kw: data
    rows = OrderedDict(imap.fetch(client, ids, query))
    assert len(ids) == len(rows)
    assert ids == list(str(k) for k in rows.keys())
    for id in ids:
        value = rows[id]
        for key in query.split():
            assert key in value


def test_fetch_body(client):
    filename = 'files_imap/fetch-header.pickle'
    query = 'RFC822.HEADER INTERNALDATE'
    # gen_response(filename, query)

    ids, data = read_file(filename)

    client.uid = lambda *a, **kw: data
    rows = OrderedDict(imap.fetch(client, ids, query))
    assert len(ids) == len(rows)
    assert ids == list(str(k) for k in rows.keys())


@mark.parametrize('query, line, expected', [
    ('FLAGS', [b'UID 1 FLAGS (\\Seen)'], {
        '1': {'FLAGS': ['\\Seen'], 'UID': 1}
    }),
    ('FLAGS', [b'UID 1 FLAGS (\\Seen))'], {
        '1': {'FLAGS': ['\\Seen'], 'UID': 1}
    }),
    ('FLAGS', [b'UID 1 FLAGS (\\FLAGS FLAGS))'], {
        '1': {'FLAGS': ['\\FLAGS', 'FLAGS'], 'UID': 1}
    }),
    ('FLAGS', [b'1 (FLAGS ("ABC\\"" UID) UID 1'], {
        '1': {'FLAGS': ['ABC"', 'UID'], 'UID': 1}
    }),
    ('FLAGS', [b'1 (FLAGS ("ABC \\\\\\"" UID) UID 1'], {
        '1': {'FLAGS': ['ABC \\"', 'UID'], 'UID': 1}
    }),
    ('FLAGS', [b'1 (FLAGS ("ABC \\")\\\\" UID) UID 1'], {
        '1': {'FLAGS': ['ABC ")\\', 'UID'], 'UID': 1}
    }),
    ('FLAGS', [b'1 (FLAGS (")ABC)\\"" UID) UID 1'], {
        '1': {'FLAGS': [')ABC)"', 'UID'], 'UID': 1}
    }),
    (
        ['FLAGS', 'BODY[HEADER.FIELDS (TO)]'],
        [(b'FLAGS (AB) UID 1 BODY[HEADER.FIELDS (TO)] {48}', b'1'), b')'],
        {'1': {'FLAGS': ['AB'], 'BODY[HEADER.FIELDS (TO)]': b'1', 'UID': 1}}
    ),
    (
        ['BODY[HEADER.FIELDS (MESSAGE-ID)]'],
        [(
            b'1 (UID 1 BODY[HEADER.FIELDS (MESSAGE-ID)] {84}',
            b'Message-ID: <123@mail.com>\r\n\r\n'
        ), b')'],
        {'1': {
            'BODY[HEADER.FIELDS (MESSAGE-ID)]': (
                b'Message-ID: <123@mail.com>\r\n\r\n'
            ),
            'UID': 1
        }}
    )
])
def test_lexer(client, query, line, expected):
    client.uid = lambda *a, **kw: ('OK', line)
    rows = imap.fetch(client, '1', query)
    assert dict(rows) == expected


def test_imap_utf7():
    orig, expect = '&BEIENQRBBEI-', 'тест'
    assert imap_utf7.decode(orig) == expect
    assert imap_utf7.encode(expect) == orig


def test_list(client):
    data = [
        b'(\\HasNoChildren) "/" "-job proposals"',
        b'(\\HasNoChildren) "/" "-social"',
        b'(\\HasNoChildren) "/" "FLAGS \\")\\\\"',
        b'(\\HasNoChildren) "/" "INBOX"',
        b'(\\HasNoChildren) "/" "UID"',
        b'(\\Noselect \\HasChildren) "/" "[Gmail]"',
        b'(\\HasNoChildren \\All) "/" "[Gmail]/All Mail"',
        b'(\\HasNoChildren \\Drafts) "/" "[Gmail]/Drafts"',
        b'(\\HasNoChildren \\Important) "/" "[Gmail]/Important"',
        b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"',
        b'(\\HasNoChildren \\Junk) "/" "[Gmail]/Spam"',
        b'(\\HasNoChildren \\Flagged) "/" "[Gmail]/Starred"',
        b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"',
        b'(\\HasNoChildren) "/" "work: 42cc"',
        b'(\\HasNoChildren) "/" "work: odesk"',
        b'(\\HasNoChildren) "/" "work: odeskps"',
        b'(\\HasNoChildren) "/" "&BEIENQRBBEI-"'
    ]

    client.list = lambda *a, **kw: ('OK', data)
    rows = imap.folders(client)
    assert rows[0] == (('\\HasNoChildren',), '/', '-job proposals')
    assert rows[3] == (('\\HasNoChildren',), '/', 'INBOX')
    assert rows[5] == (('\\Noselect', '\\HasChildren'), '/', '[Gmail]')
    assert rows[-1] == (('\\HasNoChildren',), '/', '&BEIENQRBBEI-')
