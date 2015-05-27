import json
import os
from urllib.parse import urlencode

from werkzeug.contrib.securecookie import SecureCookie
from werkzeug.exceptions import HTTPException, abort
from werkzeug.utils import cached_property, redirect
from werkzeug.wrappers import Request as _Request, Response

from . import Env, views


class Request(_Request):
    @cached_property
    def json(self):
        return json.loads(self.data.decode())


def create_app(conf):
    @Request.application
    def app(request):
        env = WebEnv(conf, request)
        try:
            response = env.wsgi()
        except HTTPException as e:
            response = e
        env.session.save_cookie(response)
        return response
    return app


class WebEnv(Env):
    def __init__(self, conf, request):
        super().__init__(conf)

        self.url_map = views.url_map
        self.request = request
        self.adapter = self.url_map.bind_to_environ(request.environ)

    def wsgi(self):
        endpoint, values = self.adapter.match()
        response = getattr(views, endpoint)(self, **values)
        if isinstance(response, str):
            return self.make_response(response)
        return response

    def url_for(self, endpoint, _args=None, _external=False, **values):
        url = self.adapter.build(endpoint, values, force_external=_external)
        url = '%s?%s' % (url, urlencode(_args)) if _args else url
        return url

    def redirect(self, location, code=302):
        return redirect(location, code)

    def redirect_for(self, endpoint, _code=302, **values):
        return redirect(self.url_for(endpoint, **values), code=_code)

    def abort(self, code, *a, **kw):
        abort(code, *a, **kw)

    @cached_property
    def session(self):
        secret_key = self('cookie_secret').encode()
        return SecureCookie.load_cookie(self.request, secret_key=secret_key)

    def login(self):
        self.session['logined'] = True

    @property
    def is_logined(self):
        return self.session.get('logined')

    def make_response(self, response, **kw):
        kw.setdefault('content_type', 'text/html')
        return Response(response, **kw)

    def to_json(self, response, **kw):
        kw.setdefault('content_type', 'application/json')
        r = json.dumps(response, ensure_ascii=False, default=str, indent=2)
        return Response(r, **kw)

    def render(self, name, ctx):
        from Stache import Stache

        stache = Stache()
        with open(os.path.join(self('path_theme'), '%s.mustache' % name)) as f:
            stache.add_template(name, f.read())

        return stache.render_template(name, ctx)

    def render_body(self, name, ctx):
        body = self.render(name, ctx)
        return self.render('base', {'body': body})
