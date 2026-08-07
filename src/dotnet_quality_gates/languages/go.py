from .generic import GenericAdapter
from .specs import GO_SPEC


class GoAdapter(GenericAdapter):
    def __init__(self) -> None:
        super().__init__(GO_SPEC)
