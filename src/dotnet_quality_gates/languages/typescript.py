from .generic import GenericAdapter
from .specs import TYPESCRIPT_SPEC


class TypeScriptAdapter(GenericAdapter):
    def __init__(self) -> None:
        super().__init__(TYPESCRIPT_SPEC)
