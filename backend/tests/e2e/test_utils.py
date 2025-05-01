import json

class DummyMsg:
    def __init__(self, content: str):
        self._content = content

    def get_json(self):
        return json.loads(self._content)
        
    def get_body(self) -> bytes:
        """Mimics azure.functions.QueueMessage.get_body"""
        return self._content.encode('utf-8') 