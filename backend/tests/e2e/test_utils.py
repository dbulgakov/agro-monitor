import json

class DummyMsg:
    def __init__(self, content: str):
        self._content = content

    def get_json(self):
        return json.loads(self._content) 