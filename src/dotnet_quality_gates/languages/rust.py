from .generic import GenericAdapter
from .specs import RUST_SPEC


class RustAdapter(GenericAdapter):
    def __init__(self) -> None:
        super().__init__(RUST_SPEC)
