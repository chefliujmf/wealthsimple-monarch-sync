import pytest

from wsmsync.config import load


def _write(tmp_path, body):
    p = tmp_path / "c.toml"
    p.write_text(body)
    return p


ACCT = '''
  [[profile.account]]
  ws_account_id = "ca-cash-msb-AAAA1111"
  monarch_account_id = "1"
'''


def test_same_ws_account_in_two_profiles_is_refused(tmp_path):
    body = ('[[profile]]\nname="me"\nws_username="a"\nstart_date="2026-10-01"\n' + ACCT +
            '[[profile]]\nname="partner"\nws_username="b"\nstart_date="2026-10-01"\n' + ACCT)
    with pytest.raises(ValueError, match="both profile"):
        load(_write(tmp_path, body))


def test_monarch_profile_reuse(tmp_path):
    body = '[[profile]]\nname="partner"\nws_username="b"\nstart_date="2026-10-01"\nmonarch_profile="me"\n'
    assert load(_write(tmp_path, body)).profiles[0].monarch_profile == "me"
