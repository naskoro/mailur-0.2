import datetime as dt
import functools as ft
import json
import os
import re

import valideer as v
from mistune import markdown
from werkzeug.routing import Map, Rule

from . import parser, syncer, gmail, filters as f

rules = [
    Rule('/auth/', endpoint='auth'),
    Rule('/auth-callback/', endpoint='auth_callback'),

    Rule('/', endpoint='index'),
    Rule('/init/', endpoint='init'),
    Rule('/raw/<id>/', endpoint='raw'),
    Rule('/body/<id>/', endpoint='body'),
    Rule('/thread/<id>/', endpoint='thread'),
    Rule('/emails/', endpoint='emails'),
    Rule('/search/<q>/', endpoint='search'),
    Rule('/mark/', endpoint='mark'),
    Rule('/compose/', endpoint='compose'),
    Rule('/preview/', endpoint='preview'),
    Rule('/search-email/', endpoint='search_email')
]
url_map = Map(rules)


def auth(env):
    redirect_uri = env.url_for('auth_callback', _external=True)
    return env.redirect(gmail.auth_url(env, redirect_uri))


def auth_callback(env):
    redirect_uri = env.url_for('auth_callback', _external=True)
    try:
        info = gmail.auth_callback(env, redirect_uri, env.request.args['code'])
        env.login(info['email'])
        return env.redirect_for('index')
    except gmail.AuthError as e:
        return str(e)


def login_required(func):
    def inner(env, *a, **kw):
        if not (env.is_logined or env('ui_is_public')):
            return env.redirect_for('auth')
        return func(env, *a, **kw)

    return ft.wraps(func)(inner)


def adapt_page():
    def inner(env, *a, **kw):
        schema = v.parse({
            'page': v.Nullable(v.AdaptTo(int), 1),
            'last': v.Nullable(v.AdaptTo(float))
        })
        data = schema.validate(env.request.args)
        page, last = data['page'], data.get('last')
        page = {
            'limit': env('ui_per_page'),
            'offset': env('ui_per_page') * (page - 1),
            'last': last,
            'last_dt': dt.datetime.fromtimestamp(last) if last else None,
            'count': env('ui_per_page') * page,
            'current': page,
            'next': page + 1,
        }
        return wrapper.func(env, page, *a, **kw)

    def wrapper(func):
        wrapper.func = func
        return ft.wraps(func)(inner)
    return wrapper


def adapt_fmt(tpl):
    def inner(env, *a, **kw):
        default = 'body' if env.request.is_xhr else 'html'
        fmt = env.request.args.get('fmt', default)

        ctx = wrapper.func(env, *a, **kw)
        if fmt == 'json':
            return env.to_json(ctx)
        elif fmt == 'body':
            return env.render(tpl, ctx)
        return env.render_body(tpl, ctx)

    def wrapper(func):
        wrapper.func = func
        return ft.wraps(func)(inner)
    return wrapper


@login_required
@adapt_fmt('index')
def index(env):
    return {'labels?': ctx_all_labels(env)}


def init(env):
    schema = v.parse({'+offset': v.Range(v.AdaptTo(int), min_value=0)})
    args = schema.validate(env.request.args)
    env.session['tz_offset'] = args['offset']
    return 'OK'


def ctx_emails(env, items, domid='id'):
    emails, last = [], None
    for i in items:
        last = i['updated'] if not last or i['updated'] > last else last
        extra = i.get('_extra', {})
        fr = f.get_addr(i['fr'][0])
        email = dict({
            'id': i['id'],
            'thrid': i['thrid'],
            'domid': i[domid],
            'subj': i['subj'],
            'subj_human': f.humanize_subj(i['subj']),
            'subj_url': env.url_for('emails', {'subj': i['subj']}),
            'preview': f.get_preview(i['text']),
            'pinned?': '\\Starred' in i['labels'],
            'unread?': '\\Unread' in i['labels'],
            'body_url': env.url_for('body', id=i['id']),
            'raw_url': env.url_for('raw', id=i['id']),
            'thread_url': env.url_for('thread', id=i['thrid']),
            'reply_url': env.url_for('compose', {'id': i['id']}),
            'replyall_url': env.url_for('compose', {'id': i['id'], 'all': 1}),
            'time': f.format_dt(env, i['time']),
            'time_human': f.humanize_dt(env, i['time']),
            'time_stamp': i['time'].timestamp(),
            'from': i['fr'][0],
            'from_short': f.format_addr(env, i['fr'][0]),
            'from_url': env.url_for('emails', {'person': fr}),
            'gravatar': f.get_gravatar(fr),
            'labels?': ctx_labels(env, i['labels'])
        }, **extra)
        email['hash'] = f.get_hash(email)
        emails.append(email)

    emails = bool(emails) and {
        'items': emails,
        'length': len(emails),
        'last': last.timestamp()
    }
    return {
        'emails?': emails,
        'emails_class': 'emails-byid' if domid == 'id' else ''
    }


