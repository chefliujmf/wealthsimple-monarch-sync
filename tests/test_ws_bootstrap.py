from wsmsync.ws_bootstrap import find_app_js, find_client_id

# Shape of the login page as served 2026-11-03: attributes split across lines.
LOGIN_HTML_2026_11 = """<html><head>
    <script src="https://my.wealthsimple.com/p.js"></script>
    <script
      src="https://assets.wealthsimple.com/app-82a898fbd5d49661.js"
      nonce="abc" defer
    ></script>
</head></html>"""

LOGIN_HTML_OLD = '<script defer src="https://assets.wealthsimple.com/app-0123abcd.js"></script>'


def test_app_js_found_when_tag_spans_lines():
    assert find_app_js(LOGIN_HTML_2026_11) == "https://assets.wealthsimple.com/app-82a898fbd5d49661.js"


def test_app_js_found_on_single_line():
    assert find_app_js(LOGIN_HTML_OLD) == "https://assets.wealthsimple.com/app-0123abcd.js"


def test_app_js_ignores_other_scripts():
    assert find_app_js('<script src="https://my.wealthsimple.com/p.js"></script>') is None


def test_client_id_from_bundle():
    js = 'x={"development":{clientId:"aaaa"},"production":{apiUrl:"u",clientId:"4da53ac2b0"}}'
    assert find_client_id(js) == "4da53ac2b0"
