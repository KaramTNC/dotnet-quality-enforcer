from .generic import GenericAdapter
from .specs import PYTHON_SPEC


class PythonAdapter(GenericAdapter):
    def __init__(self) -> None:
        super().__init__(PYTHON_SPEC)