def ctx_labels(env, labels, ignore=None):
    if not labels:
        return False
    ignore = ignore or []
    pattern = re.compile('(%s)' % '|'.join(
        [r'(?:\\\\)*(?![\\]).*'] +
        [re.escape(i) for i in ('\\Inbox', '\\Junk', '\\Trash')]
    ))
    labels = [
        l for l in sorted(set(labels))
        if l not in ignore and pattern.match(l)
    ]
    items = [
        {'name': l, 'url': env.url_for('emails', {'in': l})}
        for l in labels
    ]
    return {
        'items': items,
        'items_json': json.dumps(items),
        'names': labels,
        'names_json': json.dumps(labels)
    }


def ctx_all_labels(env):
    i = env.sql('SELECT DISTINCT unnest(labels) FROM emails;')
    items = sorted(r[0] for r in i.fetchall())
    return ctx_labels(env, items)


def ctx_header(env, subj, labels=None):
    return {
        'subj': subj,
        'labels': {
            'items_json': json.dumps(list(labels or [])),
            'all_json': ctx_all_labels(env)['items_json'],
            'base_url': env.url_for('emails', {'in': ''})
        }
    }


def ctx_body(env, msg, msgs, show=False):
    return (show or '\\Unread' in msg['labels']) and {
        'text': f.humanize_html(msg['html'], msgs),
        'attachments?': bool(msg.get('attachments')) and {
            'items': [
                {'name': os.path.basename(a), 'url': '/attachments/%s' % a}
                for a in msg['attachments']
            ]
        }
    }


@login_required
@adapt_fmt('emails')
def thread(env, id):
    i = env.sql('''
    SELECT
        id, thrid, subj, labels, time, fr, text, updated,
        html, attachments
    FROM emails
    WHERE thrid = %s
    ORDER BY time
    ''', [id])
    msgs, labels = [], set()

    def emails():
        for n, msg in enumerate(i):
            msg = dict(msg)
            labels.update(msg['labels'])
            if n == 0:
                subj = msg['subj']
            msg['_extra'] = {
                'subj_changed?': f.is_subj_changed(msg['subj'], subj),
                'subj_human': f.humanize_subj(msg['subj'], subj),
                'body?': ctx_body(env, msg, (m['html'] for m in msgs[::-1]))
            }
            yield msg
            msgs.append(msg)

    ctx = ctx_emails(env, emails())
    if ctx['emails?']:
        emails = ctx['emails?']['items']
        subj = f.humanize_subj(emails[0]['subj'])

        last = emails[-1]
        parents = reversed([p['html'] for p in msgs[:-1]])
        last['body?'] = ctx_body(env, msgs[-1], parents, show=True)

        ctx['header?'] = ctx_header(env, subj, labels)
        ctx['emails_class'] = ctx['emails_class'] + ' thread'
    return ctx


