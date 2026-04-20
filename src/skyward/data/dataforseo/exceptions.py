class IncompleteTaskError(RuntimeError):
    def __init__(self, message, *, task_ids):
        super().__init__(message)
        self.task_ids = list(task_ids)
