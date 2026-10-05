class AshareError(Exception):
    def __init__(self, code: str, message: str, **context):
        super().__init__(message)
        self.code, self.context = code, context

    def result(self):
        return {"error": self.code, "message": str(self), "context": self.context}


class Unsupported(AshareError):
    def __init__(self, message: str, **context):
        super().__init__("UNSUPPORTED", message, **context)