@login_required
@adapt_fmt('emails')
@adapt_page()
def emails(env, page):
    schema = v.parse({
        'person': str,
        'subj': str,
        'in': str
    })
    args = schema.validate(env.request.args)
    if args.get('in'):
        l = args['in']
        subj = l
        l = [l] if l in ['\\Trash', '\\Junk'] else [l, '\\All']
        where = env.mogrify('%s::varchar[] <@ labels', [l])
    elif args.get('subj'):
        subj = 'Filter by subj %r' % args['subj']
        where = env.mogrify('%s = subj', [args['subj']])
    elif args.get('person'):
        subj = 'Filter by person %r' % args['person']
        where = env.mogrify(
            '(fr[1] LIKE %(fr)s OR "to"[1] LIKE %(fr)s)',
            {'fr': '%<{}>'.format(args['person'])}
        )
    else:
        return env.abort(400)

    if page['last']:
        where = env.mogrify(where + ' AND time < %s', [page['last_dt']])

    i = env.sql('''
    WITH
    thread_ids AS (
        SELECT thrid, max(time)
        FROM emails
        WHERE {where}
        GROUP BY thrid
        ORDER BY 2 DESC
        LIMIT {page[limit]} OFFSET {page[offset]}
    ),
    threads  AS (
        SELECT
            t.thrid,
            json_agg(e.labels) AS labels,
            array_agg(id) AS id_list,
            count(id) AS count,
            json_object_agg(e.time, e.subj) AS subj_list
        FROM thread_ids t
        JOIN emails e ON e.thrid = t.thrid
        GROUP BY t.thrid
    )
    SELECT
        id, t.thrid, subj, t.labels, time, fr, text, updated,
        count, subj_list
    FROM emails e
    JOIN threads t ON e.thrid = t.thrid
    WHERE id IN (
        SELECT id FROM emails
        WHERE id = ANY(t.id_list)
        ORDER BY time DESC LIMIT 1
    )
    ORDER BY time DESC
    '''.format(where=where, page=page))

    def emails():
        for msg in i:
            base_subj = dict(msg["subj_list"])
            base_subj = base_subj[sorted(base_subj)[0]]
            msg = dict(msg, **{
                'labels': list(set(sum(msg['labels'], [])) - {args.get('in')}),
                '_extra': {
                    'count': msg['count'] > 1 and msg['count'],
                    'subj_human': f.humanize_subj(msg['subj'], base_subj)
                }
            })
            yield msg

    sql = 'SELECT count(distinct thrid) FROM emails WHERE %s' % where
    count = env.sql(sql).fetchone()[0]

    ctx = ctx_emails(env, emails(), domid='thrid')
    ctx['count'] = count
    if page['count'] < count:
        ctx['next?'] = {'url': env.url(env.request.path, dict(
            env.request.args.to_dict(),
            last=page['last'] or ctx['emails?']['items'][0]['time_stamp'],
            page=page['next']
        ))}
    ctx['header?'] = ctx_header(env, subj)
    return ctx


@login_required
@adapt_fmt('emails')
def search(env, q):
    i = env.sql('''
    WITH search AS (
        SELECT id
        FROM emails_search
        WHERE document @@ plainto_tsquery('simple', %(query)s)
        ORDER BY ts_rank(document, plainto_tsquery('simple', %(query)s)) DESC
        LIMIT 100
    )
    SELECT
        e.id, thrid, subj, labels, time, fr, text, updated,
        html, attachments
    FROM emails e, search s
    WHERE e.id = s.id
    ''', {'query': q})

    return ctx_emails(env, i)


@login_required
@adapt_fmt('emails')
def body(env, id):
    def parse(raw, id):
        return parser.parse(raw.tobytes(), id, env('path_attachments'))

    row = env.sql('''
    SELECT
        id, thrid, subj, labels, time, fr, text, updated,
        raw, attachments
    FROM emails WHERE id=%s LIMIT 1
    ''', [id]).fetchone()
    if row:
        i = env.sql('''
        SELECT id, raw FROM emails
        WHERE thrid=%s AND id!=%s AND time<%s
        ORDER BY time DESC
        ''', [row['thrid'], id, row['time']])

        def emails():
            for msg in [row]:
                if msg['raw']:
                    parsed = parse(msg['raw'], msg['id'])
                    msg = dict(msg)
                    msg['html'] = parsed['html']
                    msg['text'] = parsed['text']
                    msg['attachments'] = parsed['attachments']
                    msg['embedded'] = parsed['embedded']
                    msgs = [parse(p['raw'], p['id'])['html'] for p in i]
                    msg['_extra'] = {
                        'body?': ctx_body(env, msg, msgs, show=True),
                    }
                yield msg

        return ctx_emails(env, emails())

    env.abort(404)


@login_required
def raw(env, id):
    from tests import open_file

    i = env.sql('SELECT raw, header FROM emails WHERE id=%s LIMIT 1', [id])
    row = i.fetchone()
    raw = row[0] or row[1]
    if env('debug') and env.request.args.get('save'):
        name = '%s--test.txt' % id
        with open_file('files_parser', name, mode='bw') as f:
            f.write(raw)
    return env.make_response(raw, content_type='text/plain')


