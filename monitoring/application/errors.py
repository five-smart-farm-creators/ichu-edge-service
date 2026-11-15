"""Application errors raised by Monitoring use cases."""


class DeviceAuthenticationFailed(Exception):
    """Raised when a device is not authorized to submit telemetry."""


class CloudUnavailable(Exception):
    """Raised when the ICHU cloud API cannot be reached or fails."""


class CloudCredentialsRejected(Exception):
    """Raised when the ICHU cloud API rejects the edge gateway credentials."""


class CloudBatchRejected(Exception):
    """Raised when the ICHU cloud API refuses a whole batch as invalid."""


class SynchronizationDisabled(Exception):
    """Raised when cloud synchronization is not configured for this gateway."""
