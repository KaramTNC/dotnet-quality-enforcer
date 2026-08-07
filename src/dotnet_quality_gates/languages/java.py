from .generic import GenericAdapter
from .specs import JAVA_SPEC


class JavaAdapter(GenericAdapter):
    def __init__(self) -> None:
        super().__init__(JAVA_SPEC)
