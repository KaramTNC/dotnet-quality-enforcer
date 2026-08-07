from .generic import GenericAdapter
from .specs import KOTLIN_SPEC


class KotlinAdapter(GenericAdapter):
    def __init__(self) -> None:
        super().__init__(KOTLIN_SPEC)
