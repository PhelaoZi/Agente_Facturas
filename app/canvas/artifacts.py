"""Modelo de artefactos del lienzo y recolector usado por el agente."""
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

ARTIFACT_TYPES = {"kpi", "grafico", "tabla", "informe"}


@dataclass
class Artifact:
    tipo: str
    titulo: str
    payload: dict[str, Any]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    creado_en: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )

    def __post_init__(self) -> None:
        if self.tipo not in ARTIFACT_TYPES:
            raise ValueError(f"tipo de artefacto inválido: {self.tipo}")
        if not self.titulo:
            raise ValueError("el artefacto requiere un título")


class Collector:
    """Acumula los artefactos que el agente publica durante una consulta."""

    def __init__(self) -> None:
        self._items: list[Artifact] = []

    def add(self, art: Artifact) -> None:
        self._items.append(art)

    @property
    def items(self) -> list[Artifact]:
        return list(self._items)


def merge_artifacts(canvas: list[Artifact], nuevos: list[Artifact]) -> list[Artifact]:
    """Anexa artefactos nuevos al lienzo evitando duplicar por id."""
    existentes = {a.id for a in canvas}
    return canvas + [a for a in nuevos if a.id not in existentes]