@login_required
def mark(env):
    syncer.mark(env, env.request.json, new=True)
    return 'OK'


@login_required
def compose(env):
    schema = v.parse({'id': str, 'all': v.Nullable(v.AdaptTo(bool), False)})
    args = schema.validate(env.request.args)
    ctx, parent = {}, {}
    if args.get('id'):
        parent = env.sql('''
        SELECT thrid, msgid, "to", fr, cc, bcc, subj, reply_to
        FROM emails WHERE id=%s LIMIT 1
        ''', [args['id']]).fetchone()
        if f.get_addr(parent['fr'][0]) == env.session['email']:
            to = parent['to']
        else:
            to = parent['reply_to'] or parent['fr']
        if args.get('all'):
            to += parent['cc'] or []
        ctx.update({
            'to': ', '.join(to),
            'subj': 'Re: %s' % f.humanize_subj(parent['subj'], empty=''),
        })

    if env.request.method == 'POST':
        from email.utils import parseaddr
        import dns.resolver
        import dns.exception

        class Email(v.Validator):
            def validate(self, value, adapt=True):
                addr = parseaddr(value)[1]
                hostname = addr[addr.find('@') + 1:]
                try:
                    dns.resolver.query(hostname, 'MX')
                except dns.exception.DNSException:
                    raise v.ValidationError('No MX record for %s' % hostname)
                return value

        schema = v.parse({
            '+to': v.ChainOf(
                v.AdaptBy(lambda v: [i.strip() for i in v.split(',')]),
                [Email]
            ),
            '+subj': str,
            '+body': str
        })
        msg = schema.validate(env.request.form)
        msg['in_reply_to'] = parent.get('msgid')
        msg['files'] = env.request.files.getlist('files')
        sendmail(env, env.session['email'], msg)
        if parent.get('thrid'):
            return env.redirect_for('thread', id=parent['thrid'])
        return env.redirect_for('emails', {'in': '\\Sent'})
    return env.render_body('compose', ctx)


def sendmail(env, fr, msg):
    from email.encoders import encode_base64
    from email.mime.base import MIMEBase
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.utils import formatdate, formataddr, getaddresses

    in_reply_to, files = msg.get('in_reply_to'), msg.get('files', [])

    text = MIMEMultipart('alternative')
    text.attach(MIMEText(msg['body'], 'plain'))
    text.attach(MIMEText(markdown(msg['body']), 'html'))
    email = text

    files = [i for i in files if i.filename]
    if files:
        email = MIMEMultipart()
        email.attach(text)
    for i in files:
        a = MIMEBase(*i.mimetype.split('/'))
        a.set_payload(i.stream.read())
        a.add_header('Content-Disposition', 'attachment', filename=i.filename)
        encode_base64(a)
        email.attach(a)

    email['From'] = fr
    email['To'] = ', '.join(formataddr(a) for a in getaddresses(msg['to']))
    email['Date'] = formatdate()
    email['Subject'] = msg['subj']

    if in_reply_to:
        email['In-Reply-To'] = in_reply_to
        email['References'] = in_reply_to

    _, sendmail = gmail.smtp_connect(env, fr)
    sendmail(fr, msg['to'], email.as_string())


@login_required
def preview(env):
    schema = v.parse({'+body': str})
    body = schema.validate(env.request.form)['body']
    return markdown(body)


@login_required
def search_email(env):
    schema = v.parse({'q': str})
    args = schema.validate(env.request.args)

    where = ''
    if args.get('q'):
        where += env.mogrify('addr LIKE %s', ['%{}%'.format(args['q'])])
    where = ('WHERE ' + where) if where else ''

    addresses = env.mogrify('''
    SELECT distinct unnest("to") AS addr, time
    FROM emails
    WHERE fr[1] LIKE %s
    ''', ['%<{}>'.format(env.session['email'])])

    i = env.sql('''
    WITH addresses AS ({addresses})
    SELECT addr, time FROM addresses
    {where} ORDER BY time DESC LIMIT 100
    '''.format(where=where, addresses=addresses))
    return env.to_json([
        {'text': v[0], 'value': v[0]} for v in i if len(v[0]) < 100
    ])
