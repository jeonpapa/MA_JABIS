from agents.notify.gmail_delivery import send_gmail_message


class _FakeSend:
    def __init__(self): self.sent = None
    def users(self): return self
    def messages(self): return self
    def send(self, userId, body): self.sent = (userId, body); return self
    def execute(self): return {"id": "msg123", "threadId": "thr123", "labelIds": ["SENT"]}


def test_send_gmail_message_sends_and_returns_ids():
    svc = _FakeSend()
    out = send_gmail_message(
        recipients=["a@x.com"], subject="Hi", body_html="<b>hi</b>", service=svc,
    )
    assert out["ok"] is True
    assert out["mode"] == "gmail_send"
    assert out["sent"] is True
    assert out["gmail_message_id"] == "msg123"
    assert svc.sent[0] == "me"
