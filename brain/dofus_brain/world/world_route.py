"""World-graph routing from the same explicit transition data AstraBot uses.

The graph records *how* one map leads to the next: the transition cell, its
kind, any interactive skill and its intermediate transition-map id.  It is not
safe to replace this with coordinate adjacency: interiors, doors, zaaps and
multiple map zones make that guess wrong.

The AstraBot development checkout is a convenient default source when its Git
LFS data is present.  A pointer file is deliberately reported as unavailable;
callers can show that honestly and keep navigation disabled.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any


class WorldGraphUnavailable(RuntimeError):
    """Raised when no real exported world graph is available locally."""


@dataclass(frozen=True)
class RouteTransition:
    from_map_id: int
    from_zone_id: int
    to_map_id: int
    to_zone_id: int
    type: int
    direction: int
    skill_id: int
    criterion: str
    transition_map_id: int
    cell_id: int
    id: int

    def to_json(self) -> dict[str, int | str]:
        return {
            "from_map_id": self.from_map_id, "from_zone_id": self.from_zone_id,
            "to_map_id": self.to_map_id, "to_zone_id": self.to_zone_id,
            "type": self.type, "direction": self.direction, "skill_id": self.skill_id,
            "criterion": self.criterion, "transition_map_id": self.transition_map_id,
            "cell_id": self.cell_id, "id": self.id,
        }


class WorldRouteProvider:
    """Read explicit world transitions and find a minimum-map-hop route."""

    def __init__(self, path: str | Path | None = None):
        default = "/private/tmp/AstraBot/DBDofusUnity/datas/bundles/standalone/world-graph.json"
        self.path = Path(path or os.environ.get("DOFUS_WORLD_GRAPH") or default).expanduser()
        self._edges: dict[tuple[int, int], list[RouteTransition]] | None = None
        self._maps: dict[int, set[tuple[int, int]]] | None = None
        self._error: str | None = None

    def status(self) -> dict[str, Any]:
        try:
            self._load()
        except WorldGraphUnavailable as exc:
            return {"available": False, "source": str(self.path), "reason": str(exc)}
        return {"available": True, "source": str(self.path), "vertices": sum(len(v) for v in self._maps.values())}

    def route(self, source_map_id: int, target_map_id: int, source_zone_id: int | None = None) -> dict[str, Any]:
        self._load()
        assert self._edges is not None and self._maps is not None
        source_map_id, target_map_id = int(source_map_id), int(target_map_id)
        starts = self._vertices_for(source_map_id, source_zone_id)
        targets = self._maps.get(target_map_id, set())
        if not starts:
            raise ValueError(f"world graph has no vertex for source map {source_map_id}")
        if not targets:
            raise ValueError(f"world graph has no vertex for target map {target_map_id}")
        if set(starts) & targets:
            return {"source_map_id": source_map_id, "target_map_id": target_map_id, "steps": [], "hops": 0}

        queue: deque[tuple[int, int]] = deque(starts)
        previous: dict[tuple[int, int], tuple[tuple[int, int], RouteTransition] | None] = {vertex: None for vertex in starts}
        end: tuple[int, int] | None = None
        while queue:
            vertex = queue.popleft()
            for transition in self._edges.get(vertex, ()):
                next_vertex = (transition.to_map_id, transition.to_zone_id)
                if next_vertex in previous:
                    continue
                previous[next_vertex] = (vertex, transition)
                if next_vertex in targets:
                    end = next_vertex
                    queue.clear()
                    break
                queue.append(next_vertex)
        if end is None:
            raise ValueError(f"no route from map {source_map_id} to map {target_map_id}")
        steps: list[RouteTransition] = []
        while previous[end] is not None:
            parent, transition = previous[end]  # type: ignore[misc]
            steps.append(transition)
            end = parent
        steps.reverse()
        return {"source_map_id": source_map_id, "target_map_id": target_map_id,
                "steps": [step.to_json() for step in steps], "hops": len(steps)}

    def exit_cells(self, map_id: int) -> frozenset[int]:
        """Cells reserved for graph transitions, never harvest/use targets."""
        self._load()
        assert self._edges is not None
        return frozenset(
            step.cell_id for vertex, steps in self._edges.items() if vertex[0] == int(map_id)
            for step in steps if step.cell_id >= 0
        )

    def _vertices_for(self, map_id: int, zone_id: int | None) -> list[tuple[int, int]]:
        assert self._maps is not None
        vertices = self._maps.get(map_id, set())
        if zone_id is not None:
            vertex = (map_id, int(zone_id))
            return [vertex] if vertex in vertices else []
        return sorted(vertices)

    def _load(self) -> None:
        if self._edges is not None:
            return
        if self._error is not None:
            raise WorldGraphUnavailable(self._error)
        if not self.path.is_file():
            self._error = f"world graph is not installed: {self.path}"
            raise WorldGraphUnavailable(self._error)
        try:
            raw_bytes = self.path.read_bytes()
        except OSError as exc:
            self._error = f"could not read world graph: {exc}"
            raise WorldGraphUnavailable(self._error) from exc
        if raw_bytes.startswith(b"version https://git-lfs.github.com/spec/"):
            self._error = "world graph is a Git-LFS pointer, not the exported graph data"
            raise WorldGraphUnavailable(self._error)
        try:
            raw = json.loads(raw_bytes)
            vertices = raw["m_vertices"]
            outgoing = raw["m_outgoingEdges"]
        except (ValueError, KeyError, TypeError) as exc:
            self._error = f"invalid world graph JSON: {exc}"
            raise WorldGraphUnavailable(self._error) from exc
        by_uid: dict[int, tuple[int, int]] = {}
        maps: dict[int, set[tuple[int, int]]] = {}
        for map_key, zones in vertices.items():
            for zone_key, vertex in zones.items():
                map_id = int(vertex.get("m_mapId", map_key))
                zone_id = int(vertex.get("m_zoneId", zone_key))
                by_uid[int(vertex["m_uid"])] = (map_id, zone_id)
                maps.setdefault(map_id, set()).add((map_id, zone_id))
        edges: dict[tuple[int, int], list[RouteTransition]] = {}
        for uid_key, outgoing_edge in outgoing.items():
            source = by_uid.get(int(uid_key))
            if source is None:
                continue
            for edge in outgoing_edge.get("m_edgeList", ()):
                target_data = edge.get("m_to", {})
                target = by_uid.get(int(target_data.get("m_uid", -1)))
                if target is None:
                    target = (int(target_data["m_mapId"]), int(target_data["m_zoneId"]))
                for item in edge.get("m_transitions", ()):
                    transition = RouteTransition(
                        source[0], source[1], target[0], target[1],
                        int(item.get("m_type", 0)), int(item.get("m_direction", 0)),
                        int(item.get("m_skillId", 0)), str(item.get("m_criterion", "")),
                        int(item.get("m_transitionMapId", 0)), int(item.get("m_cellId", -1)),
                        int(item.get("m_id", 0)),
                    )
                    edges.setdefault(source, []).append(transition)
        for values in edges.values():
            values.sort(key=lambda step: (step.to_map_id, step.to_zone_id, step.type, step.id))
        self._edges, self._maps = edges, maps
