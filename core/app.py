import datetime as dt
from urllib.parse import urlsplit, parse_qs, urlencode

import rapidjson as json
from valideer import ValidationError
from werkzeug.exceptions import HTTPException, abort
from werkzeug.utils import cached_property, redirect
from werkzeug.wrappers import Request as _Request, Response

from . import Env, log


class Request(_Request):
    @cached_property
    def json(self):
        return json.loads(self.data.decode())


def create_app(views):
    env = WebEnv(views)

    app = env.wsgi

    if env('debug'):
        from werkzeug.wsgi import SharedDataMiddleware

        app = SharedDataMiddleware(app, {
            '/attachments': env('path_attachments'),
            '/theme': env('path_theme'),
        })
    return app


class WebEnv(Env):
    Response = Response

    def __init__(self, views):
        super().__init__()
        self.views = views
        self.url_map = views.url_map

    def set_request(self, request):
        self.request = request
        self.adapter = self.url_map.bind_to_environ(request.environ)
        self.username = self.session.get('username')

    def process_response(self):
        endpoint, values = self.adapter.match()
        response = getattr(self.views, endpoint)(self, **values)
        if isinstance(response, Response):
            return response
        return self.to_json(response)

    @Request.application
    def wsgi(self, request):
        try:
            self.set_request(request)
            response = self.process_response()
        except Exception as e:
            if isinstance(e, HTTPException):
                status = '%s %s' % (e.code, e.description)
            elif isinstance(e, ValidationError):
                status = '400 %s' % e.msg
            else:
                log.exception(e)
                status = '500 %s' % e
            response = self.make_response(status=status)
        finally:
            if self.valid_username or self.valid_token:
                self.db.rollback()
        self.session.save_cookie(response, max_age=dt.timedelta(days=7))
        return response

    def url_for(self, endpoint, values=None, **kw):
        return self.adapter.build(endpoint, values, **kw)

    def url(self, url, params=None):
        if not params:
            return url

        url = urlsplit(url)
        query = parse_qs(url.query)
        query.update((k, str(v)) for k, v in params.items())
        url = url._replace(query=urlencode(query))
        return url.geturl()

    def redirect(self, location, code=302):
        return redirect(location, code)

    def redirect_for(self, endpoint, values=None, code=302):
        return redirect(self.url_for(endpoint, values), code=code)

    def abort(self, code, *a, **kw):
        abort(code, *a, **kw)

    def make_response(self, response=None, **kw):
        kw.setdefault('content_type', 'text/html')
        return self.Response(response, **kw)

    def to_json(self, response, **kw):
        kw.setdefault('content_type', 'application/json')
        r = json.dumps(response, ensure_ascii=False, default=str)
        return self.Response(r, **kw)
