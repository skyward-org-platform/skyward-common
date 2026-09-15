class IncompleteTaskError(RuntimeError):
    def __init__(self, message, *, task_ids):
        super().__init__(message)
        self.task_ids = list(task_ids)


class InsufficientBalanceError(RuntimeError):
    """Raised before (or during) a run when the DataForSEO balance is too low.

    Carries what was finished so a consumer can rerun only the remainder.
    """

    def __init__(self, message, *, job_id, endpoint, balance, required,
                 upload_ids=(), completed_targets=(), remaining_targets=()):
        super().__init__(message)
        self.job_id = job_id
        self.endpoint = endpoint
        self.balance = balance
        self.required = required
        self.upload_ids = list(upload_ids)
        self.completed_targets = list(completed_targets)
        self.remaining_targets = list(remaining_targets)


class InvalidLocationError(ValueError):
    """Raised before a run when location_code isn't supported by the endpoint's DFS list."""

    def __init__(self, message, *, location_code, endpoint, supported_by):
        super().__init__(message)
        self.location_code = location_code
        self.endpoint = endpoint
        self.supported_by = supported_by
