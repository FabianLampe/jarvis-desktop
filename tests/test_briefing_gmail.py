from jarvis_core.briefing.gmail import recent_unread


class FakeMessages:
    def list(self, userId, labelIds, maxResults):
        self._ids = {"messages": [{"id": "1"}, {"id": "2"}]}
        return self

    def get(self, userId, id, format, metadataHeaders):
        self._id = id
        return self

    def execute(self):
        if hasattr(self, "_ids") and not hasattr(self, "_id"):
            r = self._ids
            del self._ids
            return r
        hdr = {"1": [{"name": "From", "value": "Alice <a@x.de>"},
                     {"name": "Subject", "value": "Hallo"}],
               "2": [{"name": "From", "value": "Bob <b@x.de>"},
                     {"name": "Subject", "value": "Test"}]}
        mid = self._id
        del self._id
        return {"payload": {"headers": hdr[mid]}, "snippet": "Kurztext " + mid}


class FakeUsers:
    def messages(self):
        return FakeMessages()


class FakeService:
    def users(self):
        return FakeUsers()


def test_recent_unread_parses_messages():
    mails = recent_unread(FakeService(), max_results=5)
    assert len(mails) == 2
    assert mails[0]["sender"] == "Alice" and mails[0]["subject"] == "Hallo"
    assert mails[0]["snippet"].startswith("Kurztext")


def test_recent_unread_empty():
    class EmptyMessages(FakeMessages):
        def list(self, **k):
            self._ids = {"messages": []}
            return self

    class U:
        def messages(self):
            return EmptyMessages()

    class S:
        def users(self):
            return U()

    assert recent_unread(S()) == []
