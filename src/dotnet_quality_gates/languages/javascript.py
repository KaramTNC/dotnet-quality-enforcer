from .generic import GenericAdapter
from .specs import JAVASCRIPT_SPEC


class JavaScriptAdapter(GenericAdapter):
    def __init__(self) -> None:
        super().__init__(JAVASCRIPT_SPEC)
